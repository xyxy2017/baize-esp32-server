import time
import uuid
import json


class ConversationMetrics:
    def __init__(self, conversation_id=None, clock=None):
        self.conversation_id = conversation_id or uuid.uuid4().hex[:8]
        self._clock = clock or time.perf_counter
        self._start = self._clock()
        self._last = self._start
        self.events = []
        self.tts_segments = 0
        self.opus_frames = 0
        self.opus_bytes = 0
        self.first_audio_sent = False
        self.first_response_ms = None
        self.question = ""
        self.answer = ""

    @staticmethod
    def _clean_text(value, limit=160):
        text = str(value or "")
        text = " ".join(text.split())
        if len(text) > limit:
            return text[:limit] + "..."
        return text

    @staticmethod
    def _quote(value):
        return json.dumps(value, ensure_ascii=False)

    def set_question(self, text):
        self.question = self._clean_text(text)

    def set_answer(self, text):
        self.answer = self._clean_text(text)

    def mark(self, name, **fields):
        now = self._clock()
        event = {
            "name": name,
            "elapsed_ms": round((now - self._start) * 1000, 1),
            "delta_ms": round((now - self._last) * 1000, 1),
        }
        event.update(fields)
        self.events.append(event)
        self._last = now
        if name == "first_audio_sent" and self.first_response_ms is None:
            self.first_response_ms = event["elapsed_ms"]
        return event

    def add_opus_frame(self, frame):
        self.opus_frames += 1
        self.opus_bytes += len(frame or b"")

    def summary(self):
        return {
            "conversation_id": self.conversation_id,
            "total_ms": self.events[-1]["elapsed_ms"] if self.events else 0,
            "events": self.events,
            "tts_segments": self.tts_segments,
            "opus_frames": self.opus_frames,
            "opus_bytes": self.opus_bytes,
            "first_response_ms": self.first_response_ms,
            "question": self.question,
            "answer": self.answer,
        }

    def format_summary(self):
        parts = []
        for event in self.events:
            detail = ", ".join(
                f"{key}={value}"
                for key, value in event.items()
                if key not in {"name", "elapsed_ms", "delta_ms"}
            )
            suffix = f" ({detail})" if detail else ""
            parts.append(
                f"{event['name']} +{event['delta_ms']}ms / {event['elapsed_ms']}ms{suffix}"
            )
        return (
            f"[conversation_metrics] id={self.conversation_id} "
            f"total={self.summary()['total_ms']}ms "
            f"first_response_ms={self.first_response_ms if self.first_response_ms is not None else '-'} "
            f"tts_segments={self.tts_segments} "
            f"opus_frames={self.opus_frames} "
            f"opus_bytes={self.opus_bytes} "
            f"question={self._quote(self.question)} "
            f"answer={self._quote(self.answer)} | "
            + " -> ".join(parts)
        )
