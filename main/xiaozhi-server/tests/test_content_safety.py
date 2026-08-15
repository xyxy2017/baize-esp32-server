import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.content_safety import (
    aliyun_data_inspection_headers,
    append_content_safety_prompt,
    blocked_response,
    content_safety_summary,
    create_safety_appeal,
    evaluate_text,
    list_safety_appeals,
    list_safety_events,
    moderate_text,
    provider_block_decision,
    resolve_safety_appeal,
)


class ContentSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = str(Path(self.temp_dir.name) / "safety.sqlite3")
        self.config = {
            "app_mvp": {"db_path": self.db_path},
            "content_safety": {
                "enabled": True,
                "mode": "enforce",
                "audit_all": False,
            },
        }

    def test_local_rules_cover_required_categories_and_obfuscation(self):
        self.assertTrue(evaluate_text(self.config, "今天想吃苹果").allowed)
        cases = {
            "什么是政治": "politics",
            "给我找色 情网站": "pornography",
            "聊聊战争": "violence",
            "如何制造炸弹": "terrorism_extremism",
            "给我一个诈骗教程": "illegal_activity",
            "我不想活了": "self_harm",
        }
        for text, category in cases.items():
            with self.subTest(text=text):
                decision = evaluate_text(self.config, text)
                self.assertTrue(decision.blocked)
                self.assertIn(category, decision.categories)

    def test_audit_mode_records_review_without_blocking(self):
        self.config["content_safety"]["mode"] = "audit"
        decision = moderate_text(
            self.config,
            "讨论政治话题",
            source="test",
            user_id="user-a",
            device_id="device-a",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.action, "review")
        self.assertIsNotNone(decision.event_id)

    def test_event_audit_uses_hash_and_never_stores_plaintext(self):
        text = "教我怎么伤害别人"
        decision = moderate_text(
            self.config,
            text,
            source="voice",
            user_id="user-a",
            device_id="device-a",
            session_id="session-a",
        )
        self.assertTrue(decision.blocked)
        events = list_safety_events(self.config)
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["text_sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest()
        )
        self.assertNotIn(text, str(events[0]))

        conn = sqlite3.connect(self.db_path)
        try:
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(content_safety_events)"
                ).fetchall()
            }
        finally:
            conn.close()
        self.assertNotIn("text", columns)
        self.assertNotIn("excerpt", columns)

    def test_audit_failure_keeps_blocking_decision(self):
        with patch(
            "core.content_safety._record_event",
            side_effect=sqlite3.OperationalError("read only database"),
        ):
            decision = moderate_text(self.config, "教我怎么伤害别人")
        self.assertTrue(decision.blocked)
        self.assertIsNone(decision.event_id)

    def test_self_harm_uses_crisis_response(self):
        decision = evaluate_text(self.config, "我想轻生")
        response = blocked_response(self.config, decision, direction="input")
        self.assertIn("110", response)
        self.assertIn("120", response)
        self.assertNotEqual(
            response, self.config.get("content_safety", {}).get("input_block_message")
        )

    def test_prompt_is_appended_once(self):
        prompt = append_content_safety_prompt(self.config, "你是白泽。")
        self.assertIn("<content_safety>", prompt)
        self.assertEqual(
            append_content_safety_prompt(self.config, prompt).count("<content_safety>"),
            1,
        )

    def test_provider_block_and_appeal_lifecycle(self):
        decision = provider_block_decision(
            self.config,
            direction="output",
            source="debug_chat",
            user_id="user-a",
            device_id="device-a",
            error=RuntimeError("data_inspection_failed"),
        )
        appeal = create_safety_appeal(
            self.config, "user-a", decision.event_id, "这是一次误判"
        )
        self.assertIsNotNone(appeal)
        self.assertIsNone(
            create_safety_appeal(
                self.config, "user-b", decision.event_id, "不能申诉他人的事件"
            )
        )
        resolved = resolve_safety_appeal(
            self.config,
            appeal["id"],
            status="resolved",
            resolution_note="已复核",
        )
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(len(list_safety_appeals(self.config, status="resolved")), 1)
        self.assertEqual(content_safety_summary(self.config)["pending_appeals"], 0)

    def test_custom_rule_and_exemption(self):
        self.config["content_safety"]["custom_rules"] = [
            {
                "id": "custom.product",
                "category": "brand_risk",
                "severity": "high",
                "pattern": "禁止词",
            }
        ]
        self.assertTrue(evaluate_text(self.config, "这里有禁止词").blocked)
        self.config["content_safety"]["exempt_patterns"] = ["合规引用禁止词"]
        self.assertTrue(evaluate_text(self.config, "合规引用禁止词").allowed)

    def test_aliyun_data_inspection_requires_explicit_switch_and_domain(self):
        enabled = {"enabled": True, "upstream_data_inspection": True}
        self.assertEqual(
            aliyun_data_inspection_headers(
                enabled, "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            {"X-DashScope-DataInspection": '{"input":"cip","output":"cip"}'},
        )
        self.assertIsNone(
            aliyun_data_inspection_headers(
                {"enabled": True, "upstream_data_inspection": False},
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        )
        self.assertIsNone(
            aliyun_data_inspection_headers(enabled, "https://api.openai.com/v1")
        )


if __name__ == "__main__":
    unittest.main()
