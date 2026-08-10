import queue
import unittest
from unittest.mock import patch

from core.providers.tts.dto.dto import ContentType, SentenceType
from core.voice_dialogue_gate import SPIRIT_POWER_NOTICES, enqueue_spirit_power_notice


class _FakeTTS:
    def __init__(self):
        self.tts_text_queue = queue.Queue()


class VoiceDialogueGateTest(unittest.TestCase):
    def test_spirit_power_notice_is_queued_as_tts_content(self):
        tts = _FakeTTS()
        selected_notice = SPIRIT_POWER_NOTICES[2]

        with patch("core.voice_dialogue_gate.random.choice", return_value=selected_notice) as choice:
            returned_notice = enqueue_spirit_power_notice(tts, "sentence-1")

        notice = tts.tts_text_queue.get_nowait()
        finished = tts.tts_text_queue.get_nowait()
        self.assertEqual(notice.sentence_id, "sentence-1")
        self.assertEqual(notice.sentence_type, SentenceType.MIDDLE)
        self.assertEqual(notice.content_type, ContentType.TEXT)
        self.assertEqual(notice.content_detail, selected_notice)
        self.assertEqual(returned_notice, selected_notice)
        choice.assert_called_once_with(SPIRIT_POWER_NOTICES)
        self.assertEqual(finished.sentence_type, SentenceType.LAST)
        self.assertEqual(finished.content_type, ContentType.ACTION)
        self.assertTrue(tts.tts_text_queue.empty())

    def test_spirit_power_notices_are_varied_and_explain_recovery(self):
        self.assertGreaterEqual(len(SPIRIT_POWER_NOTICES), 5)
        self.assertEqual(len(SPIRIT_POWER_NOTICES), len(set(SPIRIT_POWER_NOTICES)))
        for notice in SPIRIT_POWER_NOTICES:
            self.assertIn("灵力", notice)
            self.assertTrue("休息" in notice or "恢复" in notice or "小睡" in notice)


if __name__ == "__main__":
    unittest.main()
