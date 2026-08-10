import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

logger_module = types.ModuleType("config.logger")


class _NoopLogger:
    def bind(self, **kwargs):
        return self

    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


logger_module.setup_logging = lambda: _NoopLogger()
sys.modules.setdefault("config.logger", logger_module)
sys.modules.setdefault("opuslib_next", types.ModuleType("opuslib_next"))

from core.api.app_demo_store import DEMO_USER_ID, bind_device, create_device, load_state, update_settings
from core.providers.tools.device_mcp.mcp_handler import (
    MCPClient,
    _extract_activity_status,
    _extract_battery_percent,
    _refresh_device_status_report,
    _replay_saved_hardware_settings,
    handle_mcp_message,
)


async def _no_sleep(*_args, **_kwargs):
    return None


class _FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


class _FakeConnection:
    def __init__(self, state_path):
        self.config = {"app_demo": {"state_path": state_path}}
        self.device_id = "68:ee:8f:5c:71:54"
        self.headers = {"client-id": "client-mcp-001"}
        self.features = {"mcp": True}
        self.websocket = _FakeWebSocket()


class DeviceMCPHandlerTest(unittest.IsolatedAsyncioTestCase):
    def test_extracts_battery_percent_from_device_status_payloads(self):
        self.assertEqual(_extract_battery_percent({"battery": 72}), 72)
        self.assertEqual(_extract_battery_percent({"battery_percent": "88%"}), 88)
        self.assertEqual(_extract_battery_percent("当前电量为 64%"), 64)
        self.assertEqual(
            _extract_battery_percent(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"audio_speaker":{"volume":100},"battery":{"level":100,"charging":false}}',
                        }
                    ],
                    "isError": False,
                }
            ),
            100,
        )
        self.assertIsNone(_extract_battery_percent({"screen": {"brightness": 30}}))

    def test_extracts_activity_status_from_device_status_payloads(self):
        self.assertEqual(_extract_activity_status({"device_state": "idle"}), "idle")
        self.assertEqual(_extract_activity_status({"state": "speaking"}), "speaking")
        self.assertEqual(
            _extract_activity_status(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"device_state":"idle","battery":{"level":100,"charging":false}}',
                        }
                    ],
                    "isError": False,
                }
            ),
            "idle",
        )

    async def test_refresh_device_status_report_writes_battery_percent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "state.json")
            conn = _FakeConnection(state_path)
            mcp_client = MCPClient()
            await mcp_client.set_ready(True)
            await mcp_client.add_tool(
                {
                    "name": "self.get_device_status",
                    "description": "status",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            )

            async def fake_call_tool(*_args, **_kwargs):
                return '{"battery":{"level":77},"device_state":"idle"}'

            with patch(
                "core.providers.tools.device_mcp.mcp_handler.call_mcp_tool",
                new=fake_call_tool,
            ):
                await _refresh_device_status_report(conn, mcp_client)

            devices = load_state(state_path)["devices"]
            device = next(item for item in devices.values() if item["source_device_id"] == "68:ee:8f:5c:71:54")
            self.assertRegex(device["device_code"], r"^\d{6}$")
            self.assertEqual(device["battery_percent"], 77)
            self.assertEqual(device["activity_status"], "idle")
            self.assertEqual(conn.activity_status, "idle")

    async def test_mcp_server_info_updates_app_device_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "state.json")
            conn = _FakeConnection(state_path)
            mcp_client = MCPClient()

            with patch("asyncio.sleep", new=_no_sleep):
                await handle_mcp_message(
                    conn,
                    mcp_client,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "serverInfo": {
                                "name": "zhengchen_eye",
                                "version": "1.8.5",
                            }
                        },
                    },
                )

            devices = load_state(state_path)["devices"]
            device = next(item for item in devices.values() if item["source_device_id"] == "68:ee:8f:5c:71:54")
            self.assertRegex(device["device_code"], r"^\d{6}$")
            self.assertEqual(device["source_device_id"], "68:ee:8f:5c:71:54")
            self.assertEqual(device["client_id"], "client-mcp-001")
            self.assertEqual(device["model"], "zhengchen_eye")
            self.assertEqual(device["firmware_version"], "1.8.5")

    async def test_replays_settings_saved_while_device_was_offline_after_mcp_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "state.json")
            conn = _FakeConnection(state_path)
            device = create_device(
                conn.config,
                source_device_id=conn.device_id,
                client_id=conn.headers["client-id"],
            )
            bind_device(conn.config, DEMO_USER_ID, device["device_code"])
            update_settings(
                conn.config,
                DEMO_USER_ID,
                device["id"],
                {"speaker_volume": 55, "screen_brightness": 65},
            )

            mcp_client = MCPClient()
            await mcp_client.set_ready(True)
            for tool_name in (
                "self_audio_speaker_set_volume",
                "self_screen_set_brightness",
            ):
                await mcp_client.add_tool(
                    {
                        "name": tool_name,
                        "description": "settings",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                )

            calls = []

            async def fake_call_tool(_conn, _client, tool_name, args, timeout):
                calls.append((tool_name, args, timeout))

            with patch(
                "core.providers.tools.device_mcp.mcp_handler.call_mcp_tool",
                new=fake_call_tool,
            ):
                await _replay_saved_hardware_settings(conn, mcp_client)

            self.assertCountEqual(
                calls,
                [
                    ("self_audio_speaker_set_volume", {"volume": 55}, 5),
                    ("self_screen_set_brightness", {"brightness": 65}, 5),
                ],
            )


if __name__ == "__main__":
    unittest.main()
