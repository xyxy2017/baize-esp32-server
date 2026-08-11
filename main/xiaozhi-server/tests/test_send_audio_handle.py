import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.handle.sendAudioHandle import send_tts_message


class SendTtsMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_checks_audio_success_after_queue_is_drained(self):
        logger = Mock()
        logger.bind.return_value = logger
        conn = SimpleNamespace(
            sentence_id="sentence-1",
            tts_successful_sentence_id=None,
            config={"enable_stop_tts_notify": False},
            session_id="session-1",
            websocket=SimpleNamespace(send=AsyncMock()),
            logger=logger,
            current_metrics=None,
            clearSpeakStatus=Mock(),
            _complete_staged_app_dialogue=Mock(return_value=True),
        )

        async def finish_queued_audio(_conn):
            _conn.tts_successful_sentence_id = "sentence-1"

        with patch(
            "core.handle.sendAudioHandle._wait_for_audio_completion",
            side_effect=finish_queued_audio,
        ):
            await send_tts_message(conn, "stop")

        conn._complete_staged_app_dialogue.assert_called_once_with(
            "sentence-1", True
        )


if __name__ == "__main__":
    unittest.main()
