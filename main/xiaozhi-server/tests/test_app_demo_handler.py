import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import web
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

from core.api.app_demo_handler import AppDemoHandler, DEMO_TOKEN
from core.api.health_handler import HealthHandler
from core.api.ota_handler import OTAHandler


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
                return "😶 我是白泽幼灵呀，来自上古神话世界。很高兴认识你，我的新小伙伴。"

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
            "app_mvp": {"admin_phones": ["13800138001"], "firmware_bin_dir": self.bin_dir},
            "firmware_cache_ttl": 30,
            "prompt": "你是白泽幼灵，来自上古神话世界，是神兽白泽的幼年形态。你默认称呼用户为小伙伴，也可以偶尔使用朋友或伙伴，不使用主人称呼。",
            "prompt_template": str(prompt_template_path),
            "selected_module": {"LLM": "MockLLM", "TTS": "EdgeTTS"},
            "LLM": {"MockLLM": {"type": "mock_llm", "model_name": "demo-model"}},
            "TTS": {"EdgeTTS": {"language": "中文"}},
        }
        self.refreshed_connections = []
        self.demo_runs = []

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
        )
        self.handler = handler
        health_handler = HealthHandler(self.config)
        ota_handler = OTAHandler(self.config)
        ota_handler.bin_dir = self.bin_dir
        app = web.Application()
        app.add_routes(handler.routes())
        app.add_routes(health_handler.routes())
        app.add_routes([web.post("/xiaozhi/ota/", ota_handler.handle_post)])
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
        self.assertEqual(me_payload["energy"]["current"], 30)
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
        self.assertEqual(me_payload["role"], "user")

        update_response = await self.client.post(
            "/api/app/me/password",
            data=json.dumps({"old_password": "secret1", "new_password": "secret2"}),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        self.assertEqual(update_response.status, 200)

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
    async def test_demo_bound_users_can_read_shared_real_device_dialogues(self):
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
        self.assertEqual(payload["items"][0]["user_text"], "我前面有跟你聊过天吗？")
        self.assertEqual(payload["items"][0]["source_device_id"], "68:ee:8f:5c:71:54")

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

        list_response = await self.client.get(
            "/api/app/devices/baize_dev_001/diaries", headers=self.auth_headers()
        )
        self.assertEqual(list_response.status, 200)
        payload = await list_response.json()
        self.assertEqual(payload["items"][0]["id"], diary["id"])

    @unittest_run_loop
    async def test_generate_diary_uses_shared_real_device_dialogues_in_demo_mode(self):
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

        self.assertEqual(response.status, 200)
        diary = await response.json()
        self.assertEqual(diary["dialogue_count"], 1)
        self.assertIn("日记功能", diary["summary"])

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
        self.assertIn('question="白泽，你在吗？"', metrics.format_summary())
        self.assertIn('answer="我在呀。"', metrics.format_summary())

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
    async def test_demo_auto_binds_first_online_device_to_all_existing_ios_accounts(self):
        from core.api.app_demo_store import update_device_report

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

        for token in (DEMO_TOKEN, alice_token, bob_token):
            list_response = await self.client.get(
                "/api/app/devices",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(list_response.status, 200)
            payload = await list_response.json()
            self.assertTrue(any(item["id"] == created["id"] for item in payload["items"]))

        detail_response = await self.client.get(
            f"/api/app/devices/{created['id']}",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        self.assertEqual(detail_response.status, 200)
        detail = await detail_response.json()
        self.assertEqual(detail["source_device_id"], "68:ee:8f:5c:71:55")
        self.assertEqual(detail["battery_percent"], 88)

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

        app_ota_response = await self.client.get(
            "/api/app/devices/baize_dev_001/ota", headers=self.auth_headers()
        )
        app_ota = await app_ota_response.json()
        self.assertEqual(app_ota["current_version"], "2.0.1")
        self.assertEqual(app_ota["latest_version"], "2.1.0")
        self.assertTrue(app_ota["update_available"])
        self.assertIn("发现可用固件版本 2.1.0", app_ota["release_note"])

    @unittest_run_loop
    async def test_app_ota_payload_scans_uploaded_firmware_without_device_ota_check(self):
        from core.api.app_demo_store import update_device_report

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
    async def test_app_ota_upgrade_reports_offline_device_without_error(self):
        from core.api.app_demo_store import update_device_report

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
