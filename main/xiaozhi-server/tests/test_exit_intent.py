import sys
import types
import unittest
from unittest.mock import patch


logger_module = types.ModuleType("config.logger")


class _NoopLogger:
    def bind(self, **_kwargs):
        return self

    def debug(self, *_args, **_kwargs):
        pass

    def info(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


logger_module.setup_logging = lambda: _NoopLogger()
sys.modules.setdefault("config.logger", logger_module)

from plugins_func.functions.handle_exit_intent import (
    SHORT_GOODBYES,
    handle_exit_intent,
)
from plugins_func.register import Action


class _FakeConnection:
    close_after_chat = False


class ExitIntentTest(unittest.TestCase):
    def test_preserves_a_short_goodbye(self):
        conn = _FakeConnection()

        response = handle_exit_intent(conn, "  下次见！  ")

        self.assertTrue(conn.close_after_chat)
        self.assertEqual(response.action, Action.RESPONSE)
        self.assertEqual(response.response, "下次见！")

    def test_replaces_a_long_goodbye_with_a_short_variant(self):
        conn = _FakeConnection()

        with patch(
            "plugins_func.functions.handle_exit_intent.random.choice",
            return_value=SHORT_GOODBYES[1],
        ):
            response = handle_exit_intent(
                conn,
                "时间过得真快，转眼又到了该说再见的时候，真舍不得啊。",
            )

        self.assertEqual(response.response, "白泽先歇会儿。")
        self.assertLessEqual(len(response.response), 16)

    def test_all_fallback_variants_are_short(self):
        self.assertGreaterEqual(len(SHORT_GOODBYES), 5)
        self.assertTrue(all(len(text) <= 16 for text in SHORT_GOODBYES))


if __name__ == "__main__":
    unittest.main()
