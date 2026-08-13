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
    configured_hardware_settings,
    consume_energy,
    check_in_spirit_power,
    create_support_ticket,
    create_device,
    delete_user_account,
    dashboard_admin_for_token,
    delete_memory,
    generate_diary,
    get_settings,
    list_admin_conversations,
    list_admin_devices,
    list_admin_users,
    list_ops_support_tickets,
    list_user_support_tickets,
    login_dashboard_admin,
    list_devices,
    list_dialogues,
    list_diaries,
    list_energy_events,
    list_intimacy_events,
    list_memories_page,
    list_spirit_power_items,
    login_phone_user,
    mark_sms_verification_failed,
    mark_sms_verification_sent,
    ota_payload,
    operations_snapshot,
    register_phone_user,
    register_or_login_user,
    record_app_telemetry,
    reset_user_password_with_sms,
    resolve_bound_app_device,
    rotate_device_code,
    unbind_device,
    update_support_ticket,
    update_device_name,
    update_user_password,
    update_settings,
    user_for_token,
    user_summary,
    export_user_data,
    create_sms_verification_request,
    SMSRateLimitError,
    spirit_power_summary,
    use_spirit_dew,
    verify_sms_code_and_login,
)
from core.api.app_memory_store import (
    create_memory as create_memory_item,
    enqueue_rebuild_jobs,
    forget_memories,
    handle_memory_command,
    list_memory_jobs,
    memory_feedback,
    memory_summary,
    memory_v2_enabled,
    retrieve_memory_context,
    update_memory as update_memory_item,
)
from core.api.base_handler import BaseHandler
from core.api.sms_sender import (
    AliyunSMSSender,
    SMSConfigurationError,
    SMSProviderError,
)
from core.content_safety import (
    append_content_safety_prompt,
    blocked_response,
    content_safety_summary,
    create_safety_appeal,
    is_provider_moderation_error,
    list_safety_appeals,
    list_safety_events,
    moderate_text,
    provider_block_decision,
    resolve_safety_appeal,
)
from core.providers.tools.device_mcp.mcp_handler import (
    _refresh_device_status_report,
    call_mcp_tool,
)
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
        hardware_settings_applier=None,
        sms_sender=None,
    ):
        super().__init__(config)
        self.llm_factory = llm_factory or llm_utils.create_instance
        self._demo_llm = None
        self.device_registry = device_registry
        self.mcp_status_refresher = mcp_status_refresher or self._refresh_status_from_connection
        self.demo_runner = demo_runner or self._run_demo_on_connection
        self.hardware_settings_applier = hardware_settings_applier or self._apply_hardware_settings
        self.sms_sender = sms_sender or AliyunSMSSender(config)
        self._event_subscribers = {}
        self._dashboard_login_failures = {}
        if self.device_registry is not None and hasattr(self.device_registry, "subscribe"):
            self.device_registry.subscribe(self._handle_registry_event)

    def routes(self):
        return [
            web.get("/support", self.handle_support_page),
            web.get("/privacy", self.handle_privacy_page),
            web.get("/account-deletion", self.handle_account_deletion_page),
            web.get("/status", self.handle_status_page),
            web.get("/baize-ops/console", self.handle_dashboard_page),
            web.post("/api/app/ops/login", self.handle_dashboard_login),
            web.get("/api/app/ops/summary", self.handle_dashboard_summary),
            web.get("/api/app/ops/tickets", self.handle_ops_tickets),
            web.put("/api/app/ops/tickets/{ticket_id}", self.handle_ops_ticket_update),
            web.post("/api/app/register", self.handle_register),
            web.post("/api/app/login", self.handle_login),
            web.post("/api/app/auth/sms/send", self.handle_send_sms_code),
            web.post("/api/app/auth/sms/verify", self.handle_verify_sms_code),
            web.post("/api/app/auth/password/reset", self.handle_reset_password),
            web.post("/api/app/telemetry/crash", self.handle_app_crash),
            web.post("/api/app/telemetry/api", self.handle_app_api_metric),
            web.post("/api/app/demo-login", self.handle_demo_login),
            web.get("/api/app/me", self.handle_me),
            web.delete("/api/app/me", self.handle_delete_account),
            web.get("/api/app/me/export", self.handle_export_account),
            web.get("/api/app/support/tickets", self.handle_support_tickets),
            web.post("/api/app/support/tickets", self.handle_create_support_ticket),
            web.get("/api/app/spirit-power", self.handle_spirit_power),
            web.get("/api/app/spirit-power/items", self.handle_spirit_power_items),
            web.post("/api/app/spirit-power/check-in", self.handle_spirit_power_check_in),
            web.post("/api/app/spirit-power/items/use", self.handle_spirit_dew_use),
            web.post("/api/app/me/password", self.handle_update_password),
            web.get("/api/app/admin/metrics", self.handle_admin_metrics),
            web.get("/api/app/admin/users", self.handle_admin_users),
            web.get("/api/app/admin/conversations", self.handle_admin_conversations),
            web.get("/api/app/admin/energy-events", self.handle_admin_energy_events),
            web.get("/api/app/admin/spirit-power-events", self.handle_admin_energy_events),
            web.get("/api/app/admin/intimacy-events", self.handle_admin_intimacy_events),
            web.get("/api/app/admin/devices", self.handle_admin_devices),
            web.post("/api/app/admin/devices", self.handle_admin_create_device),
            web.post("/api/app/admin/devices/{device_id}/rotate-code", self.handle_admin_rotate_device_code),
            web.post("/api/app/admin/memory/rebuild", self.handle_admin_memory_rebuild),
            web.get("/api/app/admin/memory/jobs", self.handle_admin_memory_jobs),
            web.get("/api/app/admin/content-safety/summary", self.handle_admin_content_safety_summary),
            web.get("/api/app/admin/content-safety/events", self.handle_admin_content_safety_events),
            web.post("/api/app/admin/content-safety/check", self.handle_admin_content_safety_check),
            web.get("/api/app/admin/content-safety/appeals", self.handle_admin_content_safety_appeals),
            web.put("/api/app/admin/content-safety/appeals/{appeal_id}", self.handle_admin_resolve_content_safety_appeal),
            web.post("/api/app/content-safety/appeals", self.handle_content_safety_appeal),
            web.get("/api/app/devices", self.handle_devices),
            web.post("/api/app/devices/bind", self.handle_bind_device),
            web.get("/api/app/devices/{device_id}", self.handle_device_detail),
            web.put("/api/app/devices/{device_id}", self.handle_update_device),
            web.get("/api/app/devices/{device_id}/settings", self.handle_device_settings),
            web.put("/api/app/devices/{device_id}/settings", self.handle_update_settings),
            web.get("/api/app/devices/{device_id}/memories", self.handle_memories),
            web.post("/api/app/devices/{device_id}/memories", self.handle_create_memory),
            web.post("/api/app/devices/{device_id}/memories/forget", self.handle_forget_memories),
            web.put("/api/app/devices/{device_id}/memories/{memory_id}", self.handle_update_memory),
            web.delete("/api/app/devices/{device_id}/memories/{memory_id}", self.handle_delete_memory),
            web.post("/api/app/devices/{device_id}/memories/{memory_id}/feedback", self.handle_memory_feedback),
            web.get("/api/app/devices/{device_id}/memory-summary", self.handle_memory_summary),
            web.post("/api/app/devices/{device_id}/debug/chat", self.handle_debug_chat),
            web.get("/api/app/devices/{device_id}/connection", self.handle_connection_diagnostic),
            web.get("/api/app/devices/{device_id}/events", self.handle_device_events),
            web.get("/api/app/devices/{device_id}/dialogues", self.handle_dialogues),
            web.get("/api/app/devices/{device_id}/diaries", self.handle_diaries),
            web.post("/api/app/devices/{device_id}/diaries/generate", self.handle_generate_diary),
            web.get("/api/app/devices/{device_id}/ota", self.handle_ota),
            web.post("/api/app/devices/{device_id}/ota/upgrade", self.handle_ota_upgrade),
            web.post("/api/app/devices/{device_id}/eye-assets/upgrade", self.handle_eye_assets_upgrade),
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
        if payload.get("confirm_password") is not None and str(
            payload.get("password", "")
        ) != str(payload.get("confirm_password", "")):
            return self._error_response("两次输入的密码不一致", status=400)
        try:
            auth_settings = self.config.get("app_mvp", {}).get("auth", {}) or {}
            result = register_phone_user(
                self.config,
                phone=str(payload.get("phone", "")).strip(),
                password=str(payload.get("password", "")),
                nickname=str(payload.get("nickname", "")).strip(),
                verification_code=str(payload.get("code", "")).strip(),
                require_verification=bool(
                    auth_settings.get("registration_verification_required", False)
                ),
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

    async def handle_send_sms_code(self, request):
        payload = await self._read_json(request)
        purpose = str(payload.get("purpose", "login")).strip() or "login"
        verification = None
        try:
            verification = create_sms_verification_request(
                self.config,
                phone=str(payload.get("phone", "")).strip(),
                purpose=purpose,
                request_ip=request.remote,
            )
            provider_request_id = await self.sms_sender.send_verification_code(
                verification["phone"], verification["code"]
            )
            mark_sms_verification_sent(
                self.config, verification["request_id"], provider_request_id
            )
        except SMSRateLimitError as error:
            return self._json_response(
                {
                    "error": str(error),
                    "retry_after_seconds": error.retry_after_seconds,
                },
                status=429,
            )
        except SMSConfigurationError as error:
            if verification:
                mark_sms_verification_failed(self.config, verification["request_id"])
            self.logger.bind(tag=TAG).warning(f"短信服务配置不可用: {error}")
            return self._error_response("短信服务暂未配置，请使用密码登录", status=503)
        except SMSProviderError as error:
            if verification:
                mark_sms_verification_failed(self.config, verification["request_id"])
            self.logger.bind(tag=TAG).warning(f"阿里云短信发送失败: code={error.code}")
            return self._error_response(error.public_message, status=502)
        except ValueError as error:
            if verification:
                mark_sms_verification_failed(self.config, verification["request_id"])
            return self._error_response(str(error), status=400)

        return self._json_response(
            {
                "masked_phone": verification["phone"][:3]
                + "****"
                + verification["phone"][-4:],
                "expires_in_seconds": verification["expires_in_seconds"],
                "retry_after_seconds": verification["retry_after_seconds"],
            }
        )

    async def handle_verify_sms_code(self, request):
        payload = await self._read_json(request)
        try:
            result = verify_sms_code_and_login(
                self.config,
                phone=str(payload.get("phone", "")).strip(),
                code=str(payload.get("code", "")).strip(),
                nickname=str(payload.get("nickname", "")).strip(),
                purpose=str(payload.get("purpose", "login")).strip() or "login",
            )
        except ValueError as error:
            return self._error_response(str(error), status=400)
        return self._json_response(result)

    async def handle_reset_password(self, request):
        payload = await self._read_json(request)
        new_password = str(payload.get("new_password", ""))
        if new_password != str(payload.get("confirm_password", "")):
            return self._error_response("两次输入的密码不一致", status=400)
        try:
            reset_user_password_with_sms(
                self.config,
                phone=str(payload.get("phone", "")).strip(),
                code=str(payload.get("code", "")).strip(),
                new_password=new_password,
            )
        except ValueError as error:
            return self._error_response(str(error), status=400)
        return self._json_response({"ok": True})

    async def handle_app_crash(self, request):
        return await self._handle_app_telemetry(request, "crash")

    async def handle_app_api_metric(self, request):
        return await self._handle_app_telemetry(request, "api")

    async def _handle_app_telemetry(self, request, event_type):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        if request.content_length and request.content_length > 16 * 1024:
            return self._error_response("上报内容过大", status=413)
        try:
            payload = await self._read_json(request)
            result = record_app_telemetry(
                self.config, user["id"], event_type, payload
            )
            from core.telemetry import app_api_reported, app_crash_reported

            if event_type == "crash":
                app_crash_reported(
                    str(payload.get("platform", "")),
                    str(payload.get("error_type", "")),
                )
            else:
                app_api_reported(
                    str(payload.get("platform", "")),
                    str(payload.get("route", "")),
                    payload.get("status_code"),
                    payload.get("duration_ms", 0),
                )
            return self._json_response(result, status=202)
        except (TypeError, ValueError) as error:
            return self._error_response(str(error), status=400)

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
        auth_settings = self.config.get("app_mvp", {}).get("auth", {}) or {}
        if not bool(auth_settings.get("demo_login_enabled", True)):
            return self._error_response("Demo 登录已关闭", status=404)
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

    async def handle_delete_account(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        payload = await self._read_json(request)
        if str(payload.get("confirmation", "")).strip() != "注销账号":
            return self._error_response("请输入“注销账号”确认", status=400)
        try:
            deleted = delete_user_account(self.config, user["id"])
        except ValueError as e:
            return self._error_response(str(e), status=409)
        if not deleted:
            return self._error_response("账号不存在", status=404)
        return self._json_response({"deleted": True})

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
        new_password = str(payload.get("new_password", ""))
        if new_password != str(payload.get("confirm_password", "")):
            return self._error_response("两次输入的密码不一致", status=400)
        try:
            updated = update_user_password(
                self.config,
                user["id"],
                old_password=str(payload.get("old_password", "")),
                new_password=new_password,
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        if not updated:
            return self._error_response("旧密码错误", status=403)
        return self._json_response({"updated": True, "requires_reauthentication": True})

    async def handle_admin_metrics(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        return self._json_response(admin_metrics(self.config))

    async def handle_admin_users(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        return self._json_response({"items": list_admin_users(self.config)})

    async def handle_support_page(self, request):
        return web.Response(text=self._public_page(
            "白泽幼灵支持",
            "如需账号、设备、隐私或使用帮助，请发送邮件至 ranlimaowc@163.com。我们会尽快处理。",
        ), content_type="text/html", charset="utf-8")

    async def handle_privacy_page(self, request):
        return web.Response(text=self._public_page(
            "白泽幼灵隐私政策",
            "燃力猫文化创意有限公司仅在提供账号、设备绑定、语音陪伴、日记与安全保障所必需的范围内处理信息。你可以在 App 的“我的 → 隐私与账号”中注销账号，或通过 ranlimaowc@163.com 联系我们行使访问、更正、删除等权利。",
        ), content_type="text/html", charset="utf-8")

    async def handle_account_deletion_page(self, request):
        return web.Response(text=self._public_page(
            "注销白泽幼灵账号",
            "请在 App 内打开“我的 → 隐私与账号 → 注销账号”，确认后账号及其个人数据将被永久删除，设备会自动解绑。如无法进入 App，请联系 ranlimaowc@163.com。",
        ), content_type="text/html", charset="utf-8")

    async def handle_status_page(self, request):
        return web.Response(text=self._public_page(
            "山海幼灵服务状态",
            "当前账号、设备绑定和陪伴服务运行正常。如你遇到问题，请在 App 的“帮助与售后”提交工单，或发送邮件至 ranlimaowc@163.com。",
        ), content_type="text/html", charset="utf-8")

    async def handle_dashboard_login(self, request):
        forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        client_ip = forwarded or request.remote or "unknown"
        now = asyncio.get_running_loop().time()
        attempts = [
            timestamp
            for timestamp in self._dashboard_login_failures.get(client_ip, [])
            if now - timestamp < 900
        ]
        self._dashboard_login_failures[client_ip] = attempts
        if len(attempts) >= 5:
            return self._error_response("登录失败次数过多，请 15 分钟后重试", status=429)
        payload = await self._read_json(request)
        try:
            result = login_dashboard_admin(
                self.config,
                str(payload.get("username", "")),
                str(payload.get("password", "")),
            )
        except ValueError as e:
            attempts.append(now)
            return self._error_response(str(e), status=401)
        self._dashboard_login_failures.pop(client_ip, None)
        return self._json_response(result)

    async def handle_dashboard_summary(self, request):
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
        if not dashboard_admin_for_token(self.config, token):
            return self._error_response("运营登录已失效", status=401)
        return self._json_response({
            "metrics": admin_metrics(self.config),
            "users": list_admin_users(self.config),
            "operations": operations_snapshot(self.config),
        })

    def _dashboard_admin(self, request) -> str | None:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
        return dashboard_admin_for_token(self.config, token)

    async def handle_ops_tickets(self, request):
        if not self._dashboard_admin(request):
            return self._error_response("运营登录已失效", status=401)
        try:
            items = list_ops_support_tickets(self.config, request.query.get("status"))
        except ValueError as e:
            return self._error_response(str(e), status=400)
        return self._json_response({"items": items})

    async def handle_ops_ticket_update(self, request):
        username = self._dashboard_admin(request)
        if not username:
            return self._error_response("运营登录已失效", status=401)
        payload = await self._read_json(request)
        try:
            ticket = update_support_ticket(
                self.config,
                request.match_info["ticket_id"],
                str(payload.get("status", "")),
                str(payload.get("operator_reply", "")),
                username,
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        if not ticket:
            return self._error_response("工单不存在", status=404)
        return self._json_response(ticket)

    async def handle_export_account(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        data = export_user_data(self.config, user["id"])
        if not data:
            return self._error_response("账号不存在", status=404)
        return self._json_response(data)

    async def handle_support_tickets(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        return self._json_response({"items": list_user_support_tickets(self.config, user["id"])})

    async def handle_create_support_ticket(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        payload = await self._read_json(request)
        try:
            ticket = create_support_ticket(
                self.config,
                user["id"],
                str(payload.get("category", "other")),
                str(payload.get("subject", "")),
                str(payload.get("message", "")),
                str(payload.get("device_id", "")) or None,
            )
        except ValueError as e:
            return self._error_response(str(e), status=429 if "频繁" in str(e) else 400)
        return self._json_response(ticket, status=201)

    async def handle_dashboard_page(self, request):
        return web.Response(text=self._operations_dashboard_html(), content_type="text/html", charset="utf-8")

    @staticmethod
    def _operations_dashboard_html() -> str:
        return """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>山海幼灵运营中心</title><style>:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#edf4ff;color:#18345f;font:14px/1.5 -apple-system,BlinkMacSystemFont,sans-serif}main{max-width:1240px;margin:auto;padding:28px}.card{background:#fff;border:1px solid #dce7f5;border-radius:20px;padding:20px;margin:14px 0;box-shadow:0 10px 32px #214a7a12}input,select,textarea,button{font:inherit;padding:11px 13px;border:1px solid #cbd9ea;border-radius:11px;margin:4px}textarea{min-width:260px;min-height:76px}button{background:#28558e;color:#fff;border:0;cursor:pointer}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}.metric{margin:0}.metric b{font-size:27px;display:block}.tabs button{background:#dce9f8;color:#244b7b}.tabs button.active{background:#28558e;color:#fff}.table{overflow:auto}table{width:100%;border-collapse:collapse;min-width:720px}th,td{text-align:left;padding:11px 8px;border-bottom:1px solid #e8eef6;vertical-align:top}th{color:#657896}.badge{display:inline-block;padding:3px 8px;border-radius:99px;background:#e5eef9}.warn{color:#b54708}#error{color:#b42318}@media(max-width:700px){main{padding:12px}.card{padding:14px}}</style><main><h1>山海幼灵运营中心</h1><p>用户、设备、版本、异常与售后工单 · 轻量同进程运行</p><section id="login" class="card"><input id="username" autocomplete="username" placeholder="运营账号"><input id="password" type="password" autocomplete="current-password" placeholder="密码"><button onclick="signIn()">登录</button><span id="error"></span></section><section id="content" hidden><div id="metrics" class="metrics"></div><div class="tabs"><button id="usersTab" onclick="showTab('users')">用户</button><button id="devicesTab" onclick="showTab('devices')">设备</button><button id="ticketsTab" onclick="showTab('tickets')">售后工单</button><button onclick="load()">刷新</button><button onclick="signOut()">退出</button></div><div id="users" class="card table"></div><div id="devices" class="card table" hidden></div><div id="tickets" class="card table" hidden></div></section><small>运营主体：燃力猫文化创意有限公司 · 客服 ranlimaowc@163.com</small><script>const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let token=sessionStorage.getItem('baize_ops_token');async function request(path,opt={}){let r=await fetch(path,{...opt,headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{}),...opt.headers}}),j=await r.json();if(!r.ok)throw Error(j.error||'请求失败');return j}async function signIn(){try{let j=await request('/api/app/ops/login',{method:'POST',body:JSON.stringify({username:$('username').value,password:$('password').value})});token=j.token;sessionStorage.setItem('baize_ops_token',token);await load()}catch(e){$('error').textContent=e.message}}function signOut(){sessionStorage.removeItem('baize_ops_token');token='';$('login').hidden=false;$('content').hidden=true}function showTab(name){for(let n of ['users','devices','tickets']){$(n).hidden=n!==name;$(n+'Tab').className=n===name?'active':''}}function table(headers,rows){return '<table><thead><tr>'+headers.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.join('')+'</tbody></table>'}async function load(){try{let j=await request('/api/app/ops/summary'),m=j.metrics,o=j.operations,u=j.users,t=await request('/api/app/ops/tickets');$('login').hidden=true;$('content').hidden=false;$('metrics').innerHTML=[['用户',m.users],['已绑定设备',m.bound_devices],['待处理工单',o.open_tickets],['24h 崩溃',o.app_crashes_24h],['24h 接口 5xx',o.app_api_5xx_24h],['对话',m.dialogues]].map(x=>`<div class="card metric"><b>${esc(x[1])}</b>${esc(x[0])}</div>`).join('');$('users').innerHTML=table(['用户','手机号','登录','设备','最后登录'],u.map(x=>`<tr><td><b>${esc(x.nickname)}</b><br><small>${esc(x.id)}</small></td><td>${esc(x.masked_phone||'—')}</td><td>${esc(x.login_type)}${x.has_password?' · 有密码':''}</td><td>${esc(x.device_count)} ${esc(x.device_names.join('、'))}</td><td>${esc(x.last_login_at)}</td></tr>`));$('devices').innerHTML='<p>固件分布：'+Object.entries(o.firmware_versions).map(x=>esc(x[0])+' × '+esc(x[1])).join('，')+'</p>'+table(['设备','型号/版本','状态/电量','绑定用户','最后在线'],o.devices.map(x=>`<tr><td><b>${esc(x.display_name)}</b><br><small>${esc(x.id)}</small></td><td>${esc(x.model||'—')}<br>${esc(x.firmware_version||'—')}</td><td class="${x.online_status==='online'?'':'warn'}">${esc(x.online_status)} · ${esc(x.battery_percent??'—')}%</td><td>${esc(x.bound_user_id||'未绑定')}</td><td>${esc(x.last_online_at||'从未')}</td></tr>`));$('tickets').innerHTML=table(['状态','用户/设备','问题','时间','处理'],t.items.map(x=>`<tr><td><span class="badge">${esc(x.status)}</span></td><td>${esc(x.nickname)} · ${esc(x.masked_phone||'—')}<br>${esc(x.device_name||'无设备')}</td><td><b>${esc(x.subject)}</b><br>${esc(x.message)}<br><small>${esc(x.operator_reply||'尚未回复')}</small></td><td>${esc(x.updated_at)}</td><td><select id="s_${esc(x.id)}"><option value="open">待处理</option><option value="in_progress">处理中</option><option value="resolved">已解决</option><option value="closed">已关闭</option></select><textarea id="r_${esc(x.id)}" placeholder="给用户的回复">${esc(x.operator_reply||'')}</textarea><button onclick="saveTicket('${esc(x.id)}')">保存</button></td></tr>`));for(let x of t.items){let s=$('s_'+x.id);if(s)s.value=x.status}showTab('users')}catch(e){signOut();$('error').textContent=e.message}}async function saveTicket(id){try{await request('/api/app/ops/tickets/'+encodeURIComponent(id),{method:'PUT',body:JSON.stringify({status:$('s_'+id).value,operator_reply:$('r_'+id).value})});await load();showTab('tickets')}catch(e){alert(e.message)}}if(token)load();</script></main></html>"""

    @staticmethod
    def _public_page(title: str, body: str) -> str:
        return f"""<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title}</title><style>body{{margin:0;background:#f4f7ff;color:#16233f;font:16px/1.7 -apple-system,BlinkMacSystemFont,sans-serif}}main{{max-width:720px;margin:10vh auto;padding:32px;background:#fff;border-radius:24px;box-shadow:0 16px 50px #25477a18}}h1{{font-size:28px}}small{{color:#65728a}}</style><main><h1>{title}</h1><p>{body}</p><small>运营主体：燃力猫文化创意有限公司</small></main></html>"""

    @staticmethod
    def _admin_dashboard_html() -> str:
        return """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>白泽运营中心</title><style>body{margin:0;background:#f1f5fb;color:#16233f;font:14px -apple-system,BlinkMacSystemFont,sans-serif}main{max-width:1180px;margin:auto;padding:28px}.card{background:#fff;border-radius:20px;padding:20px;margin:14px 0;box-shadow:0 10px 35px #24446c14}input,button{padding:11px 13px;border:1px solid #cdd8e8;border-radius:11px;margin:4px}button{background:#244f88;color:#fff;border:0;cursor:pointer}.metrics{display:flex;gap:12px;flex-wrap:wrap}.metric{min-width:130px}.metric b{font-size:26px;display:block}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px 8px;border-bottom:1px solid #e8edf5}th{color:#65728a}#error{color:#b42318}@media(max-width:700px){main{padding:12px}.table{overflow:auto}}</style><main><h1>白泽运营中心</h1><p>独立运营账号 · 只读用户概览 · 手机号掩码展示</p><section id="login" class="card"><input id="username" autocomplete="username" placeholder="运营账号"><input id="password" type="password" autocomplete="current-password" placeholder="密码"><button onclick="signIn()">登录</button><span id="error"></span></section><section id="content" hidden><div id="metrics" class="metrics"></div><div class="card"><button onclick="load()">刷新</button><button onclick="signOut()">退出</button><div class="table"><table><thead><tr><th>用户</th><th>手机号</th><th>登录</th><th>设备</th><th>最后登录</th></tr></thead><tbody id="rows"></tbody></table></div></div></section><small>运营主体：燃力猫文化创意有限公司</small><script>const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));let token=sessionStorage.getItem('baize_ops_token');async function request(path,opt={}){let r=await fetch(path,{...opt,headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{}),...opt.headers}}),j=await r.json();if(!r.ok)throw Error(j.error||'请求失败');return j}async function signIn(){try{let j=await request('/api/app/ops/login',{method:'POST',body:JSON.stringify({username:$('username').value,password:$('password').value})});token=j.token;sessionStorage.setItem('baize_ops_token',token);await load()}catch(e){$('error').textContent=e.message}}function signOut(){sessionStorage.removeItem('baize_ops_token');token='';$('login').hidden=false;$('content').hidden=true}async function load(){try{let j=await request('/api/app/ops/summary'),m=j.metrics,u=j.users;$('login').hidden=true;$('content').hidden=false;$('metrics').innerHTML=[['用户',m.users],['手机账号',m.phone_users],['已绑定设备',m.bound_devices],['对话',m.dialogues],['日记',m.diaries]].map(x=>`<div class="card metric"><b>${esc(x[1])}</b>${esc(x[0])}</div>`).join('');$('rows').innerHTML=u.map(x=>`<tr><td><b>${esc(x.nickname)}</b><br><small>${esc(x.id)}</small></td><td>${esc(x.masked_phone||'—')}</td><td>${esc(x.login_type)}${x.has_password?' · 有密码':''}</td><td>${esc(x.device_count)} ${esc(x.device_names.join('、'))}</td><td>${esc(x.last_login_at)}</td></tr>`).join('')}catch(e){signOut();$('error').textContent=e.message}}if(token)load();</script></main></html>"""

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

    async def handle_admin_memory_rebuild(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        payload = await self._read_json(request)
        enqueued = enqueue_rebuild_jobs(
            self.config,
            user_id=str(payload.get("user_id", "")).strip() or None,
            device_id=str(payload.get("device_id", "")).strip() or None,
        )
        return self._json_response({"enqueued": enqueued})

    async def handle_admin_memory_jobs(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        try:
            items = list_memory_jobs(
                self.config,
                status=str(request.query.get("status", "")).strip() or None,
                limit=self._limit(request),
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        return self._json_response({"items": items})

    async def handle_admin_content_safety_summary(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        return self._json_response(content_safety_summary(self.config))

    async def handle_admin_content_safety_events(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        items = list_safety_events(
            self.config,
            action=str(request.query.get("action", "")).strip() or None,
            category=str(request.query.get("category", "")).strip() or None,
            direction=str(request.query.get("direction", "")).strip() or None,
            source=str(request.query.get("source", "")).strip() or None,
            limit=self._limit(request),
        )
        return self._json_response({"items": items})

    async def handle_admin_content_safety_check(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        payload = await self._read_json(request)
        text = str(payload.get("text", "")).strip()
        if not text:
            return self._error_response("text 不能为空", status=400)
        direction = str(payload.get("direction", "input")).strip() or "input"
        decision = moderate_text(
            self.config,
            text,
            direction=direction,
            source="admin_check",
            session_id=f"admin_check_{uuid.uuid4().hex}",
        )
        return self._json_response(decision.public_payload())

    async def handle_content_safety_appeal(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        payload = await self._read_json(request)
        try:
            appeal = create_safety_appeal(
                self.config,
                user["id"],
                str(payload.get("event_id", "")).strip(),
                str(payload.get("reason", "")).strip(),
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        if not appeal:
            return self._error_response("风控事件不存在", status=404)
        return self._json_response(appeal)

    async def handle_admin_content_safety_appeals(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        return self._json_response(
            {
                "items": list_safety_appeals(
                    self.config,
                    status=str(request.query.get("status", "")).strip() or None,
                    limit=self._limit(request),
                )
            }
        )

    async def handle_admin_resolve_content_safety_appeal(self, request):
        response = self._require_admin_response(request)
        if response:
            return response
        payload = await self._read_json(request)
        try:
            appeal = resolve_safety_appeal(
                self.config,
                request.match_info["appeal_id"],
                status=str(payload.get("status", "")).strip(),
                resolution_note=str(payload.get("resolution_note", "")).strip(),
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        if not appeal:
            return self._error_response("申诉不存在", status=404)
        return self._json_response(appeal)

    async def handle_devices(self, request):
        user = self._current_user(request)
        if not user:
            return self._error_response("未登录或 token 无效", status=401)
        return self._json_response(
            {
                "items": [
                    self._device_payload(item, user["id"])
                    for item in list_devices(self.config, user["id"])
                ]
            }
        )

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
        return self._json_response(self._device_payload(device, user["id"]))

    async def handle_device_detail(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        return self._json_response(self._device_payload(device_or_response, user["id"]))

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
        hardware_limits = {
            "speaker_volume": (0, 100),
            "screen_brightness": (10, 100),
        }
        for field, (minimum, maximum) in hardware_limits.items():
            if field not in payload:
                continue
            value = payload[field]
            if isinstance(value, bool):
                return self._error_response(f"{field} 必须是 {minimum} 到 {maximum} 的整数", status=400)
            try:
                value = int(value)
            except (TypeError, ValueError):
                return self._error_response(f"{field} 必须是 {minimum} 到 {maximum} 的整数", status=400)
            if value < minimum or value > maximum:
                return self._error_response(f"{field} 必须是 {minimum} 到 {maximum} 的整数", status=400)
            values[field] = value
        settings = update_settings(self.config, user["id"], device_or_response["id"], values)
        hardware_values = {
            field: values[field]
            for field in hardware_limits
            if field in values
        }
        if hardware_values:
            sync_result = await self.hardware_settings_applier(device_or_response, hardware_values)
            settings.update(sync_result)
        return self._json_response(settings)

    async def handle_memories(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        pinned = None
        if "pinned" in request.query:
            pinned = str(request.query.get("pinned", "")).lower() in {
                "1",
                "true",
                "yes",
            }
        try:
            page = list_memories_page(
                self.config,
                user["id"],
                device_or_response["id"],
                scope=str(request.query.get("scope", "")).strip() or None,
                memory_type=str(
                    request.query.get("type", request.query.get("category", ""))
                ).strip()
                or None,
                pinned=pinned,
                status=str(request.query.get("status", "active")).strip()
                or "active",
                limit=self._limit(request),
                cursor=str(request.query.get("cursor", "")).strip() or None,
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        payload = {"items": (page or {}).get("items", [])}
        if page and page.get("next_cursor"):
            payload["next_cursor"] = page["next_cursor"]
        return self._json_response(payload)

    async def handle_create_memory(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        payload = await self._read_json(request)
        memory_content = str(payload.get("content", "")).strip()
        safety = moderate_text(
            self.config,
            memory_content,
            direction="memory",
            source="memory_api",
            user_id=user["id"],
            device_id=device_or_response["id"],
            session_id=f"memory_api_{uuid.uuid4().hex}",
        )
        if safety.blocked:
            return self._json_response(
                {"created": False, "blocked": True, "safety": safety.public_payload()},
                status=422,
            )
        try:
            memory = create_memory_item(
                self.config,
                user["id"],
                device_or_response["id"],
                content=memory_content,
                memory_type=str(
                    payload.get("type", payload.get("category", "note"))
                ).strip(),
                scope=str(payload.get("scope", "relationship")).strip(),
                key=str(payload.get("key", "")).strip() or None,
                importance=payload.get("importance", 70),
                pinned=bool(payload.get("pinned", False)),
                expires_at=payload.get("expires_at"),
                occurred_at=payload.get("occurred_at"),
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
        if "content" in payload:
            safety = moderate_text(
                self.config,
                str(payload.get("content", "")).strip(),
                direction="memory",
                source="memory_api",
                user_id=user["id"],
                device_id=device_or_response["id"],
                session_id=f"memory_api_{uuid.uuid4().hex}",
            )
            if safety.blocked:
                return self._json_response(
                    {
                        "updated": False,
                        "blocked": True,
                        "safety": safety.public_payload(),
                    },
                    status=422,
                )
        try:
            memory = update_memory_item(
                self.config,
                user["id"],
                device_or_response["id"],
                request.match_info["memory_id"],
                payload,
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

    async def handle_forget_memories(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        payload = await self._read_json(request)
        try:
            result = forget_memories(
                self.config,
                user["id"],
                device_or_response["id"],
                str(payload.get("query", "")).strip(),
                scope=str(payload.get("scope", "")).strip() or None,
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        return self._json_response(result or {"matched": False, "deleted": []})

    async def handle_memory_feedback(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        payload = await self._read_json(request)
        try:
            result = memory_feedback(
                self.config,
                user["id"],
                device_or_response["id"],
                request.match_info["memory_id"],
                str(payload.get("result", "")).strip(),
            )
        except ValueError as e:
            return self._error_response(str(e), status=400)
        if not result:
            return self._error_response("记忆不存在", status=404)
        return self._json_response(result)

    async def handle_memory_summary(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        result = memory_summary(
            self.config, user["id"], device_or_response["id"]
        )
        return self._json_response(result or {})

    async def handle_debug_chat(self, request):
        user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response
        device = device_or_response
        payload = await self._read_json(request)
        user_text = str(payload.get("text", "")).strip()
        if not user_text:
            return self._error_response("text 不能为空", status=400)
        safety_session_id = f"debug_guard_{uuid.uuid4().hex}"
        input_decision = moderate_text(
            self.config,
            user_text,
            direction="input",
            source="debug_chat",
            user_id=user["id"],
            device_id=device["id"],
            session_id=safety_session_id,
        )
        if input_decision.blocked:
            return self._json_response(
                {
                    "reply": blocked_response(self.config, input_decision, direction="input"),
                    "blocked": True,
                    "safety": input_decision.public_payload(),
                    "ai_generated": True,
                }
            )
        if user_summary(self.config, user["id"])["spirit_power"]["current"] < 5:
            return self._error_response("白泽的灵力不足，稍后恢复一些再来陪你。", status=409)
        try:
            memory_control = {"handled": False, "opt_out": False}
            retrieval = None
            if memory_v2_enabled(self.config, device["id"]):
                memory_control = handle_memory_command(
                    self.config, user["id"], device["id"], user_text
                )
                retrieval = retrieve_memory_context(
                    self.config,
                    user["id"],
                    device["id"],
                    user_text,
                    session_id="debug",
                )
            system_prompt = append_content_safety_prompt(
                self.config, self._build_debug_prompt(device["id"])
            )
            if not system_prompt:
                return self._error_response("未配置白泽 prompt", status=503)
            if retrieval and retrieval.get("context"):
                system_prompt = f"{system_prompt.rstrip()}\n{retrieval['context']}"
            if memory_control.get("prompt_notice"):
                system_prompt = (
                    f"{system_prompt.rstrip()}\n<memory_operation>"
                    f"{memory_control['prompt_notice']}</memory_operation>"
                )
            reply = str(self._get_demo_llm().response_no_stream(system_prompt, user_text)).strip()
            reply = clean_baize_text(reply)
            if not reply:
                return self._error_response("LLM 未返回内容", status=502)
        except Exception as e:
            if is_provider_moderation_error(e):
                decision = provider_block_decision(
                    self.config,
                    text=user_text,
                    direction="output",
                    source="debug_chat",
                    user_id=user["id"],
                    device_id=device["id"],
                    session_id=safety_session_id,
                    error=e,
                )
                return self._json_response(
                    {
                        "reply": blocked_response(self.config, decision, direction="output"),
                        "blocked": True,
                        "safety": decision.public_payload(),
                        "ai_generated": True,
                    }
                )
            self.logger.bind(tag=TAG, error_type=type(e).__name__).error(
                "debug_chat_failed"
            )
            return self._error_response("Debug Chat 调用失败", status=500)

        output_decision = moderate_text(
            self.config,
            reply,
            direction="output",
            source="debug_chat",
            user_id=user["id"],
            device_id=device["id"],
            session_id=safety_session_id,
        )
        if output_decision.blocked:
            return self._json_response(
                {
                    "reply": blocked_response(self.config, output_decision, direction="output"),
                    "blocked": True,
                    "safety": output_decision.public_payload(),
                    "ai_generated": True,
                }
            )

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
            memory_opt_out=bool(memory_control.get("opt_out")),
            memory_retrieval=retrieval,
        )
        return self._json_response(
            {
                "reply": reply,
                "session_id": session_id,
                "dialogue": dialogue,
                "memory_action": memory_control.get("action"),
                "memory_error": memory_control.get("error"),
                "blocked": False,
                "ai_generated": True,
            }
        )

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

    async def handle_eye_assets_upgrade(self, request):
        """Ask an online device to check and apply an eye-animation package only."""
        _user, device_or_response = self._get_bound_device_or_response(request)
        if isinstance(device_or_response, web.Response):
            return device_or_response

        conn = self._find_active_connection(device_or_response)
        if conn is None:
            return self._json_response(
                {
                    "requested": False,
                    "device_online": False,
                    "message": "白泽当前不在线，开机联网后会自动检查动画资源",
                }
            )
        try:
            await conn.websocket.send(
                json.dumps(
                    {
                        "type": "system",
                        "command": "check_ota",
                        "reason": "eye_assets_upgrade_requested",
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"发送动画资源更新指令失败: {e}")
            return self._error_response("发送动画资源更新指令失败", status=502)
        return self._json_response(
            {
                "requested": True,
                "device_online": True,
                "message": "已通知白泽检查动画资源；校验通过后会自动切换新表情",
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

    def _device_payload(
        self, device: Dict[str, Any], user_id: str | None = None
    ) -> Dict[str, Any]:
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
        if user_id:
            summary = memory_summary(self.config, user_id, device["id"])
            if summary:
                payload["memory"] = {
                    "active_count": summary["total"],
                    "pending_jobs": summary["pending_jobs"],
                }
                payload["growth"] = summary["growth"]
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
        if event_type == "registered":
            # MCP may still be initializing here. The device MCP handler repeats the
            # same replay after it becomes ready; this early attempt handles already
            # initialized reconnections without waiting for the next event.
            desired = configured_hardware_settings(self.config, device["id"])
            if desired:
                await self._apply_hardware_settings(device, desired)
        await self._broadcast_device_event(device["id"], self._device_event(device, "device.status.updated"))

    async def _apply_hardware_settings(self, device: Dict[str, Any], values: Dict[str, int]) -> Dict[str, str]:
        conn = self._find_active_connection(device)
        if conn is None:
            return {
                "device_sync_status": "pending",
                "device_sync_message": "白泽当前不在线，设置会在下次连接后同步",
            }
        mcp_client = getattr(conn, "mcp_client", None)
        if mcp_client is None:
            return {
                "device_sync_status": "pending",
                "device_sync_message": "白泽正在建立控制连接，设置会自动重试",
            }
        tool_mapping = {
            "speaker_volume": ("self_audio_speaker_set_volume", "volume"),
            "screen_brightness": ("self_screen_set_brightness", "brightness"),
        }
        try:
            for setting_key, value in values.items():
                tool_name, argument_name = tool_mapping[setting_key]
                await call_mcp_tool(
                    conn,
                    mcp_client,
                    tool_name,
                    {argument_name: value},
                    timeout=5,
                )
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"设备设置待同步: {e}")
            return {
                "device_sync_status": "pending",
                "device_sync_message": "设置已保存，白泽连接稳定后会自动同步",
            }
        return {
            "device_sync_status": "applied",
            "device_sync_message": "已同步到白泽",
        }

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
        llm_config = dict(llm_config)
        llm_config["_content_safety"] = self.config.get("content_safety", {}) or {}
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
