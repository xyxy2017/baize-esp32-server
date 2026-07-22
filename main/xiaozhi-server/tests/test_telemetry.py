import unittest

from core import telemetry


class TelemetryTest(unittest.TestCase):
    def test_metrics_payload_contains_baize_metrics(self):
        telemetry.observe_http_request("GET", "/healthz", 200, 0.02)
        telemetry.websocket_opened()
        telemetry.websocket_closed("success")
        telemetry.dialogue_persisted("debug", "happy")
        telemetry.energy_spent(5, "debug_chat")
        telemetry.diary_generated()
        telemetry.set_sqlite_health(True)
        telemetry.set_business_snapshot(
            {
                "users": 2,
                "bound_devices": 1,
                "dialogues": 3,
                "diaries": 1,
                "energy_consumed": 5,
                "emotion_hits": {"happy": 2},
            }
        )

        body, content_type = telemetry.metrics_payload()
        rendered = body.decode("utf-8")

        self.assertIn("text/plain", content_type)
        self.assertIn("baize_http_requests_total", rendered)
        self.assertIn('baize_dialogues_total{emotion="happy",source="debug"}', rendered)
        self.assertIn("baize_sqlite_healthy 1.0", rendered)
        self.assertIn('baize_emotion_hits{emotion="happy"} 2.0', rendered)
