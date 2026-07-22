import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any, Dict

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from core.api.app_demo_store import (
    DEMO_USER_ID,
    DEMO_TOKEN,
    append_dialogue,
    admin_metrics,
    bind_device,
    bound_device,
    clean_baize_text,
    consume_energy,
    check_in_spirit_power,
    create_device,
    delete_memory,
    generate_diary,
    get_settings,
    list_admin_conversations,
    list_admin_devices,
    list_devices,
    list_dialogues,
    list_diaries,
    list_energy_events,
    list_intimacy_events,
    list_memories,
    list_spirit_power_items,
    login_phone_user,
    ota_payload,
    register_phone_user,
    register_or_login_user,
    resolve_bound_app_device,
    rotate_device_code,
    unbind_device,
    update_device_name,
    update_user_password,
    upsert_memory,
    update_settings,
    user_for_token,
    user_summary,
    spirit_power_summary,
    use_spirit_dew,
)
from core.api.base_handler import BaseHandler
from core.providers.tools.device_mcp.mcp_handler import _refresh_device_status_report
from core.utils import llm as llm_utils
from core.utils.prompt_manager import PromptManager

TAG = __name__


class AppDemoHandler(BaseHandler):
    """User-facing App API for the Baize MVP."""

    def __init__(
        self,
        config: dict,
        llm_factory=None,
        device_registry=None,
        mcp_status_refresher=None,
        demo_runner=None,
    ):
        super().__init__(config)
        self.llm_factory = llm_factory or llm_utils.create_instance
        self._demo_llm = None
        self.device_registry = device_registry
        self.mcp_status_refresher = mcp_status_refresher or self._refresh_status_from_connection
        self.demo_runner = demo_runner or self._run_demo_on_connection
        self._event_subscribers = {}
        if self.device_registry is not None and hasattr(self.device_registry, "subscribe"):
            self.device_registry.subscribe(self._handle_registry_event)

    def routes(self):
        return [
            web.post("/api/app/register", self.handle_register),
            web.post("/api/app/login", self.handle_login),
            web.post("/api/app/demo-login", self.handle_demo_login),
            web.get("/api/app/me", self.handle_me),
            web.get("/api/app/spirit-power", self.handle_spirit_power),
            web.get("/api/app/spirit-power/items", self.handle_spirit_power_items),
            web.post("/api/app/spirit-power/check-in", self.handle_spirit_power_check_in),
            web.post("/api/app/spirit-power/items/use", self.handle_spirit_dew_use),
            web.post("/api/app/me/password", self.handle_update_password),
            web.get("/api/app/admin/metrics", self.handle_admin_metrics),
            web.get("/api/app/admin/conversations", self.handle_admin_conversations),
            web.get("/api/app/admin/energy-events", self.handle_admin_energy_events),
            web.get("/api/app/admin/spirit-power-events", self.handle_admin_energy_events),
            web.get("/api/app/admin/intimacy-events", self.handle_admin_intimacy_events),
            web.get("/api/app/admin/devices", self.handle_admin_devices),
            web.post("/api/app/admin/devices", self.handle_admin_create_device),
            web.post("/api/app/admin/devices/{device_id}/rotate-code", self.handle_admin_rotate_device_code),
            web.get("/api/app/devices", self.handle_devices),
            web.post("/api/app/devices/bind", self.handle_bind_device),
            web.get("/api/app/devices/{device_id}", self.handle_device_detail),
            web.put("/api/app/devices/{device_id}", self.handle_update_device),
            web.get("/api/app/devices/{device_id}/settings", self.handle_device_settings),
            web.put("/api/app/devices/{device_id}/settings", self.handle_update_settings),
            web.get("/api/app/devices/{device_id}/memories", self.handle_memories),
            web.post("/api/app/devices/{device_id}/memories", self.handle_create_memory),
            web.put("/api/app/devices/{device_id}/memories/{memory_id}", self.handle_update_memory),
            web.delete("/api/app/devices/{device_id}/memories/{memory_id}", self.handle_delete_memory),
            web.post("/api/app/devices/{device_id}/debug/chat", self.handle_debug_chat),
            web.get("/api/app/devices/{device_id}/connection", self.handle_connection_diagnostic),
            web.get("/api/app/devices/{device_id}/events", self.handle_device_events),
            web.get("/api/app/devices/{device_id}/dialogues", self.handle_dialogues),
            web.get("/api/app/devices/{device_id}/diaries", self.handle_diaries),
            web.post("/api/app/devices/{device_id}/diaries/generate", self.handle_generate_diary),
            web.get("/api/app/devices/{device_id}/ota", self.handle_ota),
            web.post("/api/app/devices/{device_id}/ota/upgrade", self.handle_ota_upgrade),
            web.post("/api/app/devices/{device_id}/refresh-status", self.handle_refresh_status),
            web.post("/api/app/devices/{device_id}/demo/run", self.handle_demo_run),
            web.post("/api/app/devices/{device_id}/unbind", self.handle_unbind_device),
            web.options("/api/app/{tail:.*}", self.handle_options),
        ]

    async def handle_options(self, request):
        response = web.Response(body=b"", content_type="text/plain")
        self._add_cors_headers(response)
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return response

    async def handle_register(self, request):
        payload = await self._read_json(request)
        if payload.get("phone") is None:
            return await self._handle_invite_auth_payload(payload)
        try:
            result = register_phone_user(
                self.config,
                phone=str(payload.get("phone", "")).strip(),
                password=str(payload.get("password", "")),
                nickname=str(payload.get("nickname", "")).strip(),
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        return self._json_response(result)

    async def handle_login(self, request):
        payload = await self._read_json(request)
        if payload.get("phone") is None:
            return await self._handle_invite_auth_payload(payload)
        try:
            result = login_phone_user(
                self.config,
                phone=str(payload.get("phone", "")).strip(),
                password=str(payload.get("password", "")),
            )
        except ValueError as e:
            return self._error_response(str(e), status=401)
        return self._json_response(result)

    async def _handle_invite_auth(self, request):
        payload = await self._read_json(request)
        return await self._handle_invite_auth_payload(payload)

    async def _handle_invite_auth_payload(self, payload):
        try:
            result = register_or_login_user(
                self.config,
                invite_code=str(payload.get("invite_code", "")).strip(),
                nickname=str(payload.get("nickname", "")).strip(),
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        return self._json_response(result)

    async def handle_demo_login(self, request):
        return self._json_response(
            {
                "token": DEMO_TOKEN,
                "legacy": True,
                "user": user_summary(self.config, DEMO_USER_ID),
            }
        )

    async def handle_me(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        return self._json_response(user_summary(self.config, user["id"]))

    async def handle_spirit_power(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        return self._json_response(spirit_power_summary(self.config, user["id"]))

    async def handle_spirit_power_check_in(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        try:
            return self._json_response(check_in_spirit_power(self.config, user["id"]))
        except ValueError as e:
            return self._error_response(str(e), status=409)

    async def handle_spirit_power_items(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        return self._json_response({"items": list_spirit_power_items(self.config, user["id"])})

    async def handle_spirit_dew_use(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        try:
            payload = await self._read_json(request)
            return self._json_response(use_spirit_dew(self.config, user["id"], str(payload.get("item_id", "")).strip() or None))
        except ValueError as e:
            return self._error_response(str(e), status=409)

    async def handle_update_password(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        payload = await self._read_json(request)
        try:
            updated = update_user_password(
                self.config,
                user["id"],
                old_password=str(payload.get("old_password", "")),
                new_password=str(payload.get("new_password", "")),
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        if not updated:
            return self._error_response("旧密码错误", status=403)
        return self._json_response({"updated": True})

    async def handle_admin_metrics(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        return self._json_response(admin_metrics(self.config))

    async def handle_admin_conversations(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        return self._json_response({"items": list_admin_conversations(self.config, self._limit(request))})

    async def handle_admin_energy_events(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        return self._json_response({"items": list_energy_events(self.config, self._limit(request))})

    async def handle_admin_intimacy_events(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        return self._json_response({"items": list_intimacy_events(self.config, self._limit(request))})

    async def handle_admin_devices(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        return self._json_response({"items": list_admin_devices(self.config)})

    async def handle_admin_create_device(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        payload = await self._read_json(request)
        try:
            device = create_device(
                self.config,
                device_code=str(payload.get("device_code", "")).strip() or None,
                display_name=str(payload.get("display_name", "")).strip() or None,
                source_device_id=str(payload.get("source_device_id", "")).strip() or None,
                client_id=str(payload.get("client_id", "")).strip() or None,
                model=str(payload.get("model", "")).strip() or None,
                firmware_version=str(payload.get("firmware_version", "")).strip() or None,
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        return self._json_response(device)

    async def handle_admin_rotate_device_code(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        payload = await self._read_json(request)
        try:
            device = rotate_device_code(
                self.config,
                request.match_info["device_id"],
                str(payload.get("device_code", "")).strip() or None,
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        if not device:
            return self._error_response("设备不存在", status=404)
        return self._json_response(device)

    async def handle_devices(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        return self._json_response({"items": [self._device_payload(item) for item in list_devices(self.config, user["id"])]})

    async def handle_bind_device(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        payload = await self._read_json(request)
        device_code = str(payload.get("device_code", "")).strip()
        if not device_code:
            return self._error_response("device_code 不能为空", status=400)
        try:
            device = bind_device(self.config, user["id"], device_code)
        except ValueError as e:
            return self._error_response(str(e), status=409)
        if not device:
            return self._error_response("设备码不存在", status=404)
        return self._json_response(self._device_payload(device))

    async def handle_device_detail(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        return self._json_response(self._device_payload(device_or_response))

    async def handle_update_device(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        payload = await self._read_json(request)
        display_name = str(payload.get("display_name", "")).strip()
        if not display_name:
            return self._error_response("display_name 不能为空", status=400)
        device = update_device_name(self.config, user["id"], request.match_info["device_id"], display_name)
        if not device:
            return self._error_response("设备不存在或未绑定", status=404)
        return self._json_response(self._device_payload(device))

    async def handle_device_settings(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        return self._json_response(get_settings(self.config, user["id"], device_or_response["id"]))

    async def handle_update_settings(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        payload = await self._read_json(request)
        values = {}
        for field in ("baize_nickname", "user_call_name", "personality_mode", "tts_voice"):
            if field in payload:
                value = str(payload[field]).strip()
                if not value:
                    return self._error_response(f"{field} 不能为空", status=400)
                values[field] = value
        settings = update_settings(self.config, user["id"], device_or_response["id"], values)
        return self._json_response(settings)

    async def handle_memories(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        return self._json_response({"items": list_memories(self.config, user["id"], device_or_response["id"]) or []})

    async def handle_create_memory(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        payload = await self._read_json(request)
        try:
            memory = upsert_memory(
                self.config,
                user["id"],
                device_or_response["id"],
                str(payload.get("category", "note")).strip(),
                str(payload.get("content", "")).strip(),
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        if not memory:
            return self._error_response("设备不存在或未绑定", status=404)
        return self._json_response(memory)

    async def handle_update_memory(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        payload = await self._read_json(request)
        try:
            memory = upsert_memory(
                self.config,
                user["id"],
                device_or_response["id"],
                str(payload.get("category", "note")).strip(),
                str(payload.get("content", "")).strip(),
                memory_id=request.match_info["memory_id"],
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        if not memory:
            return self._error_response("记忆不存在", status=404)
        return self._json_response(memory)

    async def handle_delete_memory(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        deleted = delete_memory(
            self.config,
            user["id"],
            device_or_response["id"],
            request.match_info["memory_id"],
        )
        if not deleted:
            return self._error_response("记忆不存在", status=404)
        return self._json_response({"deleted": True, "id": request.match_info["memory_id"]})

    async def handle_debug_chat(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        device = device_or_response
        if user_summary(self.config, user["id"])["spirit_power"]["current"] < 5:
            return self._error_response("白泽的灵力不足，稍后恢复一些再来陪你。", status=409)

        payload = await self._read_json(request)
        user_text = str(payload.get("text", "")).strip()
        if not user_text:
            return self._error_response("text 不能为空", status=400)
        try:
            system_prompt = self._build_debug_prompt(device["id"])
            if not system_prompt:
                return self._error_response("未配置白泽 prompt", status=503)
            reply = str(self._get_demo_llm().response_no_stream(system_prompt, user_text)).strip()
            reply = clean_baize_text(reply)
            if not reply:
                return self._error_response("LLM 未返回内容", status=502)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Debug Chat 调用失败: {e}")
            return self._error_response("Debug Chat 调用失败", status=500)

        session_id = f"demo_chat_{uuid.uuid4().hex}"
        if not consume_energy(self.config, user["id"], device["id"], 5, "debug_chat"):
            return self._error_response("白泽的灵力不足，稍后恢复一些再来陪你。", status=409)
        dialogue = append_dialogue(
            self.config,
            source_device_id=device.get("source_device_id") or "",
            session_id=session_id,
            user_text=user_text,
            baize_text=reply,
            emotion="neutral",
            user_id=user["id"],
            device_id=device["id"],
            source="debug",
        )
        return self._json_response({"reply": reply, "session_id": session_id, "dialogue": dialogue})

    async def handle_dialogues(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        return self._json_response({"items": list_dialogues(self.config, user["id"], device_or_response["id"]) or []})

    async def handle_diaries(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        return self._json_response({"items": list_diaries(self.config, user["id"], device_or_response["id"]) or []})

    async def handle_generate_diary(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        payload = await self._read_json(request)
        diary_date = str(payload.get("date", "")).strip() or None
        diary = generate_diary(self.config, diary_date=diary_date, user_id=user["id"], device_id=device_or_response["id"])
        if not diary:
            return self._error_response("没有可生成日记的对话记录", status=404)
        return self._json_response(diary)

    async def handle_ota(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        return self._json_response(ota_payload(self.config, user["id"], device_or_response["id"]))

    async def handle_ota_upgrade(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        ota = ota_payload(self.config, user["id"], device_or_response["id"])
        if not ota:
            return self._error_response("设备不存在或未绑定", status=404)
        if not ota.get("update_available"):
            return self._json_response(
                {
                    "requested": False,
                    "device_online": False,
                    "message": "当前已经是最新版本",
                    "ota": ota,
                }
            )
        conn = self._find_active_connection(device_or_response)
        if conn is None:
            return self._json_response(
                {
                    "requested": False,
                    "device_online": False,
                    "message": "白泽当前不在线，开机联网后会自动检查升级",
                    "ota": ota,
                }
            )
        try:
            await conn.websocket.send(
                json.dumps(
                    {
                        "type": "system",
                        "command": "reboot",
                        "reason": "ota_upgrade_requested",
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"发送 OTA 升级指令失败: {e}")
            return self._error_response("发送 OTA 升级指令失败", status=502)
        return self._json_response(
            {
                "requested": True,
                "device_online": True,
                "message": "已通知白泽重启并检查 OTA 升级",
                "ota": ota,
            }
        )

    async def handle_connection_diagnostic(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        device = device_or_response
        matched_identifier, matched_value, conn = self._find_active_connection_match(device)
        active_identifiers = []
        if self.device_registry is not None and hasattr(self.device_registry, "active_identifiers"):
            active_identifiers = self.device_registry.active_identifiers()
        return self._json_response(
            {
                "online": conn is not None,
                "matched_identifier": matched_identifier,
                "matched_value": matched_value,
                "active_identifiers": active_identifiers,
                "device": {
                    "id": device.get("id"),
                    "source_device_id": device.get("source_device_id"),
                    "client_id": device.get("client_id"),
                    "online_status": self._device_payload(device).get("online_status"),
                },
            }
        )

    async def handle_device_events(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        device_id = device_or_response["id"]
        queue = asyncio.Queue()
        self._event_subscribers.setdefault(device_id, set()).add(queue)
        try:
            if not await self._send_device_event_json(ws, self._device_event(device_or_response, "device.status.snapshot")):
                return ws
            while not ws.closed:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    continue
                if not await self._send_device_event_json(ws, event):
                    break
        finally:
            subscribers = self._event_subscribers.get(device_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._event_subscribers.pop(device_id, None)
        return ws

    async def handle_refresh_status(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        conn = self._find_active_connection(device_or_response)
        if conn is None:
            return self._error_response("设备当前不在线，无法刷新 MCP 状态", status=409)
        try:
            battery_percent = await self.mcp_status_refresher(conn)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"刷新 MCP 状态失败: {e}")
            return self._error_response("刷新 MCP 状态失败", status=502)
        device = bound_device(self.config, user["id"], device_or_response["id"])
        payload = self._device_payload(device)
        if battery_percent is not None:
            payload["battery_percent"] = battery_percent
        return self._json_response(payload)

    async def handle_demo_run(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        conn = self._find_active_connection(device_or_response)
        if conn is None:
            return self._error_response("设备当前不在线，无法执行 Demo", status=409)
        payload = await self._read_json(request)
        script = str(payload.get("script", "sixty_second")).strip() or "sixty_second"
        if script != "sixty_second":
            return self._error_response("Demo 阶段仅支持 sixty_second 脚本", status=400)
        prompt = self._build_sixty_second_demo_prompt(device_or_response, user["id"])
        try:
            run_result = await self.demo_runner(conn, prompt)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"执行 60 秒 Demo 失败: {e}")
            return self._error_response("执行 Demo 失败", status=502)
        response_payload = {"started": True, "script": script, "prompt": prompt}
        if isinstance(run_result, dict):
            response_payload.update(run_result)
        return self._json_response(response_payload)

    async def handle_unbind_device(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        if not unbind_device(self.config, user["id"], device_or_response["id"]):
            return self._error_response("设备不存在或未绑定", status=404)
        return self._json_response({"unbound": True, "id": device_or_response["id"]})

    async def _read_json(self, request) -> Dict[str, Any]:
        try:
            return await request.json()
        except Exception:
            return {}

    def _current_user(self, request) -> Dict[str, Any] | None:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        return user_for_token(self.config, auth_header.removeprefix("Bearer ").strip())

    def _require_admin_response(self, request) -> web.Response | None:
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        if user.get("role") != "admin":
            return self._error_response("需要管理员权限", status=403)
        return None

    def _get_bound_device_or_response(self, request) -> tuple[Dict[str, Any] | None, Any]:
        user = self._current_user(request)
        if not user:
            return None, self._error_response("未登录或 token 无效", status=401)
        device = bound_device(self.config, user["id"], request.match_info["device_id"])
        if not device:
            return user, self._error_response("设备不存在或未绑定", status=404)
        return user, device

    def _device_payload(self, device: Dict[str, Any]) -> Dict[str, Any]:
        fields = (
            "id",
            "device_code",
            "display_name",
            "source_device_id",
            "client_id",
            "model",
            "online_status",
            "activity_status",
            "battery_percent",
            "firmware_version",
            "last_online_at",
        )
        payload = {field: device.get(field) for field in fields}
        if self._has_real_device_identity(device) and self.device_registry is not None:
            conn = self._find_active_connection(device)
            payload["online_status"] = "online" if conn is not None else "offline"
            if conn is not None and getattr(conn, "activity_status", None):
                payload["activity_status"] = getattr(conn, "activity_status")
        return payload

    def _has_real_device_identity(self, device: Dict[str, Any]) -> bool:
        return bool(device.get("source_device_id") or device.get("client_id"))

    def _find_active_connection(self, device: Dict[str, Any]):
        return self._find_active_connection_match(device)[2]

    def _find_active_connection_match(self, device: Dict[str, Any]):
        if self.device_registry is None:
            return None, None, None
        for field in ("source_device_id", "client_id", "id"):
            value = device.get(field)
            conn = self.device_registry.get(value)
            if conn is not None:
                return field, value, conn
        return None, None, None

    async def _refresh_status_from_connection(self, conn):
        mcp_client = getattr(conn, "mcp_client", None)
        if mcp_client is None:
            raise RuntimeError("设备连接没有 MCP 客户端")
        await _refresh_device_status_report(conn, mcp_client)
        return getattr(conn, "battery_percent", None)

    async def _handle_registry_event(self, event_type: str, conn) -> None:
        device = resolve_bound_app_device(
            self.config,
            source_device_id=getattr(conn, "device_id", "") or "",
            client_id=getattr(conn, "headers", {}).get("client-id", ""),
        )
        if not device:
            return
        await self._broadcast_device_event(device["id"], self._device_event(device, "device.status.updated"))

    def _device_event(self, device: Dict[str, Any], event_type: str) -> Dict[str, Any]:
        return {
            "type": event_type,
            "device_id": device["id"],
            "device": self._device_payload(device),
        }

    async def _send_device_event_json(self, ws: web.WebSocketResponse, event: Dict[str, Any]) -> bool:
        if ws.closed:
            return False
        try:
            await ws.send_json(event)
            return True
        except (ClientConnectionResetError, ConnectionResetError, RuntimeError):
            return False

    async def _broadcast_device_event(self, device_id: str, event: Dict[str, Any]) -> None:
        queues = list(self._event_subscribers.get(device_id, set()))
        for queue in queues:
            queue.put_nowait(event)

    async def _run_demo_on_connection(self, conn, prompt: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, conn.chat, prompt)
        return {"started": True}

    def _build_sixty_second_demo_prompt(self, device: Dict[str, Any], user_id: str) -> str:
        settings = get_settings(self.config, user_id, device["id"]) or {}
        baize_name = settings.get("baize_nickname", "白泽")
        user_call_name = settings.get("user_call_name", "小伙伴")
        return (
            "请执行白泽幼灵 60 秒 Demo。"
            "你正在真实设备上和用户互动，请用温柔、简短、适合播放的中文回答。"
            f"你的昵称是{baize_name}，称呼用户为{user_call_name}。"
            "开场请表现为刚被触摸唤醒，然后简短介绍：你是来自上古神话世界的白泽幼灵。"
            "语气轻快、有精神，控制在 1 到 2 句话。"
        )

    def _get_demo_llm(self):
        if self._demo_llm is not None:
            return self._demo_llm
        selected_module = self.config.get("selected_module", {}).get("LLM")
        if not selected_module:
            raise ValueError("selected_module.LLM 未配置")
        llm_config = self.config.get("LLM", {}).get(selected_module)
        if not llm_config:
            raise ValueError(f"LLM 配置缺失: {selected_module}")
        self._demo_llm = self.llm_factory(llm_config.get("type", selected_module), llm_config)
        return self._demo_llm

    def _build_debug_prompt(self, device_id: str) -> str:
        user_prompt = str(self.config.get("prompt", "")).strip()
        if not user_prompt:
            return ""
        prompt_manager = PromptManager(self.config, self.logger)
        prompt_manager.update_context_info(SimpleNamespace(device_id=device_id), None)
        quick_prompt = prompt_manager.get_quick_prompt(user_prompt)
        return prompt_manager.build_enhanced_prompt(quick_prompt, device_id, None, emoji_enabled=True) or quick_prompt

    def _limit(self, request, default: int = 100) -> int:
        try:
            return max(1, min(int(request.query.get("limit", default)), 500))
        except Exception:
            return default

    def _json_response(self, payload: Dict[str, Any], status: int = 200) -> web.Response:
        response = web.Response(
            text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            content_type="application/json",
            status=status,
        )
        self._add_cors_headers(response)
        return response

    def _error_response(self, message: str, status: int) -> web.Response:
        return self._json_response({"error": message}, status=status)
