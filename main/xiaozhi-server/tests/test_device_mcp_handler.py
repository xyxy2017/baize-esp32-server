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

from core.api.app_demo_store import load_state
from core.providers.tools.device_mcp.mcp_handler import (
    MCPClient,
    _extract_battery_percent,
    _refresh_device_status_report,
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
                return "当前电量为 77%"

            with patch(
                "core.providers.tools.device_mcp.mcp_handler.call_mcp_tool",
                new=fake_call_tool,
            ):
                await _refresh_device_status_report(conn, mcp_client)

            device = load_state(state_path)["devices"]["baize_dev_001"]
            self.assertEqual(device["battery_percent"], 77)

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

            device = load_state(state_path)["devices"]["baize_dev_001"]
            self.assertEqual(device["source_device_id"], "68:ee:8f:5c:71:54")
            self.assertEqual(device["client_id"], "client-mcp-001")
            self.assertEqual(device["model"], "zhengchen_eye")
            self.assertEqual(device["firmware_version"], "1.8.5")


if __name__ == "__main__":
    unittest.main()
