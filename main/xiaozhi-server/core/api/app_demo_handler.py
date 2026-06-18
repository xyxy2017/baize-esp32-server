import json
import uuid
from copy import deepcopy
from types import SimpleNamespace
import asyncio
from typing import Any, Dict, Optional

from aiohttp import web

from core.api.app_demo_store import (
    DEMO_DEVICE_CODE,
    DEMO_DEVICE_ID,
    DEMO_TOKEN,
    DEMO_USER_ID,
    append_dialogue,
    clean_baize_text,
    default_state,
    generate_diary,
    load_state,
    merge_defaults,
    now_iso,
    save_state,
    state_path_from_config,
)
from core.api.base_handler import BaseHandler
from core.utils import llm as llm_utils
from core.utils.prompt_manager import PromptManager
from core.providers.tools.device_mcp.mcp_handler import _refresh_device_status_report

TAG = __name__


class AppDemoHandler(BaseHandler):
    """Lightweight user-facing App API for the iOS Demo.

    The data is intentionally small and file-backed so the iOS app can call real
    API endpoints while firmware status reporting is still being wired up.
    """

    def __init__(
        self,
        config: dict,
        llm_factory=None,
        device_registry=None,
        mcp_status_refresher=None,
        demo_runner=None,
    ):
        super().__init__(config)
        self.state_path = state_path_from_config(config)
        self.llm_factory = llm_factory or llm_utils.create_instance
        self._demo_llm = None
        self.device_registry = device_registry
        self.mcp_status_refresher = (
            mcp_status_refresher or self._refresh_status_from_connection
        )
        self.demo_runner = demo_runner or self._run_demo_on_connection

    def routes(self):
        return [
            web.post("/api/app/demo-login", self.handle_demo_login),
            web.get("/api/app/me", self.handle_me),
            web.get("/api/app/devices", self.handle_devices),
            web.post("/api/app/devices/bind", self.handle_bind_device),
            web.get("/api/app/devices/{device_id}", self.handle_device_detail),
            web.put("/api/app/devices/{device_id}", self.handle_update_device),
            web.get(
                "/api/app/devices/{device_id}/settings", self.handle_device_settings
            ),
            web.put(
                "/api/app/devices/{device_id}/settings", self.handle_update_settings
            ),
            web.get("/api/app/devices/{device_id}/memories", self.handle_memories),
            web.delete(
                "/api/app/devices/{device_id}/memories/{memory_id}",
                self.handle_delete_memory,
            ),
            web.post(
                "/api/app/devices/{device_id}/debug/chat", self.handle_debug_chat
            ),
            web.get(
                "/api/app/devices/{device_id}/connection",
                self.handle_connection_diagnostic,
            ),
            web.get("/api/app/devices/{device_id}/dialogues", self.handle_dialogues),
            web.get("/api/app/devices/{device_id}/diaries", self.handle_diaries),
            web.post(
                "/api/app/devices/{device_id}/diaries/generate",
                self.handle_generate_diary,
            ),
            web.get("/api/app/devices/{device_id}/ota", self.handle_ota),
            web.post(
                "/api/app/devices/{device_id}/refresh-status",
                self.handle_refresh_status,
            ),
            web.post("/api/app/devices/{device_id}/demo/run", self.handle_demo_run),
            web.post("/api/app/devices/{device_id}/unbind", self.handle_unbind_device),
            web.options("/api/app/{tail:.*}", self.handle_options),
        ]

    async def handle_options(self, request):
        response = web.Response(body=b"", content_type="text/plain")
        self._add_cors_headers(response)
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, DELETE, OPTIONS"
        )
        return response

    async def handle_demo_login(self, request):
        state = self._load_state()
        response = self._json_response(
            {
                "token": DEMO_TOKEN,
                "user": state["users"][DEMO_USER_ID],
            }
        )
        return response

    async def handle_me(self, request):
        if not self._is_authorized(request):
            return self._error_response("未登录或 token 无效", status=401)
        state = self._load_state()
        return self._json_response(state["users"][DEMO_USER_ID])

    async def handle_devices(self, request):
        if not self._is_authorized(request):
            return self._error_response("未登录或 token 无效", status=401)
        state = self._load_state()
        devices = []
        for device_id in state["bindings"].get(DEMO_USER_ID, []):
            device = state["devices"].get(device_id)
            if device:
                devices.append(self._device_payload(device))
        return self._json_response({"items": devices})

    async def handle_bind_device(self, request):
        if not self._is_authorized(request):
            return self._error_response("未登录或 token 无效", status=401)
        payload = await self._read_json(request)
        device_code = str(payload.get("device_code", "")).strip()
        if not device_code:
            return self._error_response("device_code 不能为空", status=400)
        if device_code != DEMO_DEVICE_CODE:
            return self._error_response("Demo 阶段仅支持设备码 123456", status=404)

        state = self._load_state()
        bindings = state["bindings"].setdefault(DEMO_USER_ID, [])
        if DEMO_DEVICE_ID not in bindings:
            bindings.append(DEMO_DEVICE_ID)
            state["devices"][DEMO_DEVICE_ID]["bound_at"] = now_iso()
            self._save_state(state)
        return self._json_response(self._device_payload(state["devices"][DEMO_DEVICE_ID]))

    async def handle_device_detail(self, request):
        device = self._get_bound_device_or_response(request)
        if isinstance(device, web.Response):
            return device
        return self._json_response(self._device_payload(device))

    async def handle_update_device(self, request):
        if not self._is_authorized(request):
            return self._error_response("未登录或 token 无效", status=401)
        device_id = request.match_info["device_id"]
        payload = await self._read_json(request)
        display_name = str(payload.get("display_name", "")).strip()
        if not display_name:
            return self._error_response("display_name 不能为空", status=400)

        state = self._load_state()
        if not self._is_bound(state, device_id):
            return self._error_response("设备不存在或未绑定", status=404)
        state["devices"][device_id]["display_name"] = display_name
        self._save_state(state)
        return self._json_response(self._device_payload(state["devices"][device_id]))

    async def handle_device_settings(self, request):
        device = self._get_bound_device_or_response(request)
        if isinstance(device, web.Response):
            return device
        return self._json_response(device["settings"])

    async def handle_update_settings(self, request):
        if not self._is_authorized(request):
            return self._error_response("未登录或 token 无效", status=401)
        device_id = request.match_info["device_id"]
        payload = await self._read_json(request)
        state = self._load_state()
        if not self._is_bound(state, device_id):
            return self._error_response("设备不存在或未绑定", status=404)

        settings = state["devices"][device_id]["settings"]
        for field in ("baize_nickname", "user_call_name", "personality_mode"):
            if field in payload:
                value = str(payload[field]).strip()
                if not value:
                    return self._error_response(f"{field} 不能为空", status=400)
                settings[field] = value
        self._save_state(state)
        return self._json_response(settings)

    async def handle_memories(self, request):
        device = self._get_bound_device_or_response(request)
        if isinstance(device, web.Response):
            return device
        return self._json_response({"items": device.get("memories", [])})

    async def handle_delete_memory(self, request):
        if not self._is_authorized(request):
            return self._error_response("未登录或 token 无效", status=401)
        device_id = request.match_info["device_id"]
        memory_id = request.match_info["memory_id"]
        state = self._load_state()
        if not self._is_bound(state, device_id):
            return self._error_response("设备不存在或未绑定", status=404)

        memories = state["devices"][device_id].get("memories", [])
        next_memories = [item for item in memories if item.get("id") != memory_id]
        if len(next_memories) == len(memories):
            return self._error_response("记忆不存在", status=404)
        state["devices"][device_id]["memories"] = next_memories
        self._save_state(state)
        return self._json_response({"deleted": True, "id": memory_id})

    async def handle_debug_chat(self, request):
        device = self._get_bound_device_or_response(request)
        if isinstance(device, web.Response):
            return device

        payload = await self._read_json(request)
        user_text = str(payload.get("text", "")).strip()
        if not user_text:
            return self._error_response("text 不能为空", status=400)

        try:
            system_prompt = self._build_debug_prompt(device["id"])
            if not system_prompt:
                return self._error_response("未配置白泽 prompt", status=503)

            llm_provider = self._get_demo_llm()
            reply = str(
                llm_provider.response_no_stream(system_prompt, user_text)
            ).strip()
            reply = clean_baize_text(reply)
            if not reply:
                return self._error_response("LLM 未返回内容", status=502)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Debug Chat 调用失败: {e}")
            return self._error_response("Debug Chat 调用失败", status=500)

        session_id = f"demo_chat_{uuid.uuid4().hex}"
        dialogue = append_dialogue(
            self.config,
            source_device_id=device.get("source_device_id", "") or "",
            session_id=session_id,
            user_text=user_text,
            baize_text=reply,
            emotion="neutral",
        )
        return self._json_response(
            {
                "reply": reply,
                "session_id": session_id,
                "dialogue": dialogue,
            }
        )

    async def handle_dialogues(self, request):
        device = self._get_bound_device_or_response(request)
        if isinstance(device, web.Response):
            return device
        return self._json_response({"items": device.get("dialogues", [])})

    async def handle_diaries(self, request):
        device = self._get_bound_device_or_response(request)
        if isinstance(device, web.Response):
            return device
        return self._json_response({"items": device.get("diaries", [])})

    async def handle_generate_diary(self, request):
        device = self._get_bound_device_or_response(request)
        if isinstance(device, web.Response):
            return device
        payload = await self._read_json(request)
        diary_date = str(payload.get("date", "")).strip() or None
        diary = generate_diary(self.config, diary_date=diary_date)
        if not diary:
            return self._error_response("没有可生成日记的对话记录", status=404)
        return self._json_response(diary)

    async def handle_ota(self, request):
        device = self._get_bound_device_or_response(request)
        if isinstance(device, web.Response):
            return device
        ota = deepcopy(device.get("ota", {}))
        ota["device_id"] = device["id"]
        return self._json_response(ota)

    async def handle_connection_diagnostic(self, request):
        device = self._get_bound_device_or_response(request)
        if isinstance(device, web.Response):
            return device

        matched_identifier, matched_value, conn = self._find_active_connection_match(
            device
        )
        active_identifiers = []
        if self.device_registry is not None and hasattr(
            self.device_registry, "active_identifiers"
        ):
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
                    "online_status": self._device_payload(device).get(
                        "online_status"
                    ),
                },
            }
        )

    async def handle_refresh_status(self, request):
        device = self._get_bound_device_or_response(request)
        if isinstance(device, web.Response):
            return device

        conn = self._find_active_connection(device)
        if conn is None:
            return self._error_response("设备当前不在线，无法刷新 MCP 状态", status=409)

        try:
            battery_percent = await self.mcp_status_refresher(conn)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"刷新 MCP 状态失败: {e}")
            return self._error_response("刷新 MCP 状态失败", status=502)

        refreshed_device = self._load_state()["devices"][device["id"]]
        payload = self._device_payload(refreshed_device)
        if battery_percent is not None:
            payload["battery_percent"] = battery_percent
        return self._json_response(payload)

    async def handle_demo_run(self, request):
        device = self._get_bound_device_or_response(request)
        if isinstance(device, web.Response):
            return device

        conn = self._find_active_connection(device)
        if conn is None:
            return self._error_response("设备当前不在线，无法执行 Demo", status=409)

        payload = await self._read_json(request)
        script = str(payload.get("script", "sixty_second")).strip() or "sixty_second"
        if script != "sixty_second":
            return self._error_response("Demo 阶段仅支持 sixty_second 脚本", status=400)

        prompt = self._build_sixty_second_demo_prompt(device)
        try:
            run_result = await self.demo_runner(conn, prompt)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"执行 60 秒 Demo 失败: {e}")
            return self._error_response("执行 Demo 失败", status=502)

        response_payload = {
            "started": True,
            "script": script,
            "prompt": prompt,
        }
        if isinstance(run_result, dict):
            response_payload.update(run_result)
        return self._json_response(response_payload)

    async def handle_unbind_device(self, request):
        if not self._is_authorized(request):
            return self._error_response("未登录或 token 无效", status=401)
        device_id = request.match_info["device_id"]
        state = self._load_state()
        bindings = state["bindings"].setdefault(DEMO_USER_ID, [])
        if device_id not in bindings:
            return self._error_response("设备不存在或未绑定", status=404)
        state["bindings"][DEMO_USER_ID] = [item for item in bindings if item != device_id]
        self._save_state(state)
        return self._json_response({"unbound": True, "id": device_id})

    def _load_state(self) -> Dict[str, Any]:
        try:
            state = load_state(self.state_path)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"读取 App Demo 状态失败: {e}")
            state = default_state()
            self._save_state(state)
        return state

    def _save_state(self, state: Dict[str, Any]) -> None:
        save_state(self.state_path, state)

    def _merge_defaults(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return merge_defaults(state)

    def _default_state(self) -> Dict[str, Any]:
        return default_state()

    async def _read_json(self, request) -> Dict[str, Any]:
        try:
            return await request.json()
        except Exception:
            return {}

    def _is_authorized(self, request) -> bool:
        auth_header = request.headers.get("Authorization", "")
        return auth_header == f"Bearer {DEMO_TOKEN}"

    def _is_bound(self, state: Dict[str, Any], device_id: str) -> bool:
        return device_id in state["bindings"].get(DEMO_USER_ID, [])

    def _get_bound_device_or_response(self, request) -> Any:
        if not self._is_authorized(request):
            return self._error_response("未登录或 token 无效", status=401)
        device_id = request.match_info["device_id"]
        state = self._load_state()
        if not self._is_bound(state, device_id):
            return self._error_response("设备不存在或未绑定", status=404)
        return state["devices"][device_id]

    def _device_payload(self, device: Dict[str, Any]) -> Dict[str, Any]:
        fields = (
            "id",
            "device_code",
            "display_name",
            "source_device_id",
            "client_id",
            "model",
            "online_status",
            "battery_percent",
            "firmware_version",
            "last_online_at",
        )
        payload = {field: device.get(field) for field in fields}
        if self._has_real_device_identity(device) and self.device_registry is not None:
            payload["online_status"] = (
                "online" if self._find_active_connection(device) is not None else "offline"
            )
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
        state = self._load_state()
        device = state["devices"].get(DEMO_DEVICE_ID, {})
        return device.get("battery_percent")

    async def _run_demo_on_connection(self, conn, prompt: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, conn.chat, prompt)
        return {"started": True}

    def _build_sixty_second_demo_prompt(self, device: Dict[str, Any]) -> str:
        settings = device.get("settings", {})
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
        llm_type = llm_config.get("type", selected_module)
        self._demo_llm = self.llm_factory(llm_type, llm_config)
        return self._demo_llm

    def _build_debug_prompt(self, device_id: str) -> str:
        user_prompt = str(self.config.get("prompt", "")).strip()
        if not user_prompt:
            return ""

        prompt_manager = PromptManager(self.config, self.logger)
        prompt_manager.update_context_info(SimpleNamespace(device_id=device_id), None)
        quick_prompt = prompt_manager.get_quick_prompt(user_prompt)
        enhanced_prompt = prompt_manager.build_enhanced_prompt(
            quick_prompt,
            device_id,
            None,
            emoji_enabled=True,
        )
        return enhanced_prompt or quick_prompt

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
