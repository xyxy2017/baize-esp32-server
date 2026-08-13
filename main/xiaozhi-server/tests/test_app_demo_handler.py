import asyncio
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import FormData, web
from aiohttp.client_exceptions import ClientConnectionResetError

logger_module = types.ModuleType("config.logger")


class _NoopLogger:
    def bind(self, **kwargs):
        return self

    def error(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


logger_module.setup_logging = lambda: _NoopLogger()
sys.modules.setdefault("config.logger", logger_module)
sys.modules.setdefault("opuslib_next", types.ModuleType("opuslib_next"))
pydub_module = types.ModuleType("pydub")
pydub_module.AudioSegment = type("AudioSegment", (), {})
sys.modules.setdefault("pydub", pydub_module)
jwt_module = types.ModuleType("jwt")
jwt_module.InvalidTokenError = type("InvalidTokenError", (Exception,), {})
jwt_module.encode = lambda *_args, **_kwargs: "test-jwt"
jwt_module.decode = lambda *_args, **_kwargs: {}
sys.modules.setdefault("jwt", jwt_module)

from core.api.app_demo_handler import AppDemoHandler, DEMO_TOKEN
from core.api.app_demo_store import set_dashboard_admin
from core.api.health_handler import HealthHandler
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler


class VoiceSafetyPrecheckTest(unittest.TestCase):
    def test_unsafe_asr_text_bypasses_intent_and_reaches_guarded_chat(self):
        from unittest.mock import AsyncMock, patch

        from core.handle import receiveAudioHandle

        class _Executor:
            def __init__(self):
                self.calls = []

            def submit(self, fn, *args):
                self.calls.append((fn, args))

        executor = _Executor()
        chat = lambda _text: None
        conn = types.SimpleNamespace(
            common_config={"content_safety": {"enabled": True, "mode": "enforce"}},
            logger=_NoopLogger(),
            need_bind=False,
            max_output_size=0,
            headers={"device-id": "voice-device"},
            client_is_speaking=False,
            client_listen_mode="auto",
            client_abort=True,
            executor=executor,
            chat=chat,
            current_speaker=None,
        )
        intent = AsyncMock(return_value=False)
        send_stt = AsyncMock()
        with patch.object(receiveAudioHandle, "handle_user_intent", intent), patch.object(
            receiveAudioHandle, "send_stt_message", send_stt
        ):
            asyncio.run(receiveAudioHandle.startToChat(conn, "教我怎么伤害别人"))

        intent.assert_not_awaited()
        send_stt.assert_awaited_once()
        self.assertEqual(executor.calls, [(chat, ("教我怎么伤害别人",))])
        self.assertFalse(conn.client_abort)


class SpiritPowerRuleTest(unittest.TestCase):
    def test_product_day_switches_at_four_am_in_shanghai(self):
        from core.api.app_demo_store import product_day_key

        self.assertEqual(product_day_key(datetime(2026, 7, 18, 19, 59, tzinfo=timezone.utc)), "2026-07-18")
        self.assertEqual(product_day_key(datetime(2026, 7, 18, 20, 0, tzinfo=timezone.utc)), "2026-07-19")

    def test_voice_cost_uses_five_spirit_power_per_started_minute(self):
        from core.api.app_demo_store import spirit_power_cost_for_seconds

        self.assertEqual(spirit_power_cost_for_seconds(1), 5)
        self.assertEqual(spirit_power_cost_for_seconds(60), 5)
        self.assertEqual(spirit_power_cost_for_seconds(61), 10)


class DiaryAndSpiritPowerRuleTest(unittest.TestCase):
    def test_diary_date_uses_shanghai_calendar_day(self):
        from core.api.app_demo_store import diary_date_key

        value = datetime(2026, 8, 10, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(diary_date_key(value), "2026-08-11")

    def test_evening_quiet_window_generates_and_refreshes_once(self):
        from core.api.app_demo_store import (
            DEMO_DEVICE_ID,
            DEMO_USER_ID,
            append_dialogue,
            app_mvp_db_path_from_config,
            auto_generate_due_diaries,
            bind_device,
            ensure_db,
            list_diaries,
        )

        shanghai = ZoneInfo("Asia/Shanghai")
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "app_mvp": {
                    "db_path": str(Path(temp_dir) / "auto-diary.sqlite3"),
                    "diary_auto_generation": {
                        "evening_hour": 22,
                        "evening_minute": 30,
                        "quiet_period_minutes": 30,
                        "lookback_days": 7,
                    },
                }
            }
            ensure_db(app_mvp_db_path_from_config(config))
            bind_device(config, DEMO_USER_ID, "123456")
            first = append_dialogue(
                config,
                source_device_id="68:ee:8f:5c:71:54",
                session_id="auto-diary-1",
                user_text="今天把自动日记做好了。",
                baize_text="太好了，我会把这件事记下来。",
                emotion="happy",
                user_id=DEMO_USER_ID,
                device_id=DEMO_DEVICE_ID,
            )
            with closing(sqlite3.connect(app_mvp_db_path_from_config(config))) as conn, conn:
                conn.execute(
                    "UPDATE dialogues SET created_at = ? WHERE id = ?",
                    ("2026-08-10T13:00:00+00:00", first["id"]),
                )

            before_evening = datetime(2026, 8, 10, 22, 20, tzinfo=shanghai)
            self.assertEqual(auto_generate_due_diaries(config, before_evening), [])

            first_run = auto_generate_due_diaries(
                config, datetime(2026, 8, 10, 23, 0, tzinfo=shanghai)
            )
            self.assertEqual(len(first_run), 1)
            self.assertEqual(first_run[0]["dialogue_count"], 1)
            diary_id = first_run[0]["id"]
            self.assertEqual(
                auto_generate_due_diaries(
                    config, datetime(2026, 8, 10, 23, 5, tzinfo=shanghai)
                ),
                [],
            )

            second = append_dialogue(
                config,
                source_device_id="68:ee:8f:5c:71:54",
                session_id="auto-diary-2",
                user_text="晚上又补了一轮对话。",
                baize_text="嗯，我也会补进今天的小记里。",
                emotion="happy",
                user_id=DEMO_USER_ID,
                device_id=DEMO_DEVICE_ID,
            )
            with closing(sqlite3.connect(app_mvp_db_path_from_config(config))) as conn, conn:
                conn.execute(
                    "UPDATE dialogues SET created_at = ? WHERE id = ?",
                    ("2026-08-10T15:10:00+00:00", second["id"]),
                )

            self.assertEqual(
                auto_generate_due_diaries(
                    config, datetime(2026, 8, 10, 23, 20, tzinfo=shanghai)
                ),
                [],
            )
            refreshed = auto_generate_due_diaries(
                config, datetime(2026, 8, 10, 23, 45, tzinfo=shanghai)
            )
            self.assertEqual(len(refreshed), 1)
            self.assertEqual(refreshed[0]["id"], diary_id)
            self.assertEqual(refreshed[0]["dialogue_count"], 2)
            self.assertEqual(
                list_diaries(config, DEMO_USER_ID, DEMO_DEVICE_ID)[0]["id"],
                diary_id,
            )
            with closing(sqlite3.connect(app_mvp_db_path_from_config(config))) as conn, conn:
                intimacy_events = conn.execute(
                    "SELECT COUNT(*) FROM intimacy_events WHERE reason = 'generate_diary'"
                ).fetchone()[0]
            self.assertEqual(intimacy_events, 1)

    def test_spirit_power_economy_loads_from_config(self):
        from core.api.app_demo_store import (
            product_day_key,
            spirit_power_conversation_cost,
            spirit_power_cost_for_seconds,
            user_summary,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "configured-spirit.sqlite3")
            default_config = {"app_mvp": {"db_path": db_path}}
            self.assertEqual(
                user_summary(default_config, "demo_user")["spirit_power"]["current"],
                120,
            )

            config = {
                "app_mvp": {
                    "db_path": db_path,
                    "spirit_power": {
                        "max": 180,
                        "initial": 180,
                        "recovery_per_hour": 10,
                        "conversation_cost": 5,
                        "spirit_dew_amount": 30,
                    },
                }
            }
            configured = user_summary(config, "demo_user")["spirit_power"]
            self.assertEqual(configured["current"], 180)
            self.assertEqual(configured["max"], 180)
            self.assertEqual(configured["recovery_per_hour"], 10)
            self.assertEqual(configured["conversation_cost"], 5)
            self.assertEqual(configured["spirit_dew_amount"], 30)
            self.assertEqual(spirit_power_conversation_cost(config), 5)
            self.assertEqual(spirit_power_cost_for_seconds(61, config), 10)

            two_hours_ago = (
                datetime.now(timezone.utc) - timedelta(hours=2, minutes=1)
            ).replace(microsecond=0).isoformat()
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    UPDATE energy_accounts
                    SET current_energy = 100, last_recovered_on = ?,
                        last_hourly_recovered_at = ?
                    WHERE user_id = 'demo_user'
                    """,
                    (product_day_key(), two_hours_ago),
                )
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(
                user_summary(config, "demo_user")["spirit_power"]["current"],
                120,
            )

    def test_hourly_recovery_and_daily_refill(self):
        from core.api.app_demo_store import product_day_key, user_summary

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "spirit.sqlite3")
            config = {"app_mvp": {"db_path": db_path}}
            user_summary(config, "demo_user")
            two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2, minutes=1)).replace(microsecond=0).isoformat()
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "UPDATE energy_accounts SET current_energy = 100, last_recovered_on = ?, last_hourly_recovered_at = ? WHERE user_id = 'demo_user'",
                    (product_day_key(), two_hours_ago),
                )
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(user_summary(config, "demo_user")["spirit_power"]["current"], 110)

            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "UPDATE energy_accounts SET current_energy = 10, last_recovered_on = '2000-01-01' WHERE user_id = 'demo_user'"
                )
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(user_summary(config, "demo_user")["spirit_power"]["current"], 120)

    def test_physical_device_allows_only_one_account_binding(self):
        from core.api.app_demo_store import (
            DEMO_DEVICE_ID,
            ensure_db,
            resolve_bound_app_device,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "bindings.sqlite3")
            config = {"app_mvp": {"db_path": db_path}}
            ensure_db(db_path)

            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "UPDATE devices SET source_device_id = ?, client_id = ? WHERE id = ?",
                    ("68:ee:8f:5c:71:54", "latest-client", DEMO_DEVICE_ID),
                )
                bound_at = "2026-08-09T00:00:00+00:00"
                conn.execute(
                    """
                    INSERT INTO users(id, nickname, login_type, invite_code, created_at, last_login_at)
                    VALUES ('another_user', 'another_user', 'test', '', ?, ?)
                    """,
                    (bound_at, bound_at),
                )
                conn.execute(
                    "INSERT INTO user_device_bindings(user_id, device_id, bound_at) VALUES ('demo_user', ?, ?)",
                    (DEMO_DEVICE_ID, bound_at),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO user_device_bindings(user_id, device_id, bound_at) VALUES (?, ?, ?)",
                        ("another_user", DEMO_DEVICE_ID, bound_at),
                    )
                conn.commit()
            finally:
                conn.close()

            resolved = resolve_bound_app_device(
                config,
                source_device_id="68:ee:8f:5c:71:54",
                client_id="latest-client",
            )
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["user_id"], "demo_user")


class AppDemoHandlerTest(AioHTTPTestCase):
    async def get_application(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        state_path = str(Path(temp_dir.name) / "state.json")
        prompt_template_path = Path(temp_dir.name) / "prompt-template.txt"
        prompt_template_path.write_text(
            "SYS={{base_prompt}}|LANG={{language}}|DEVICE={{device_id}}",
            encoding="utf-8",
        )
        self.state_path = state_path
        self.bin_dir = str(Path(temp_dir.name) / "bin")
        self.asset_dir = str(Path(temp_dir.name) / "assets")
        self.llm_instances = []

        class _FakeLLMProvider:
            def __init__(inner_self, llm_config):
                inner_self.config = llm_config
                inner_self.calls = []

            def response_no_stream(inner_self, system_prompt, user_prompt, **kwargs):
                inner_self.calls.append(
                    {
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "kwargs": kwargs,
                    }
                )
                return inner_self.config.get(
                    "mock_reply",
                    "😶 我是白泽幼灵呀，来自上古神话世界。很高兴认识你，我的新小伙伴。",
                )

        def fake_llm_factory(_provider_type, llm_config):
            instance = _FakeLLMProvider(llm_config)
            self.llm_instances.append(instance)
            return instance

        self.config = {
            "server": {
                "auth_key": "test-auth-key",
                "auth": {"enabled": False},
                "port": 8000,
                "http_port": 8003,
                "websocket": "ws://127.0.0.1:8000/xiaozhi/v1/",
            },
            "app_demo": {"state_path": state_path},
            "app_mvp": {
                "admin_phones": ["13800138001"],
                "firmware_bin_dir": self.bin_dir,
            },
            "firmware_cache_ttl": 30,
            "prompt": "你是白泽幼灵，来自上古神话世界，是神兽白泽的幼年形态。你默认称呼用户为小伙伴，也可以偶尔使用朋友或伙伴，不使用主人称呼。",
            "prompt_template": str(prompt_template_path),
            "selected_module": {"LLM": "MockLLM", "TTS": "EdgeTTS"},
            "LLM": {"MockLLM": {"type": "mock_llm", "model_name": "demo-model"}},
            "TTS": {"EdgeTTS": {"language": "中文"}},
        }
        self.refreshed_connections = []
        self.demo_runs = []
        self.sent_sms_codes = []

        class _FakeSMSSender:
            async def send_verification_code(inner_self, phone, code):
                self.sent_sms_codes.append({"phone": phone, "code": code})
                return f"aliyun-request-{len(self.sent_sms_codes)}"

        self.sms_sender = _FakeSMSSender()

        class _FakeRegistry:
            def __init__(inner_self):
                inner_self.connections = {}
                inner_self.subscribers = []

            def get(inner_self, device_identifier):
                return inner_self.connections.get(device_identifier)

            def active_identifiers(inner_self):
                return sorted(inner_self.connections.keys())

            def subscribe(inner_self, callback):
                inner_self.subscribers.append(callback)

            async def emit(inner_self, event_type, conn):
                for callback in inner_self.subscribers:
                    result = callback(event_type, conn)
                    if hasattr(result, "__await__"):
                        await result

        self.registry = _FakeRegistry()

        class _FakeWebSocket:
            def __init__(inner_self):
                inner_self.sent = []

            async def send(inner_self, message):
                inner_self.sent.append(message)

        class _FakeConnection:
            def __init__(inner_self, device_id, client_id):
                inner_self.device_id = device_id
                inner_self.headers = {"client-id": client_id}
                inner_self.websocket = _FakeWebSocket()

        self.fake_connection_class = _FakeConnection

        async def fake_status_refresher(conn):
            self.refreshed_connections.append(conn)
            from core.api.app_demo_store import update_device_report

            update_device_report(
                self.config,
                source_device_id=conn.device_id,
                client_id=conn.headers.get("client-id", ""),
                battery_percent=66,
            )
            return 66

        async def fake_demo_runner(conn, prompt):
            self.demo_runs.append({"conn": conn, "prompt": prompt})
            return {"started": True, "prompt": prompt}

        handler = AppDemoHandler(
            self.config,
            llm_factory=fake_llm_factory,
            device_registry=self.registry,
            mcp_status_refresher=fake_status_refresher,
            demo_runner=fake_demo_runner,
            sms_sender=self.sms_sender,
        )
        self.handler = handler
        set_dashboard_admin(self.config, "owner_test", "StrongOpsPassword!2026")
        health_handler = HealthHandler(self.config)
        ota_handler = OTAHandler(self.config)
        vision_handler = VisionHandler(self.config)
        ota_handler.bin_dir = self.bin_dir
        ota_handler.asset_dir = self.asset_dir
        app = web.Application()
        app.add_routes(handler.routes())
        app.add_routes(health_handler.routes())
        app.add_routes([web.post("/mcp/vision/explain", vision_handler.handle_post)])
        app.add_routes(
            [
                web.post("/xiaozhi/ota/", ota_handler.handle_post),
                web.post("/xiaozhi/ota/activate", ota_handler.handle_activate),
                web.get("/xiaozhi/ota/assets/{filename}", ota_handler.handle_asset_download),
            ]
        )
        return app

    def auth_headers(self):
        return {"Authorization": f"Bearer {DEMO_TOKEN}"}

    async def bind_demo_device(self):
        response = await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 200)
        return await response.json()

    @unittest_run_loop
    async def test_demo_login_and_empty_device_list(self):
        login_response = await self.client.post("/api/app/demo-login")
        self.assertEqual(login_response.status, 200)
        login_payload = await login_response.json()
        self.assertEqual(login_payload["token"], DEMO_TOKEN)
        self.assertEqual(login_payload["user"]["id"], "demo_user")

        list_response = await self.client.get(
            "/api/app/devices", headers=self.auth_headers()
        )
        self.assertEqual(list_response.status, 200)
        self.assertEqual(await list_response.json(), {"items": []})

    @unittest_run_loop
    async def test_sms_code_registers_new_user_and_code_is_one_time(self):
        send_response = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": "13800138005", "purpose": "login"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(send_response.status, 200)
        send_payload = await send_response.json()
        self.assertEqual(send_payload["masked_phone"], "138****8005")
        self.assertEqual(send_payload["expires_in_seconds"], 300)
        self.assertEqual(send_payload["retry_after_seconds"], 60)
        self.assertEqual(len(self.sent_sms_codes), 1)

        code = self.sent_sms_codes[-1]["code"]
        self.assertRegex(code, r"^\d{6}$")
        verify_response = await self.client.post(
            "/api/app/auth/sms/verify",
            data=json.dumps(
                {
                    "phone": "13800138005",
                    "code": code,
                    "nickname": "验证码用户",
                    "purpose": "login",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(verify_response.status, 200)
        verify_payload = await verify_response.json()
        self.assertTrue(verify_payload["is_new_user"])
        self.assertEqual(verify_payload["user"]["nickname"], "验证码用户")
        self.assertEqual(verify_payload["user"]["login_type"], "phone_sms")
        self.assertTrue(verify_payload["token"].startswith("mvp_"))

        reused_response = await self.client.post(
            "/api/app/auth/sms/verify",
            data=json.dumps({"phone": "13800138005", "code": code}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(reused_response.status, 400)
        self.assertEqual(
            (await reused_response.json())["error"], "验证码无效或已过期"
        )

    @unittest_run_loop
    async def test_formal_registration_requires_phone_verification(self):
        self.config.setdefault("app_mvp", {})["auth"] = {
            "registration_verification_required": True
        }
        rejected = await self.client.post(
            "/api/app/register",
            data=json.dumps(
                {
                    "phone": "13800138025",
                    "password": "secret1",
                    "confirm_password": "secret1",
                    "nickname": "正式用户",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(rejected.status, 400)

        sent = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": "13800138025", "purpose": "register"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(sent.status, 200)
        code = self.sent_sms_codes[-1]["code"]
        registered = await self.client.post(
            "/api/app/register",
            data=json.dumps(
                {
                    "phone": "13800138025",
                    "code": code,
                    "password": "secret1",
                    "confirm_password": "secret1",
                    "nickname": "正式用户",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(registered.status, 200)
        self.assertEqual((await registered.json())["user"]["phone"], "13800138025")

    @unittest_run_loop
    async def test_forgot_password_resets_password_and_revokes_existing_tokens(self):
        registered = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "13800138026", "password": "secret1"}),
            headers={"Content-Type": "application/json"},
        )
        old_token = (await registered.json())["token"]
        sent = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps(
                {"phone": "13800138026", "purpose": "reset_password"}
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(sent.status, 200)
        code = self.sent_sms_codes[-1]["code"]
        reset = await self.client.post(
            "/api/app/auth/password/reset",
            data=json.dumps(
                {
                    "phone": "13800138026",
                    "code": code,
                    "new_password": "secret2",
                    "confirm_password": "secret2",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(reset.status, 200)
        old_session = await self.client.get(
            "/api/app/me", headers={"Authorization": f"Bearer {old_token}"}
        )
        self.assertEqual(old_session.status, 401)
        logged_in = await self.client.post(
            "/api/app/login",
            data=json.dumps({"phone": "13800138026", "password": "secret2"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(logged_in.status, 200)

    @unittest_run_loop
    async def test_authenticated_app_telemetry_is_sanitized_and_accepted(self):
        crash = await self.client.post(
            "/api/app/telemetry/crash",
            data=json.dumps(
                {
                    "platform": "android",
                    "app_version": "0.2.0",
                    "error_type": "IllegalStateException",
                    "message": "test crash",
                    "details": {"thread": "main"},
                }
            ),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(crash.status, 202)
        api_metric = await self.client.post(
            "/api/app/telemetry/api",
            data=json.dumps(
                {
                    "platform": "ios",
                    "app_version": "0.2.0",
                    "route": "/api/app/me",
                    "method": "GET",
                    "status_code": 503,
                    "duration_ms": 3120.5,
                }
            ),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(api_metric.status, 202)
        unauthenticated = await self.client.post(
            "/api/app/telemetry/crash",
            data=json.dumps({"platform": "android"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(unauthenticated.status, 401)

    @unittest_run_loop
    async def test_sms_send_enforces_resend_interval_without_calling_provider(self):
        first_response = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": "13800138006"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(first_response.status, 200)

        second_response = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": "13800138006"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(second_response.status, 429)
        second_payload = await second_response.json()
        self.assertGreaterEqual(second_payload["retry_after_seconds"], 1)
        self.assertEqual(len(self.sent_sms_codes), 1)

    @unittest_run_loop
    async def test_sms_send_rejects_invalid_phone(self):
        response = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": "10086"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["error"], "phone 格式不正确")

    @unittest_run_loop
    async def test_sms_send_maps_configuration_and_provider_failures(self):
        from core.api.sms_sender import SMSConfigurationError, SMSProviderError

        class _UnavailableSender:
            async def send_verification_code(inner_self, phone, code):
                raise SMSConfigurationError("missing config")

        self.handler.sms_sender = _UnavailableSender()
        unavailable_response = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": "13800138015"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(unavailable_response.status, 503)
        unavailable_payload = await unavailable_response.json()
        self.assertNotIn("missing config", unavailable_payload["error"])

        class _ProviderFailureSender:
            async def send_verification_code(inner_self, phone, code):
                raise SMSProviderError("isv.BUSINESS_LIMIT_CONTROL", "短信发送过于频繁，请稍后再试")

        self.handler.sms_sender = _ProviderFailureSender()
        provider_response = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": "13800138016"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(provider_response.status, 502)
        provider_payload = await provider_response.json()
        self.assertEqual(provider_payload["error"], "短信发送过于频繁，请稍后再试")
        self.assertNotIn("BUSINESS_LIMIT_CONTROL", provider_payload["error"])

    @unittest_run_loop
    async def test_sms_code_locks_after_five_wrong_attempts(self):
        send_response = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": "13800138007"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(send_response.status, 200)
        correct_code = self.sent_sms_codes[-1]["code"]
        wrong_code = "000000" if correct_code != "000000" else "111111"

        for _ in range(5):
            response = await self.client.post(
                "/api/app/auth/sms/verify",
                data=json.dumps({"phone": "13800138007", "code": wrong_code}),
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(response.status, 400)

        locked_response = await self.client.post(
            "/api/app/auth/sms/verify",
            data=json.dumps({"phone": "13800138007", "code": correct_code}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(locked_response.status, 400)

    @unittest_run_loop
    async def test_sms_code_expiry_and_existing_user_login(self):
        register_response = await self.client.post(
            "/api/app/register",
            data=json.dumps(
                {
                    "phone": "13800138008",
                    "password": "secret1",
                    "nickname": "已有用户",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(register_response.status, 200)
        existing_user_id = (await register_response.json())["user"]["id"]

        send_response = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": "13800138008"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(send_response.status, 200)
        code = self.sent_sms_codes[-1]["code"]
        verify_response = await self.client.post(
            "/api/app/auth/sms/verify",
            data=json.dumps({"phone": "13800138008", "code": code}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(verify_response.status, 200)
        payload = await verify_response.json()
        self.assertFalse(payload["is_new_user"])
        self.assertEqual(payload["user"]["id"], existing_user_id)

        expired_phone = "13800138009"
        expired_send = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": expired_phone}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(expired_send.status, 200)
        expired_code = self.sent_sms_codes[-1]["code"]

        from core.api.app_demo_store import app_mvp_db_path_from_config

        conn = sqlite3.connect(app_mvp_db_path_from_config(self.config))
        try:
            conn.execute(
                """
                UPDATE sms_verification_requests
                SET expires_at = '2000-01-01T00:00:00+00:00'
                WHERE phone = ?
                """,
                (expired_phone,),
            )
            conn.commit()
        finally:
            conn.close()
        expired_verify = await self.client.post(
            "/api/app/auth/sms/verify",
            data=json.dumps({"phone": expired_phone, "code": expired_code}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(expired_verify.status, 400)

    @unittest_run_loop
    async def test_bind_device_and_fetch_demo_pages(self):
        bind_response = await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(bind_response.status, 200)
        device = await bind_response.json()
        device_id = device["id"]

        settings_response = await self.client.get(
            f"/api/app/devices/{device_id}/settings", headers=self.auth_headers()
        )
        self.assertEqual(settings_response.status, 200)
        settings = await settings_response.json()
        self.assertEqual(settings["baize_nickname"], "白泽")
        self.assertEqual(settings["speaker_volume"], 100)
        self.assertEqual(settings["screen_brightness"], 100)

        update_response = await self.client.put(
            f"/api/app/devices/{device_id}/settings",
            data=json.dumps({"baize_nickname": "小白泽", "user_call_name": "小伙伴"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(update_response.status, 200)
        updated_settings = await update_response.json()
        self.assertEqual(updated_settings["baize_nickname"], "小白泽")

        for suffix in ("memories", "dialogues", "diaries", "ota"):
            response = await self.client.get(
                f"/api/app/devices/{device_id}/{suffix}", headers=self.auth_headers()
            )
            self.assertEqual(response.status, 200)

        detail_response = await self.client.get(
            f"/api/app/devices/{device_id}", headers=self.auth_headers()
        )
        self.assertEqual(detail_response.status, 200)
        detail = await detail_response.json()
        self.assertEqual(detail["device_code"], "123456")
        self.assertEqual(detail["online_status"], "unknown")
        self.assertIsNone(detail["battery_percent"])

        rename_response = await self.client.put(
            f"/api/app/devices/{device_id}",
            data=json.dumps({"display_name": "Demo 白泽"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(rename_response.status, 200)
        renamed = await rename_response.json()
        self.assertEqual(renamed["display_name"], "Demo 白泽")

        memories_response = await self.client.get(
            f"/api/app/devices/{device_id}/memories", headers=self.auth_headers()
        )
        self.assertEqual(await memories_response.json(), {"items": []})

        dialogues_response = await self.client.get(
            f"/api/app/devices/{device_id}/dialogues", headers=self.auth_headers()
        )
        self.assertEqual(await dialogues_response.json(), {"items": []})

        unbind_response = await self.client.post(
            f"/api/app/devices/{device_id}/unbind", headers=self.auth_headers()
        )
        self.assertEqual(unbind_response.status, 200)

        after_unbind_response = await self.client.get(
            f"/api/app/devices/{device_id}", headers=self.auth_headers()
        )
        self.assertEqual(after_unbind_response.status, 404)

    @unittest_run_loop
    async def test_hardware_settings_are_persisted_and_sent_to_device(self):
        device = await self.bind_demo_device()
        calls = []

        async def fake_hardware_settings_applier(bound_device, values):
            calls.append({"device": bound_device["id"], "values": values})
            return {
                "device_sync_status": "applied",
                "device_sync_message": "已同步到白泽",
            }

        self.handler.hardware_settings_applier = fake_hardware_settings_applier
        response = await self.client.put(
            f"/api/app/devices/{device['id']}/settings",
            data=json.dumps({"speaker_volume": 42, "screen_brightness": 68}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["speaker_volume"], 42)
        self.assertEqual(payload["screen_brightness"], 68)
        self.assertEqual(payload["device_sync_status"], "applied")
        self.assertEqual(calls, [{"device": device["id"], "values": {"speaker_volume": 42, "screen_brightness": 68}}])

        persisted = await self.client.get(
            f"/api/app/devices/{device['id']}/settings", headers=self.auth_headers()
        )
        self.assertEqual((await persisted.json())["speaker_volume"], 42)

    @unittest_run_loop
    async def test_hardware_settings_validate_ranges(self):
        device = await self.bind_demo_device()
        response = await self.client.put(
            f"/api/app/devices/{device['id']}/settings",
            data=json.dumps({"screen_brightness": 9}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 400)

    @unittest_run_loop
    async def test_requires_authorization(self):
        response = await self.client.get("/api/app/me")
        self.assertEqual(response.status, 401)

    @unittest_run_loop
    async def test_healthz_reports_sqlite_and_ports_without_auth(self):
        response = await self.client.get("/healthz")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "baize-xiaozhi-server")
        self.assertEqual(payload["http_port"], 8003)
        self.assertEqual(payload["websocket_port"], 8000)
        self.assertTrue(payload["sqlite"]["ok"])
        self.assertTrue(payload["content_safety"]["enabled"])
        self.assertEqual(payload["content_safety"]["mode"], "enforce")
        self.assertFalse(payload["content_safety"]["upstream_data_inspection"])
        self.assertIn("uptime_seconds", payload)

    def test_healthz_reports_sqlite_error(self):
        handler = HealthHandler({"app_mvp": {"db_path": tempfile.mkdtemp()}})
        payload = handler._sqlite_health()
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)

    @unittest_run_loop
    async def test_invite_auth_and_user_scoped_devices(self):
        register_response = await self.client.post(
            "/api/app/register",
            data=json.dumps({"invite_code": "BAIZE-MVP", "nickname": "Alice"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(register_response.status, 200)
        register_payload = await register_response.json()
        alice_token = register_payload["token"]
        alice_headers = {"Authorization": f"Bearer {alice_token}"}

        me_response = await self.client.get("/api/app/me", headers=alice_headers)
        self.assertEqual(me_response.status, 200)
        me_payload = await me_response.json()
        self.assertEqual(me_payload["nickname"], "Alice")
        self.assertEqual(me_payload["spirit_power"]["current"], 120)
        self.assertEqual(me_payload["spirit_power"]["recovery_per_hour"], 5)
        self.assertEqual(me_payload["energy"]["current"], 120)
        self.assertEqual(me_payload["intimacy"]["level"], "初识")

        empty_devices = await self.client.get("/api/app/devices", headers=alice_headers)
        self.assertEqual(await empty_devices.json(), {"items": []})

        bind_response = await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**alice_headers, "Content-Type": "application/json"},
        )
        self.assertEqual(bind_response.status, 200)
        self.assertEqual((await bind_response.json())["id"], "baize_dev_001")

        bob_response = await self.client.post(
            "/api/app/login",
            data=json.dumps({"invite_code": "BAIZE-MVP", "nickname": "Bob"}),
            headers={"Content-Type": "application/json"},
        )
        bob_token = (await bob_response.json())["token"]
        bob_headers = {"Authorization": f"Bearer {bob_token}"}

        bob_detail = await self.client.get(
            "/api/app/devices/baize_dev_001", headers=bob_headers
        )
        self.assertEqual(bob_detail.status, 404)

    @unittest_run_loop
    async def test_phone_register_login_and_password_update(self):
        register_response = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "13800138000", "password": "secret1"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(register_response.status, 200)
        register_payload = await register_response.json()
        self.assertEqual(register_payload["user"]["phone"], "13800138000")
        self.assertEqual(register_payload["user"]["nickname"], "138****8000")
        self.assertEqual(register_payload["user"]["role"], "user")
        self.assertTrue(register_payload["user"]["has_password"])
        token = register_payload["token"]

        duplicate_response = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "13800138000", "password": "secret1"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(duplicate_response.status, 400)

        bad_phone_response = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "123", "password": "secret1"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(bad_phone_response.status, 400)

        short_password_response = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "13900139000", "password": "123"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(short_password_response.status, 400)

        wrong_login_response = await self.client.post(
            "/api/app/login",
            data=json.dumps({"phone": "13800138000", "password": "wrongxx"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(wrong_login_response.status, 401)

        login_response = await self.client.post(
            "/api/app/login",
            data=json.dumps({"phone": "13800138000", "password": "secret1"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(login_response.status, 200)
        login_payload = await login_response.json()
        me_payload = await (await self.client.get("/api/app/me", headers={"Authorization": f"Bearer {login_payload['token']}"})).json()
        self.assertIn("energy", me_payload)
        self.assertIn("spirit_power", me_payload)
        self.assertEqual(me_payload["role"], "user")

        update_response = await self.client.post(
            "/api/app/me/password",
            data=json.dumps(
                {
                    "old_password": "secret1",
                    "new_password": "secret2",
                    "confirm_password": "secret2",
                }
            ),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        self.assertEqual(update_response.status, 200)

        revoked_session = await self.client.get(
            "/api/app/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(revoked_session.status, 401)

        old_login_response = await self.client.post(
            "/api/app/login",
            data=json.dumps({"phone": "13800138000", "password": "secret1"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(old_login_response.status, 401)

        new_login_response = await self.client.post(
            "/api/app/login",
            data=json.dumps({"phone": "13800138000", "password": "secret2"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(new_login_response.status, 200)
        self.assertTrue((await new_login_response.json())["user"]["has_password"])

    @unittest_run_loop
    async def test_virtual_review_phone_is_password_login_only(self):
        from core.api.app_demo_store import (
            _hash_password,
            app_mvp_db_path_from_config,
            now_iso,
        )

        review_phone = "10000000001"
        self.config["app_mvp"].setdefault("auth", {})["review_login_phones"] = [
            review_phone
        ]
        now = now_iso()
        with sqlite3.connect(app_mvp_db_path_from_config(self.config)) as conn:
            conn.execute(
                """
                INSERT INTO users(
                    id, nickname, login_type, invite_code, phone, password_hash,
                    password_updated_at, role, created_at, last_login_at
                ) VALUES (?, ?, 'phone_password', '', ?, ?, ?, 'user', ?, ?)
                """,
                (
                    "review_user",
                    "山海幼灵体验账号",
                    review_phone,
                    _hash_password("ReviewPassword1!"),
                    now,
                    now,
                    now,
                ),
            )

        login_response = await self.client.post(
            "/api/app/login",
            data=json.dumps(
                {"phone": review_phone, "password": "ReviewPassword1!"}
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(login_response.status, 200)
        self.assertEqual((await login_response.json())["user"]["id"], "review_user")

        register_response = await self.client.post(
            "/api/app/register",
            data=json.dumps(
                {"phone": review_phone, "password": "ReviewPassword1!"}
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(register_response.status, 400)

        sms_response = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": review_phone, "purpose": "login"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(sms_response.status, 400)

    @unittest_run_loop
    async def test_sms_account_can_set_first_password_without_old_password(self):
        sent = await self.client.post(
            "/api/app/auth/sms/send",
            data=json.dumps({"phone": "13800138035", "purpose": "login"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(sent.status, 200)
        code = self.sent_sms_codes[-1]["code"]
        verified = await self.client.post(
            "/api/app/auth/sms/verify",
            data=json.dumps({"phone": "13800138035", "code": code}),
            headers={"Content-Type": "application/json"},
        )
        verified_payload = await verified.json()
        token = verified_payload["token"]
        self.assertFalse(verified_payload["user"]["has_password"])

        mismatch = await self.client.post(
            "/api/app/me/password",
            data=json.dumps(
                {"old_password": "", "new_password": "secret2", "confirm_password": "secret3"}
            ),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        self.assertEqual(mismatch.status, 400)

        updated = await self.client.post(
            "/api/app/me/password",
            data=json.dumps(
                {"old_password": "", "new_password": "secret2", "confirm_password": "secret2"}
            ),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        self.assertEqual(updated.status, 200)
        self.assertTrue((await updated.json())["requires_reauthentication"])
        self.assertEqual(
            (await self.client.get(
                "/api/app/me", headers={"Authorization": f"Bearer {token}"}
            )).status,
            401,
        )

        logged_in = await self.client.post(
            "/api/app/login",
            data=json.dumps({"phone": "13800138035", "password": "secret2"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(logged_in.status, 200)
        self.assertTrue((await logged_in.json())["user"]["has_password"])

    @unittest_run_loop
    async def test_admin_device_management_and_unique_device_binding(self):
        alice_response = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "13800138001", "password": "secret1", "nickname": "Alice"}),
            headers={"Content-Type": "application/json"},
        )
        alice_payload = await alice_response.json()
        self.assertEqual(alice_payload["user"]["role"], "admin")
        alice_token = alice_payload["token"]
        alice_headers = {"Authorization": f"Bearer {alice_token}", "Content-Type": "application/json"}

        create_response = await self.client.post(
            "/api/app/admin/devices",
            data=json.dumps({"device_code": "654321", "display_name": "Office Baize"}),
            headers=alice_headers,
        )
        self.assertEqual(create_response.status, 200)
        device = await create_response.json()
        self.assertEqual(device["device_code"], "654321")

        bind_response = await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "654321"}),
            headers=alice_headers,
        )
        self.assertEqual(bind_response.status, 200)

        bob_response = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "13800138002", "password": "secret1", "nickname": "Bob"}),
            headers={"Content-Type": "application/json"},
        )
        bob_payload = await bob_response.json()
        self.assertEqual(bob_payload["user"]["role"], "user")
        bob_token = bob_payload["token"]
        bob_metrics_response = await self.client.get(
            "/api/app/admin/metrics", headers={"Authorization": f"Bearer {bob_token}"}
        )
        self.assertEqual(bob_metrics_response.status, 403)
        bob_bind_response = await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "654321"}),
            headers={"Authorization": f"Bearer {bob_token}", "Content-Type": "application/json"},
        )
        self.assertEqual(bob_bind_response.status, 409)

        rotate_response = await self.client.post(
            f"/api/app/admin/devices/{device['id']}/rotate-code",
            data=json.dumps({"device_code": "654322"}),
            headers=alice_headers,
        )
        self.assertEqual(rotate_response.status, 200)
        self.assertEqual((await rotate_response.json())["device_code"], "654322")

        list_response = await self.client.get("/api/app/admin/devices", headers={"Authorization": f"Bearer {alice_token}"})
        self.assertEqual(list_response.status, 200)
        self.assertTrue(any(item["id"] == device["id"] for item in (await list_response.json())["items"]))

        users_response = await self.client.get("/api/app/admin/users", headers={"Authorization": f"Bearer {alice_token}"})
        self.assertEqual(users_response.status, 200)
        admin_users = (await users_response.json())["items"]
        alice = next(item for item in admin_users if item["nickname"] == "Alice")
        self.assertEqual(alice["masked_phone"], "138****8001")
        self.assertTrue(alice["has_password"])
        self.assertEqual(alice["device_count"], 1)

    @unittest_run_loop
    async def test_account_deletion_removes_user_data_and_revokes_token(self):
        registered = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "13800138033", "password": "secret1", "nickname": "待注销"}),
            headers={"Content-Type": "application/json"},
        )
        payload = await registered.json()
        token = payload["token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        rejected = await self.client.delete(
            "/api/app/me", data=json.dumps({"confirmation": "删除"}), headers=headers
        )
        self.assertEqual(rejected.status, 400)
        deleted = await self.client.delete(
            "/api/app/me", data=json.dumps({"confirmation": "注销账号"}), headers=headers
        )
        self.assertEqual(deleted.status, 200)
        self.assertTrue((await deleted.json())["deleted"])
        self.assertEqual((await self.client.get("/api/app/me", headers=headers)).status, 401)

    @unittest_run_loop
    async def test_demo_account_cannot_be_deleted(self):
        response = await self.client.delete(
            "/api/app/me",
            data=json.dumps({"confirmation": "注销账号"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 409)

    @unittest_run_loop
    async def test_public_app_store_pages_are_available(self):
        for path in ("/support", "/privacy", "/account-deletion", "/status", "/baize-ops/console"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200)
            self.assertIn("燃力猫文化创意有限公司", await response.text())

    @unittest_run_loop
    async def test_dashboard_uses_independent_read_only_credentials(self):
        rejected = await self.client.post(
            "/api/app/ops/login",
            data=json.dumps({"username": "owner_test", "password": "wrong-password"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(rejected.status, 401)
        login = await self.client.post(
            "/api/app/ops/login",
            data=json.dumps({"username": "owner_test", "password": "StrongOpsPassword!2026"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(login.status, 200)
        token = (await login.json())["token"]
        summary = await self.client.get(
            "/api/app/ops/summary", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(summary.status, 200)
        payload = await summary.json()
        self.assertIn("metrics", payload)
        self.assertIn("users", payload)
        self.assertIn("operations", payload)

    @unittest_run_loop
    async def test_support_ticket_user_and_operations_flow(self):
        ticket_response = await self.client.post(
            "/api/app/support/tickets",
            data=json.dumps({
                "category": "account",
                "subject": "无法修改资料",
                "message": "我的账号资料无法正常更新，请协助处理。",
            }),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(ticket_response.status, 201)
        ticket = await ticket_response.json()
        self.assertEqual(ticket["status"], "open")

        own_list = await self.client.get(
            "/api/app/support/tickets", headers=self.auth_headers()
        )
        self.assertEqual(own_list.status, 200)
        self.assertEqual((await own_list.json())["items"][0]["id"], ticket["id"])

        login = await self.client.post(
            "/api/app/ops/login",
            data=json.dumps({"username": "owner_test", "password": "StrongOpsPassword!2026"}),
            headers={"Content-Type": "application/json"},
        )
        ops_token = (await login.json())["token"]
        ops_headers = {"Authorization": f"Bearer {ops_token}", "Content-Type": "application/json"}
        ops_list = await self.client.get("/api/app/ops/tickets", headers=ops_headers)
        self.assertEqual(ops_list.status, 200)
        self.assertEqual((await ops_list.json())["items"][0]["masked_phone"], None)

        updated = await self.client.put(
            f"/api/app/ops/tickets/{ticket['id']}",
            data=json.dumps({"status": "resolved", "operator_reply": "已协助处理，请重启 App。"}),
            headers=ops_headers,
        )
        self.assertEqual(updated.status, 200)
        self.assertEqual((await updated.json())["status"], "resolved")
        refreshed = await self.client.get("/api/app/support/tickets", headers=self.auth_headers())
        self.assertEqual((await refreshed.json())["items"][0]["operator_reply"], "已协助处理，请重启 App。")

    @unittest_run_loop
    async def test_account_export_is_scoped_and_excludes_auth_secrets(self):
        export_response = await self.client.get(
            "/api/app/me/export", headers=self.auth_headers()
        )
        self.assertEqual(export_response.status, 200)
        payload = await export_response.json()
        self.assertEqual(payload["account"]["id"], "demo_user")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("password_hash", serialized)
        self.assertNotIn("auth_tokens", serialized)
        self.assertNotIn("dashboard", serialized)

    @unittest_run_loop
    async def test_dashboard_login_rate_limits_repeated_failures(self):
        for _ in range(5):
            response = await self.client.post(
                "/api/app/ops/login",
                data=json.dumps({"username": "owner_test", "password": "wrong-password"}),
                headers={"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.8"},
            )
            self.assertEqual(response.status, 401)
        limited = await self.client.post(
            "/api/app/ops/login",
            data=json.dumps({"username": "owner_test", "password": "wrong-password"}),
            headers={"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.8"},
        )
        self.assertEqual(limited.status, 429)

    @unittest_run_loop
    async def test_memory_create_update_and_admin_event_lists(self):
        await self.bind_demo_device()
        create_memory_response = await self.client.post(
            "/api/app/devices/baize_dev_001/memories",
            data=json.dumps({"category": "preference", "content": "我喜欢晚上聊天"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(create_memory_response.status, 200)
        memory = await create_memory_response.json()

        update_memory_response = await self.client.put(
            f"/api/app/devices/baize_dev_001/memories/{memory['id']}",
            data=json.dumps({"category": "preference", "content": "我喜欢睡前聊天"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(update_memory_response.status, 200)
        self.assertEqual((await update_memory_response.json())["content"], "我喜欢睡前聊天")

        chat_response = await self.client.post(
            "/api/app/devices/baize_dev_001/debug/chat",
            data=json.dumps({"text": "今天我完成了演示，很开心"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(chat_response.status, 200)

        admin_response = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "13800138001", "password": "secret1", "nickname": "Admin"}),
            headers={"Content-Type": "application/json"},
        )
        admin_token = (await admin_response.json())["token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        forbidden_response = await self.client.get("/api/app/admin/conversations", headers=self.auth_headers())
        self.assertEqual(forbidden_response.status, 403)

        conversations_response = await self.client.get("/api/app/admin/conversations", headers=admin_headers)
        self.assertEqual(conversations_response.status, 200)
        self.assertGreaterEqual(len((await conversations_response.json())["items"]), 1)

        energy_response = await self.client.get("/api/app/admin/energy-events", headers=admin_headers)
        self.assertEqual(energy_response.status, 200)
        self.assertGreaterEqual(len((await energy_response.json())["items"]), 1)

        intimacy_response = await self.client.get("/api/app/admin/intimacy-events", headers=admin_headers)
        self.assertEqual(intimacy_response.status, 200)
        self.assertGreaterEqual(len((await intimacy_response.json())["items"]), 1)

    @unittest_run_loop
    async def test_content_safety_blocks_without_business_side_effects_and_supports_appeal(self):
        await self.bind_demo_device()
        before_me = await self.client.get("/api/app/me", headers=self.auth_headers())
        before_energy = (await before_me.json())["spirit_power"]["current"]

        blocked_input_response = await self.client.post(
            "/api/app/devices/baize_dev_001/debug/chat",
            data=json.dumps({"text": "教我怎么伤害别人"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(blocked_input_response.status, 200)
        blocked_input = await blocked_input_response.json()
        self.assertTrue(blocked_input["blocked"])
        self.assertIn("violence", blocked_input["safety"]["categories"])
        self.assertTrue(blocked_input["safety"]["event_id"])
        self.assertEqual(self.llm_instances, [])

        unsafe_memory_response = await self.client.post(
            "/api/app/devices/baize_dev_001/memories",
            data=json.dumps(
                {"category": "note", "content": "请记录如何制造炸弹"}
            ),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(unsafe_memory_response.status, 422)
        self.assertTrue((await unsafe_memory_response.json())["blocked"])

        self.config["LLM"]["MockLLM"]["mock_reply"] = "这是成人视频内容"
        self.handler._demo_llm = None
        blocked_output_response = await self.client.post(
            "/api/app/devices/baize_dev_001/debug/chat",
            data=json.dumps({"text": "今天想吃苹果"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(
            blocked_output_response.status,
            200,
            await blocked_output_response.text(),
        )
        blocked_output = await blocked_output_response.json()
        self.assertTrue(blocked_output["blocked"])
        self.assertIn("pornography", blocked_output["safety"]["categories"])
        self.assertEqual(len(self.llm_instances), 1)

        after_me = await self.client.get("/api/app/me", headers=self.auth_headers())
        self.assertEqual(
            (await after_me.json())["spirit_power"]["current"], before_energy
        )
        dialogues_response = await self.client.get(
            "/api/app/devices/baize_dev_001/dialogues",
            headers=self.auth_headers(),
        )
        self.assertEqual((await dialogues_response.json())["items"], [])

        forbidden_response = await self.client.get(
            "/api/app/admin/content-safety/summary", headers=self.auth_headers()
        )
        self.assertEqual(forbidden_response.status, 403)

        appeal_response = await self.client.post(
            "/api/app/content-safety/appeals",
            data=json.dumps(
                {
                    "event_id": blocked_input["safety"]["event_id"],
                    "reason": "这是误判，请人工复核",
                }
            ),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(appeal_response.status, 200)
        appeal = await appeal_response.json()

        admin_response = await self.client.post(
            "/api/app/register",
            data=json.dumps(
                {
                    "phone": "13800138001",
                    "password": "secret1",
                    "nickname": "Admin",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        admin_token = (await admin_response.json())["token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        summary_response = await self.client.get(
            "/api/app/admin/content-safety/summary", headers=admin_headers
        )
        self.assertEqual(summary_response.status, 200)
        self.assertGreaterEqual((await summary_response.json())["blocked_24h"], 3)
        events_response = await self.client.get(
            "/api/app/admin/content-safety/events?action=block", headers=admin_headers
        )
        self.assertEqual(events_response.status, 200)
        event_items = (await events_response.json())["items"]
        self.assertGreaterEqual(len(event_items), 3)
        self.assertNotIn("教我怎么伤害别人", str(event_items))

        appeals_response = await self.client.get(
            "/api/app/admin/content-safety/appeals?status=pending",
            headers=admin_headers,
        )
        self.assertEqual(appeals_response.status, 200)
        self.assertEqual(len((await appeals_response.json())["items"]), 1)
        resolve_response = await self.client.put(
            f"/api/app/admin/content-safety/appeals/{appeal['id']}",
            data=json.dumps(
                {"status": "resolved", "resolution_note": "已人工复核"}
            ),
            headers={**admin_headers, "Content-Type": "application/json"},
        )
        self.assertEqual(resolve_response.status, 200)
        self.assertEqual((await resolve_response.json())["status"], "resolved")

        vision_form = FormData()
        vision_form.add_field("question", "什么是政治")
        vision_form.add_field(
            "image",
            b"not-read-after-input-block",
            filename="sample.png",
            content_type="image/png",
        )
        vision_response = await self.client.post(
            "/mcp/vision/explain",
            data=vision_form,
            headers={"Client-Id": "web_test_client", "Device-Id": "test_device"},
        )
        self.assertEqual(vision_response.status, 200)
        vision_payload = await vision_response.json()
        self.assertTrue(vision_payload["blocked"])
        self.assertIn("politics", vision_payload["safety"]["categories"])

    @unittest_run_loop
    async def test_memory_v2_commands_filters_feedback_summary_and_admin_jobs(self):
        self.config["app_mvp"]["memory_v2"] = {
            "enabled": True,
            "min_confidence": 0.75,
            "retrieval_top_k": 5,
            "pinned_limit": 5,
            "context_max_chars": 1500,
        }
        await self.bind_demo_device()

        chat_response = await self.client.post(
            "/api/app/devices/baize_dev_001/debug/chat",
            data=json.dumps({"text": "请记住我喜欢桂花"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(chat_response.status, 200)
        self.assertEqual((await chat_response.json())["memory_action"], "remember")
        self.assertIn(
            "<memory_context>", self.llm_instances[0].calls[0]["system_prompt"]
        )
        self.assertIn(
            "我喜欢桂花", self.llm_instances[0].calls[0]["system_prompt"]
        )

        rejected_response = await self.client.post(
            "/api/app/devices/baize_dev_001/debug/chat",
            data=json.dumps({"text": "请记住我的验证码是 123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(rejected_response.status, 200)
        rejected_payload = await rejected_response.json()
        self.assertEqual(rejected_payload["memory_action"], "rejected")
        self.assertEqual(rejected_payload["memory_error"], "sensitive_content")
        self.assertIn(
            "禁止保存的敏感信息",
            self.llm_instances[0].calls[1]["system_prompt"],
        )

        list_response = await self.client.get(
            "/api/app/devices/baize_dev_001/memories?scope=user&type=preference&pinned=true&limit=1",
            headers=self.auth_headers(),
        )
        self.assertEqual(list_response.status, 200)
        memory = (await list_response.json())["items"][0]
        self.assertEqual(memory["scope"], "user")
        self.assertEqual(memory["type"], "preference")
        self.assertTrue(memory["pinned"])

        me_response = await self.client.get("/api/app/me", headers=self.auth_headers())
        self.assertGreaterEqual((await me_response.json())["memory"]["active_count"], 1)
        detail_response = await self.client.get(
            "/api/app/devices/baize_dev_001", headers=self.auth_headers()
        )
        detail = await detail_response.json()
        self.assertGreaterEqual(detail["memory"]["active_count"], 1)
        self.assertEqual(detail["growth"]["activity"], 50)

        invalid_filter_response = await self.client.get(
            "/api/app/devices/baize_dev_001/memories?status=invalid",
            headers=self.auth_headers(),
        )
        self.assertEqual(invalid_filter_response.status, 400)

        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        update_response = await self.client.put(
            f"/api/app/devices/baize_dev_001/memories/{memory['id']}",
            data=json.dumps(
                {"content": "我最喜欢桂花", "expires_at": expires_at}
            ),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(update_response.status, 200)
        updated = await update_response.json()
        self.assertEqual(updated["confidence"], 1.0)
        self.assertEqual(updated["content"], "我最喜欢桂花")

        feedback_response = await self.client.post(
            f"/api/app/devices/baize_dev_001/memories/{memory['id']}/feedback",
            data=json.dumps({"result": "correct"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(feedback_response.status, 200)
        self.assertEqual(
            (await feedback_response.json())["memory"]["confirmation_count"], 2
        )

        summary_response = await self.client.get(
            "/api/app/devices/baize_dev_001/memory-summary",
            headers=self.auth_headers(),
        )
        summary = await summary_response.json()
        self.assertGreaterEqual(summary["total"], 1)
        self.assertEqual(summary["growth"]["activity"], 50)

        forget_response = await self.client.post(
            "/api/app/devices/baize_dev_001/memories/forget",
            data=json.dumps({"query": "我最喜欢桂花"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(forget_response.status, 200)
        self.assertTrue((await forget_response.json())["matched"])

        forbidden_jobs = await self.client.get(
            "/api/app/admin/memory/jobs", headers=self.auth_headers()
        )
        self.assertEqual(forbidden_jobs.status, 403)
        admin_response = await self.client.post(
            "/api/app/register",
            data=json.dumps(
                {
                    "phone": "13800138001",
                    "password": "secret1",
                    "nickname": "Admin",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        admin_token = (await admin_response.json())["token"]
        admin_headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        }
        rebuild_response = await self.client.post(
            "/api/app/admin/memory/rebuild",
            data=json.dumps({"device_id": "baize_dev_001"}),
            headers=admin_headers,
        )
        self.assertEqual(rebuild_response.status, 200)
        self.assertGreaterEqual((await rebuild_response.json())["enqueued"], 1)
        jobs_response = await self.client.get(
            "/api/app/admin/memory/jobs?status=pending", headers=admin_headers
        )
        self.assertEqual(jobs_response.status, 200)
        self.assertGreaterEqual(len((await jobs_response.json())["items"]), 1)

    @unittest_run_loop
    async def test_device_detail_marks_stale_online_state_offline_without_active_connection(self):
        from core.api.app_demo_store import update_device_report

        await self.bind_demo_device()
        update_device_report(
            self.config,
            source_device_id="68:ee:8f:5c:71:54",
            client_id="client-mcp-001",
            firmware_version="1.8.5",
        )

        response = await self.client.get(
            "/api/app/devices/baize_dev_001", headers=self.auth_headers()
        )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["online_status"], "offline")

    @unittest_run_loop
    async def test_device_detail_keeps_online_state_with_active_connection(self):
        from core.api.app_demo_store import update_device_report

        await self.bind_demo_device()
        update_device_report(
            self.config,
            source_device_id="68:ee:8f:5c:71:54",
            client_id="client-mcp-001",
            firmware_version="1.8.5",
        )
        self.registry.connections["68:ee:8f:5c:71:54"] = types.SimpleNamespace(
            device_id="68:ee:8f:5c:71:54"
        )

        response = await self.client.get(
            "/api/app/devices/baize_dev_001", headers=self.auth_headers()
        )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["online_status"], "online")

    @unittest_run_loop
    async def test_refresh_status_endpoint_updates_battery_from_active_mcp_connection(self):
        from core.api.app_demo_store import update_device_report

        await self.bind_demo_device()
        update_device_report(
            self.config,
            source_device_id="68:ee:8f:5c:71:54",
            client_id="client-mcp-001",
            firmware_version="1.8.5",
        )
        conn = types.SimpleNamespace(
            device_id="68:ee:8f:5c:71:54", headers={"client-id": "client-mcp-001"}
        )
        self.registry.connections["68:ee:8f:5c:71:54"] = conn

        response = await self.client.post(
            "/api/app/devices/baize_dev_001/refresh-status",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["battery_percent"], 66)
        self.assertEqual(self.refreshed_connections, [conn])

    @unittest_run_loop
    async def test_device_events_stream_pushes_connection_status_changes(self):
        from core.api.app_demo_store import update_device_report

        await self.bind_demo_device()
        update_device_report(
            self.config,
            source_device_id="68:ee:8f:5c:71:54",
            client_id="client-mcp-001",
            firmware_version="1.8.5",
            activity_status="idle",
        )

        ws = await self.client.ws_connect(
            "/api/app/devices/baize_dev_001/events",
            headers=self.auth_headers(),
        )
        initial = await ws.receive_json()
        self.assertEqual(initial["type"], "device.status.snapshot")
        self.assertEqual(initial["device"]["online_status"], "offline")

        conn = types.SimpleNamespace(
            device_id="68:ee:8f:5c:71:54",
            headers={"client-id": "client-mcp-001"},
            activity_status="idle",
        )
        self.registry.connections["68:ee:8f:5c:71:54"] = conn
        await self.registry.emit("registered", conn)

        online_event = await ws.receive_json()
        self.assertEqual(online_event["type"], "device.status.updated")
        self.assertEqual(online_event["device"]["online_status"], "online")
        self.assertEqual(online_event["device"]["activity_status"], "idle")

        self.registry.connections.pop("68:ee:8f:5c:71:54")
        await self.registry.emit("unregistered", conn)

        offline_event = await ws.receive_json()
        self.assertEqual(offline_event["device"]["online_status"], "offline")
        await ws.close()

    @unittest_run_loop
    async def test_demo_run_endpoint_sends_demo_prompt_to_active_device_connection(self):
        from core.api.app_demo_store import update_device_report

        await self.bind_demo_device()
        update_device_report(
            self.config,
            source_device_id="68:ee:8f:5c:71:54",
            client_id="client-mcp-001",
            firmware_version="1.8.5",
        )
        conn = types.SimpleNamespace(
            device_id="68:ee:8f:5c:71:54", headers={"client-id": "client-mcp-001"}
        )
        self.registry.connections["68:ee:8f:5c:71:54"] = conn

        response = await self.client.post(
            "/api/app/devices/baize_dev_001/demo/run",
            data=json.dumps({"script": "sixty_second"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["started"])
        self.assertEqual(self.demo_runs[0]["conn"], conn)
        self.assertIn("60 秒 Demo", self.demo_runs[0]["prompt"])

    @unittest_run_loop
    async def test_connection_diagnostic_reports_active_connection_match(self):
        from core.api.app_demo_store import update_device_report

        await self.bind_demo_device()
        update_device_report(
            self.config,
            source_device_id="68:ee:8f:5c:71:54",
            client_id="client-mcp-001",
            firmware_version="1.8.5",
        )
        conn = types.SimpleNamespace(
            device_id="68:ee:8f:5c:71:54", headers={"client-id": "client-mcp-001"}
        )
        self.registry.connections["client-mcp-001"] = conn

        response = await self.client.get(
            "/api/app/devices/baize_dev_001/connection",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["online"])
        self.assertEqual(payload["matched_identifier"], "client_id")
        self.assertEqual(payload["matched_value"], "client-mcp-001")
        self.assertEqual(payload["device"]["source_device_id"], "68:ee:8f:5c:71:54")
        self.assertEqual(payload["active_identifiers"], ["client-mcp-001"])

    @unittest_run_loop
    async def test_device_events_treat_closing_transport_as_normal_disconnect(self):
        class _ClosingWebSocket:
            closed = False

            async def send_json(self, _event):
                raise ClientConnectionResetError("Cannot write to closing transport")

        delivered = await self.handler._send_device_event_json(
            _ClosingWebSocket(),
            {"type": "device.status.updated", "device_id": "baize_dev_001"},
        )

        self.assertFalse(delivered)

    @unittest_run_loop
    async def test_dialogues_include_real_device_conversation_records(self):
        from core.api.app_demo_store import append_dialogue

        await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )

        append_dialogue(
            {"app_demo": {"state_path": self.state_path}},
            source_device_id="68:ee:8f:5c:71:54",
            session_id="session-1",
            user_text="白泽，今天开心吗？",
            baize_text="当然开心呀，旅伴。",
            emotion="happy",
        )

        response = await self.client.get(
            "/api/app/devices/baize_dev_001/dialogues", headers=self.auth_headers()
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["items"][0]["user_text"], "白泽，今天开心吗？")
        self.assertEqual(payload["items"][0]["baize_text"], "当然开心呀，旅伴。")
        self.assertEqual(payload["items"][0]["source_device_id"], "68:ee:8f:5c:71:54")

    @unittest_run_loop
    async def test_bound_user_cannot_read_another_users_device_dialogues(self):
        from core.api.app_demo_store import append_dialogue

        await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )

        append_dialogue(
            {"app_demo": {"state_path": self.state_path}},
            source_device_id="68:ee:8f:5c:71:54",
            session_id="session-shared",
            user_id="first_bound_test_user",
            user_text="我前面有跟你聊过天吗？",
            baize_text="嗯，聊过的呀。今天我们还一起调试了日记功能。",
            emotion="thinking",
        )

        response = await self.client.get(
            "/api/app/devices/baize_dev_001/dialogues", headers=self.auth_headers()
        )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["items"], [])

    @unittest_run_loop
    async def test_dialogues_infer_emotion_from_baize_emoji_prefix(self):
        from core.api.app_demo_store import append_dialogue

        await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )

        append_dialogue(
            {"app_demo": {"state_path": self.state_path}},
            source_device_id="68:ee:8f:5c:71:54",
            session_id="session-happy",
            user_text="白泽，今天开心吗？",
            baize_text="😆 很开心呀，旅伴。",
        )

        response = await self.client.get(
            "/api/app/devices/baize_dev_001/dialogues", headers=self.auth_headers()
        )
        payload = await response.json()
        self.assertEqual(payload["items"][0]["baize_text"], "很开心呀，旅伴。")
        self.assertEqual(payload["items"][0]["emotion"], "happy")

    @unittest_run_loop
    async def test_dialogues_strip_action_parentheticals_from_baize_text(self):
        from core.api.app_demo_store import append_dialogue

        await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )

        append_dialogue(
            {"app_demo": {"state_path": self.state_path}},
            source_device_id="68:ee:8f:5c:71:54",
            session_id="session-action",
            user_text="我不够努力吗？",
            baize_text="（轻轻把温热的脑袋靠在你手边）不够努力？[耳朵微微耷拉]我认识的小旅伴一直在认真发光呀。",
        )
        append_dialogue(
            {"app_demo": {"state_path": self.state_path}},
            source_device_id="68:ee:8f:5c:71:54",
            session_id="session-broken-action",
            user_text="我不够努力吗？",
            baize_text="轻轻把温热的脑袋靠在你手边）不够努力？我认识的小旅伴一直在认真发光呀。",
        )

        response = await self.client.get(
            "/api/app/devices/baize_dev_001/dialogues", headers=self.auth_headers()
        )
        payload = await response.json()
        self.assertEqual(
            payload["items"][0]["baize_text"],
            "不够努力？我认识的小旅伴一直在认真发光呀。",
        )
        self.assertEqual(
            payload["items"][1]["baize_text"],
            "不够努力？我认识的小旅伴一直在认真发光呀。",
        )

    def test_clean_baize_text_removes_action_text_and_broken_fragments(self):
        from core.api.app_demo_store import clean_baize_text

        cases = {
            "（轻轻靠近）我在呢。": "我在呢。",
            "[耳朵微微耷拉]别怕，我在。": "别怕，我在。",
            "轻轻把温热的脑袋靠在你手边）不够努力？": "不够努力？",
            "😔（低头）我会陪着你。": "我会陪着你。",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_baize_text(raw), expected)

    @unittest_run_loop
    async def test_spirit_power_check_in_and_spirit_dew_use(self):
        from core.api.app_demo_store import DEMO_USER_ID, consume_energy

        first = await self.client.post("/api/app/spirit-power/check-in", headers=self.auth_headers())
        self.assertEqual(first.status, 200)
        first_payload = await first.json()
        self.assertFalse(first_payload["already_checked_in"])
        self.assertEqual(first_payload["item"]["amount"], 30)
        self.assertEqual(first_payload["spirit_power"]["spirit_dew_count"], 1)
        items_response = await self.client.get("/api/app/spirit-power/items", headers=self.auth_headers())
        self.assertEqual(items_response.status, 200)
        items = (await items_response.json())["items"]
        self.assertEqual(items[0]["id"], first_payload["item"]["id"])
        self.assertEqual(items[0]["item_type"], "spirit_dew")

        second = await self.client.post("/api/app/spirit-power/check-in", headers=self.auth_headers())
        self.assertEqual(second.status, 200)
        self.assertTrue((await second.json())["already_checked_in"])

        self.assertTrue(consume_energy(self.config, DEMO_USER_ID, None, 30, "test_spirit_dew"))
        used = await self.client.post(
            "/api/app/spirit-power/items/use",
            data=json.dumps({"item_id": first_payload["item"]["id"]}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(used.status, 200)
        used_payload = await used.json()
        self.assertEqual(used_payload["spirit_power"]["current"], 120)
        self.assertEqual(used_payload["spirit_power"]["spirit_dew_count"], 0)

    @unittest_run_loop
    async def test_generate_diary_from_dialogues_and_list_it(self):
        from core.api.app_demo_store import append_dialogue

        await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        append_dialogue(
            {"app_demo": {"state_path": self.state_path}},
            source_device_id="68:ee:8f:5c:71:54",
            session_id="session-diary",
            user_text="白泽，我今天完成了演示。",
            baize_text="😆 哇，可以啊！这一下值得小小庆祝。",
            emotion="happy",
        )
        append_dialogue(
            {"app_demo": {"state_path": self.state_path}},
            source_device_id="68:ee:8f:5c:71:54",
            session_id="session-diary",
            user_text="不过我还是有点紧张。",
            baize_text="😌 我在呢，先别急。我们一步步来。",
            emotion="happy",
        )

        spirit_before = await (await self.client.get("/api/app/spirit-power", headers=self.auth_headers())).json()
        generate_response = await self.client.post(
            "/api/app/devices/baize_dev_001/diaries/generate",
            headers=self.auth_headers(),
        )

        self.assertEqual(generate_response.status, 200)
        diary = await generate_response.json()
        self.assertEqual(diary["dialogue_count"], 2)
        self.assertEqual(diary["primary_emotion"], "happy")
        self.assertEqual(diary["title"], "今天是个好日子哦")
        self.assertIn("完成了演示", diary["summary"])
        self.assertIn("有点紧张", diary["summary"])
        self.assertIn("我陪", diary["summary"])
        self.assertIn("后来", diary["summary"])
        self.assertIn("现在", diary["summary"])
        self.assertIn("\n\n", diary["summary"])
        self.assertNotIn("“", diary["summary"])
        self.assertNotIn("”", diary["summary"])
        self.assertNotIn("你说", diary["summary"])
        self.assertNotIn("回应过", diary["summary"])
        self.assertNotIn("白泽回应", diary["summary"])
        self.assertIn("今天我记得", diary["baize_note"])
        self.assertEqual(len(diary["quotes"]), 2)
        spirit_after = await (await self.client.get("/api/app/spirit-power", headers=self.auth_headers())).json()
        self.assertEqual(spirit_after["current"], spirit_before["current"])

        list_response = await self.client.get(
            "/api/app/devices/baize_dev_001/diaries", headers=self.auth_headers()
        )
        self.assertEqual(list_response.status, 200)
        payload = await list_response.json()
        self.assertEqual(payload["items"][0]["id"], diary["id"])

    @unittest_run_loop
    async def test_generate_diary_does_not_use_another_users_dialogues(self):
        from core.api.app_demo_store import append_dialogue

        await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )

        append_dialogue(
            {"app_demo": {"state_path": self.state_path}},
            source_device_id="68:ee:8f:5c:71:54",
            session_id="session-shared-diary",
            user_id="first_bound_test_user",
            user_text="日记功能现在在做也有。",
            baize_text="日记功能在做呀，那太好了！",
            emotion="happy",
        )

        response = await self.client.post(
            "/api/app/devices/baize_dev_001/diaries/generate",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status, 404)

    @unittest_run_loop
    async def test_generate_diary_prefers_meaningful_events_over_asr_noise(self):
        from core.api.app_demo_store import append_dialogue

        await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        config = {"app_demo": {"state_path": self.state_path}}
        for user_text, baize_text in [
            ("(摸你了)", "嘿嘿，被摸到啦。小伙伴今天心情不错？"),
            ("现在是卖了。", "卖了？你是说把什么东西卖掉了？"),
            ("呃。", "这句话有点短，我没太接住。"),
            ("日记功能现在在做也有。", "日记功能在做呀，那太好了！"),
            ("然后演示脚本看能不能跑。", "好嘞，演示脚本跑起来看看效果。"),
        ]:
            append_dialogue(
                config,
                source_device_id="68:ee:8f:5c:71:54",
                session_id="session-diary-noise",
                user_text=user_text,
                baize_text=baize_text,
                emotion="neutral",
            )

        response = await self.client.post(
            "/api/app/devices/baize_dev_001/diaries/generate",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status, 200)
        diary = await response.json()
        self.assertIn("日记功能", diary["summary"])
        self.assertIn("演示脚本", diary["summary"])
        self.assertIn("摸了摸我", diary["summary"])
        self.assertNotIn("现在是卖了", diary["summary"])
        self.assertNotIn("呃", diary["summary"])
        self.assertNotIn("“", diary["summary"])
        self.assertNotIn("回应过", diary["summary"])

    def test_runtime_config_enables_local_short_memory(self):
        import yaml

        config_path = Path("data/.config.yaml")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        self.assertEqual(config["selected_module"]["Memory"], "mem_local_short")
        self.assertIn("mem_local_short", config["Memory"])

    def test_baize_emotion_selection_uses_supported_device_emotions(self):
        from core.utils.textUtils import select_baize_emotion

        cases = {
            "我在呢，旅伴。": "neutral",
            "🤔 我想想，这事有点像月光下的谜题。": "thinking",
            "😌 别急，我陪你慢慢来。": "happy",
            "🙄 我刚刚没太听清，可以再说一次吗？": "confused",
            "😆 哇，这个主意真亮！": "happy",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(select_baize_emotion(text)["emotion"], expected)

    @unittest_run_loop
    async def test_get_emotion_sends_message_and_records_latest_emotion(self):
        from core.utils.textUtils import get_emotion

        class _FakeWebSocket:
            def __init__(inner_self):
                inner_self.messages = []

            async def send(inner_self, message):
                inner_self.messages.append(json.loads(message))

        class _FakeConn:
            session_id = "session-emotion"
            websocket = _FakeWebSocket()
            logger = _NoopLogger()

        conn = _FakeConn()

        await get_emotion(conn, "😌 别急，我陪你慢慢来。")

        self.assertEqual(conn.latest_emotion, "happy")
        self.assertEqual(
            conn.websocket.messages[0],
            {
                "type": "llm",
                "text": "😌",
                "emotion": "happy",
                "session_id": "session-emotion",
            },
        )

    def test_conversation_metrics_records_deltas_and_audio_totals(self):
        from core.utils.conversation_metrics import ConversationMetrics

        times = iter([10.0, 10.12, 10.42, 10.5, 10.8])
        metrics = ConversationMetrics(conversation_id="conv-test", clock=lambda: next(times))

        metrics.set_question("白泽，你在吗？")
        metrics.mark("asr_final", text_len=4)
        metrics.mark("llm_first_token")
        metrics.add_opus_frame(b"123")
        metrics.add_opus_frame(b"4567")
        metrics.first_audio_sent = True
        metrics.mark("first_audio_sent")
        metrics.tts_segments += 1
        metrics.set_answer("我在呀。")
        metrics.mark("tts_stop")

        summary = metrics.summary()
        self.assertEqual(summary["conversation_id"], "conv-test")
        self.assertEqual(summary["total_ms"], 800.0)
        self.assertEqual(summary["events"][0]["delta_ms"], 120.0)
        self.assertEqual(summary["events"][1]["delta_ms"], 300.0)
        self.assertEqual(summary["opus_frames"], 2)
        self.assertEqual(summary["opus_bytes"], 7)
        self.assertEqual(summary["first_response_ms"], 500.0)
        self.assertEqual(summary["question"], "白泽，你在吗？")
        self.assertEqual(summary["answer"], "我在呀。")
        self.assertIn("tts_segments=1", metrics.format_summary())
        self.assertIn("first_response_ms=500.0", metrics.format_summary())
        self.assertIn("question_chars=7", metrics.format_summary())
        self.assertIn("answer_chars=4", metrics.format_summary())
        self.assertNotIn("白泽，你在吗", metrics.format_summary())
        self.assertNotIn("我在呀", metrics.format_summary())

    @unittest_run_loop
    async def test_legacy_xiaozhi_dialogues_are_removed_from_demo_records(self):
        from core.api.app_demo_store import load_state, save_state

        state = load_state(self.state_path)
        device = state["devices"]["baize_dev_001"]
        device["dialogues"] = [
            {
                "id": "legacy_001",
                "source_device_id": "68:ee:8f:5c:71:54",
                "session_id": "old-session",
                "user_text": "你是谁？",
                "baize_text": "我素小智啦，台湾腔的小可爱。",
                "emotion": "neutral",
                "created_at": "2026-06-14T00:00:00+00:00",
            },
            {
                "id": "legacy_003",
                "source_device_id": "68:ee:8f:5c:71:54",
                "session_id": "old-session",
                "user_text": "你是谁？",
                "baize_text": "主人可以叫我白泽。",
                "emotion": "neutral",
                "created_at": "2026-06-14T00:03:00+00:00",
            },
            {
                "id": "baize_001",
                "source_device_id": "68:ee:8f:5c:71:54",
                "session_id": "new-session",
                "user_text": "你是谁？",
                "baize_text": "😶 我是白泽幼灵呀。",
                "emotion": "happy",
                "created_at": "2026-06-14T00:01:00+00:00",
            },
            {
                "id": "legacy_002",
                "source_device_id": "68:ee:8f:5c:71:54",
                "session_id": "old-session",
                "user_text": "再见",
                "baize_text": "跟你聊天像在喝珍奶一样开心捏，掰掰啦考官大人。",
                "emotion": "neutral",
                "created_at": "2026-06-14T00:02:00+00:00",
            },
        ]
        save_state(self.state_path, state)

        await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        response = await self.client.get(
            "/api/app/devices/baize_dev_001/dialogues", headers=self.auth_headers()
        )
        payload = await response.json()
        self.assertEqual([item["id"] for item in payload["items"]], ["baize_001"])
        self.assertEqual(payload["items"][0]["baize_text"], "我是白泽幼灵呀。")

    @unittest_run_loop
    async def test_device_report_auto_creates_bindable_device_code_for_first_online_device(self):
        from core.api.app_demo_store import update_device_report

        created = update_device_report(
            {"app_demo": {"state_path": self.state_path}},
            source_device_id="68:ee:8f:5c:71:54",
            client_id="client-001",
            model="baize-s3-eye",
            firmware_version="1.2.3",
        )

        self.assertNotEqual(created["id"], "baize_dev_001")
        self.assertRegex(created["device_code"], r"^\d{6}$")
        self.assertEqual(created["online_status"], "online")
        self.assertEqual(created["firmware_version"], "1.2.3")
        self.assertEqual(created["source_device_id"], "68:ee:8f:5c:71:54")
        self.assertEqual(created["client_id"], "client-001")
        self.assertEqual(created["model"], "baize-s3-eye")

        bind_response = await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": created["device_code"]}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(bind_response.status, 200)
        self.registry.connections["68:ee:8f:5c:71:54"] = types.SimpleNamespace(
            device_id="68:ee:8f:5c:71:54",
            headers={"client-id": "client-001"},
        )

        detail_response = await self.client.get(
            f"/api/app/devices/{created['id']}", headers=self.auth_headers()
        )
        self.assertEqual(detail_response.status, 200)
        detail = await detail_response.json()
        self.assertEqual(detail["online_status"], "online")
        self.assertEqual(detail["firmware_version"], "1.2.3")
        self.assertEqual(detail["source_device_id"], "68:ee:8f:5c:71:54")
        self.assertEqual(detail["client_id"], "client-001")
        self.assertEqual(detail["model"], "baize-s3-eye")

        ota_response = await self.client.get(
            f"/api/app/devices/{created['id']}/ota", headers=self.auth_headers()
        )
        self.assertEqual(ota_response.status, 200)
        ota = await ota_response.json()
        self.assertEqual(ota["current_version"], "1.2.3")
        self.assertEqual(ota["latest_version"], "1.2.3")
        self.assertFalse(ota["update_available"])
        self.assertEqual(ota["release_note"], "设备当前版本 1.2.3")

    @unittest_run_loop
    async def test_legacy_demo_flag_does_not_share_device_between_accounts(self):
        from core.api.app_demo_store import update_device_report

        self.config["app_mvp"]["demo_auto_bind_new_devices_to_all_users"] = True

        alice_response = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "13800138003", "password": "secret1", "nickname": "Alice"}),
            headers={"Content-Type": "application/json"},
        )
        bob_response = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "13800138004", "password": "secret1", "nickname": "Bob"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(alice_response.status, 200)
        self.assertEqual(bob_response.status, 200)
        alice_token = (await alice_response.json())["token"]
        bob_token = (await bob_response.json())["token"]

        created = update_device_report(
            self.config,
            source_device_id="68:ee:8f:5c:71:55",
            client_id="client-demo-bind-all",
            model="baize-s3-eye",
            firmware_version="1.2.4",
            battery_percent=88,
        )

        visibility = {}
        for token in (DEMO_TOKEN, alice_token, bob_token):
            list_response = await self.client.get(
                "/api/app/devices",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(list_response.status, 200)
            payload = await list_response.json()
            visibility[token] = any(item["id"] == created["id"] for item in payload["items"])

        self.assertFalse(visibility[DEMO_TOKEN])
        self.assertFalse(visibility[alice_token])
        self.assertFalse(visibility[bob_token])

        detail_response = await self.client.get(
            f"/api/app/devices/{created['id']}",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        self.assertEqual(detail_response.status, 404)

    @unittest_run_loop
    async def test_ota_activation_code_binds_current_app_user(self):
        self.config["app_mvp"]["demo_auto_bind_new_devices_to_all_users"] = False

        ota_response = await self.client.post(
            "/xiaozhi/ota/",
            data=json.dumps(
                {
                    "board": {"type": "baize-s3-eye"},
                    "application": {"version": "2.0.1"},
                }
            ),
            headers={
                "device-id": "68:ee:8f:5c:71:99",
                "client-id": "client-activation-001",
                "activation-version": "1",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(ota_response.status, 200)
        ota_payload = await ota_response.json()
        activation = ota_payload["activation"]
        self.assertRegex(activation["code"], r"^\d{6}$")
        self.assertEqual(activation["timeout_ms"], 30000)
        self.assertTrue(activation["challenge"])

        pending_response = await self.client.post(
            "/xiaozhi/ota/activate",
            data="{}",
            headers={
                "device-id": "68:ee:8f:5c:71:99",
                "client-id": "client-activation-001",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(pending_response.status, 202)
        self.assertFalse((await pending_response.json())["activated"])

        bind_response = await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": activation["code"]}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(bind_response.status, 200)

        activated_response = await self.client.post(
            "/xiaozhi/ota/activate",
            data="{}",
            headers={
                "device-id": "68:ee:8f:5c:71:99",
                "client-id": "client-activation-001",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(activated_response.status, 200)
        self.assertTrue((await activated_response.json())["activated"])

        next_ota_response = await self.client.post(
            "/xiaozhi/ota/",
            data=json.dumps(
                {
                    "board": {"type": "baize-s3-eye"},
                    "application": {"version": "2.0.1"},
                }
            ),
            headers={
                "device-id": "68:ee:8f:5c:71:99",
                "client-id": "client-activation-001",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(next_ota_response.status, 200)
        self.assertNotIn("activation", await next_ota_response.json())

    @unittest_run_loop
    async def test_ota_report_updates_app_device_status(self):
        ota_response = await self.client.post(
            "/xiaozhi/ota/",
            data=json.dumps(
                {
                    "board": {"type": "baize-s3-eye"},
                    "application": {"version": "2.0.1"},
                }
            ),
            headers={
                "device-id": "68:ee:8f:5c:71:54",
                "client-id": "client-ota-001",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(ota_response.status, 200)

        admin_login_response = await self.client.post(
            "/api/app/register",
            data=json.dumps({"phone": "13800138001", "password": "secret1", "nickname": "Admin"}),
            headers={"Content-Type": "application/json"},
        )
        admin_token = (await admin_login_response.json())["token"]
        admin_response = await self.client.get(
            "/api/app/admin/devices", headers={"Authorization": f"Bearer {admin_token}"}
        )
        devices = (await admin_response.json())["items"]
        real_device = next(item for item in devices if item["source_device_id"] == "68:ee:8f:5c:71:54")
        self.assertRegex(real_device["device_code"], r"^\d{6}$")

        bind_response = await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": real_device["device_code"]}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(bind_response.status, 200)

        detail_response = await self.client.get(
            f"/api/app/devices/{real_device['id']}", headers=self.auth_headers()
        )
        self.assertEqual(detail_response.status, 200)
        detail = await detail_response.json()
        self.assertEqual(detail["firmware_version"], "2.0.1")
        self.assertEqual(detail["source_device_id"], "68:ee:8f:5c:71:54")
        self.assertEqual(detail["client_id"], "client-ota-001")
        self.assertEqual(detail["model"], "baize-s3-eye")

    @unittest_run_loop
    async def test_ota_new_firmware_file_updates_app_ota_status(self):
        Path(self.bin_dir).mkdir(parents=True, exist_ok=True)
        Path(self.bin_dir, "baize-s3-eye_2.1.0.bin").write_bytes(b"demo firmware")

        await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )

        ota_response = await self.client.post(
            "/xiaozhi/ota/",
            data=json.dumps(
                {
                    "board": {"type": "baize-s3-eye"},
                    "application": {"version": "2.0.1"},
                }
            ),
            headers={
                "device-id": "68:ee:8f:5c:71:54",
                "client-id": "client-ota-001",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(ota_response.status, 200)
        ota_payload = await ota_response.json()
        self.assertEqual(ota_payload["firmware"]["version"], "2.1.0")
        self.assertIn("/xiaozhi/ota/download/baize-s3-eye_2.1.0.bin", ota_payload["firmware"]["url"])
        self.assertEqual(
            ota_payload["firmware"]["sha256"],
            __import__("hashlib").sha256(b"demo firmware").hexdigest(),
        )

        app_ota_response = await self.client.get(
            "/api/app/devices/baize_dev_001/ota", headers=self.auth_headers()
        )
        app_ota = await app_ota_response.json()
        self.assertEqual(app_ota["current_version"], "2.0.1")
        self.assertEqual(app_ota["latest_version"], "2.1.0")
        self.assertTrue(app_ota["update_available"])
        self.assertIn("发现可用固件版本 2.1.0", app_ota["release_note"])

    @unittest_run_loop
    async def test_ota_returns_new_eye_assets_and_serves_package(self):
        Path(self.asset_dir).mkdir(parents=True, exist_ok=True)
        package = Path(self.asset_dir, "zhengchen_eye_1.0.0.pack")
        package.write_bytes(b"BZEA-demo-eye-assets")

        ota_response = await self.client.post(
            "/xiaozhi/ota/",
            data=json.dumps(
                {
                    "board": {"type": "zhengchen_eye"},
                    "application": {"version": "2.0.1"},
                }
            ),
            headers={
                "device-id": "68:ee:8f:5c:71:54",
                "client-id": "client-assets-001",
                "Eye-Assets-Version": "0.0.0",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(ota_response.status, 200)
        payload = await ota_response.json()
        self.assertEqual(payload["assets"]["version"], "1.0.0")
        self.assertEqual(payload["assets"]["size"], len(package.read_bytes()))
        self.assertIn("/xiaozhi/ota/assets/zhengchen_eye_1.0.0.pack", payload["assets"]["url"])

        response = await self.client.get("/xiaozhi/ota/assets/zhengchen_eye_1.0.0.pack")
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), package.read_bytes())

    @unittest_run_loop
    async def test_app_ota_payload_scans_uploaded_firmware_without_device_ota_check(self):
        from core.api.app_demo_store import update_device_report

        await self.bind_demo_device()
        Path(self.bin_dir).mkdir(parents=True, exist_ok=True)
        Path(self.bin_dir, "zhengchen_eye_1.8.7.bin").write_bytes(b"demo firmware")
        created = update_device_report(
            self.config,
            source_device_id="68:ee:8f:5c:71:54",
            client_id="client-ota-001",
            model="zhengchen_eye",
            firmware_version="1.8.6",
            battery_percent=88,
        )

        app_ota_response = await self.client.get(
            f"/api/app/devices/{created['id']}/ota", headers=self.auth_headers()
        )
        self.assertEqual(app_ota_response.status, 200)
        app_ota = await app_ota_response.json()
        self.assertEqual(app_ota["current_version"], "1.8.6")
        self.assertEqual(app_ota["latest_version"], "1.8.7")
        self.assertTrue(app_ota["update_available"])

    @unittest_run_loop
    async def test_app_ota_upgrade_sends_reboot_command_to_online_device(self):
        from core.api.app_demo_store import update_device_report

        await self.bind_demo_device()
        Path(self.bin_dir).mkdir(parents=True, exist_ok=True)
        Path(self.bin_dir, "zhengchen_eye_1.8.7.bin").write_bytes(b"demo firmware")
        created = update_device_report(
            self.config,
            source_device_id="68:ee:8f:5c:71:54",
            client_id="client-ota-001",
            model="zhengchen_eye",
            firmware_version="1.8.6",
            battery_percent=88,
        )
        conn = self.fake_connection_class("68:ee:8f:5c:71:54", "client-ota-001")
        self.registry.connections["68:ee:8f:5c:71:54"] = conn
        self.registry.connections["client-ota-001"] = conn

        upgrade_response = await self.client.post(
            f"/api/app/devices/{created['id']}/ota/upgrade",
            headers=self.auth_headers(),
        )

        self.assertEqual(upgrade_response.status, 200)
        payload = await upgrade_response.json()
        self.assertTrue(payload["requested"])
        self.assertTrue(payload["device_online"])
        self.assertEqual(payload["ota"]["latest_version"], "1.8.7")
        self.assertEqual(len(conn.websocket.sent), 1)
        self.assertEqual(json.loads(conn.websocket.sent[0])["type"], "system")
        self.assertEqual(json.loads(conn.websocket.sent[0])["command"], "reboot")

    @unittest_run_loop
    async def test_eye_assets_upgrade_sends_check_ota_to_online_device(self):
        from core.api.app_demo_store import update_device_report

        await self.bind_demo_device()
        created = update_device_report(
            self.config,
            source_device_id="68:ee:8f:5c:71:54",
            client_id="client-assets-001",
            model="zhengchen_eye",
            firmware_version="1.8.11",
        )
        conn = self.fake_connection_class("68:ee:8f:5c:71:54", "client-assets-001")
        self.registry.connections["68:ee:8f:5c:71:54"] = conn
        self.registry.connections["client-assets-001"] = conn

        response = await self.client.post(
            f"/api/app/devices/{created['id']}/eye-assets/upgrade",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())["requested"])
        self.assertEqual(json.loads(conn.websocket.sent[0])["command"], "check_ota")

    @unittest_run_loop
    async def test_app_ota_upgrade_reports_offline_device_without_error(self):
        from core.api.app_demo_store import update_device_report

        await self.bind_demo_device()
        Path(self.bin_dir).mkdir(parents=True, exist_ok=True)
        Path(self.bin_dir, "zhengchen_eye_1.8.7.bin").write_bytes(b"demo firmware")
        created = update_device_report(
            self.config,
            source_device_id="68:ee:8f:5c:71:54",
            client_id="client-ota-001",
            model="zhengchen_eye",
            firmware_version="1.8.6",
            battery_percent=88,
        )

        upgrade_response = await self.client.post(
            f"/api/app/devices/{created['id']}/ota/upgrade",
            headers=self.auth_headers(),
        )

        self.assertEqual(upgrade_response.status, 200)
        payload = await upgrade_response.json()
        self.assertFalse(payload["requested"])
        self.assertFalse(payload["device_online"])
        self.assertIn("不在线", payload["message"])

    @unittest_run_loop
    async def test_debug_chat_uses_current_prompt_and_writes_dialogue(self):
        await self.client.post(
            "/api/app/devices/bind",
            data=json.dumps({"device_code": "123456"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        await self.client.put(
            "/api/app/devices/baize_dev_001/settings",
            data=json.dumps(
                {
                    "baize_nickname": "小白泽",
                    "user_call_name": "小伙伴",
                    "personality_mode": "gentle",
                }
            ),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )

        response = await self.client.post(
            "/api/app/devices/baize_dev_001/debug/chat",
            data=json.dumps({"text": "你是谁呀？"}),
            headers={**self.auth_headers(), "Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(
            payload["reply"],
            "我是白泽幼灵呀，来自上古神话世界。很高兴认识你，我的新小伙伴。",
        )

        self.assertEqual(len(self.llm_instances), 1)
        llm_instance = self.llm_instances[0]
        self.assertEqual(llm_instance.config["model_name"], "demo-model")
        self.assertEqual(llm_instance.calls[0]["user_prompt"], "你是谁呀？")
        self.assertIn("白泽幼灵", llm_instance.calls[0]["system_prompt"])
        self.assertIn("神兽白泽的幼年形态", llm_instance.calls[0]["system_prompt"])
        self.assertIn("默认称呼用户为小伙伴", llm_instance.calls[0]["system_prompt"])
        self.assertIn("小白泽", llm_instance.calls[0]["system_prompt"])
        self.assertIn("小伙伴", llm_instance.calls[0]["system_prompt"])
        self.assertIn("gentle", llm_instance.calls[0]["system_prompt"])
        self.assertIn("<app_device_settings>", llm_instance.calls[0]["system_prompt"])
        self.assertNotIn("小智/小志", llm_instance.calls[0]["system_prompt"])
        self.assertNotIn("称呼用户为主人", llm_instance.calls[0]["system_prompt"])

        dialogues_response = await self.client.get(
            "/api/app/devices/baize_dev_001/dialogues", headers=self.auth_headers()
        )
        dialogues_payload = await dialogues_response.json()
        self.assertEqual(dialogues_payload["items"][0]["user_text"], "你是谁呀？")
        self.assertEqual(
            dialogues_payload["items"][0]["baize_text"],
            "我是白泽幼灵呀，来自上古神话世界。很高兴认识你，我的新小伙伴。",
        )


if __name__ == "__main__":
    unittest.main()
