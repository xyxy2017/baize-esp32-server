from urllib.parse import urlparse

from config.logger import setup_logging
from core.providers.tts.dto.dto import SentenceType
from core.utils.tts import MarkdownCleaner
from core.providers.tts.base import TTSProviderBase


TAG = __name__
logger = setup_logging()


class TTSProvider(TTSProviderBase):
    """通过 DashScope tts_v2 SDK 调用 CosyVoice 3.5。"""

    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume", 0, 100, 50, int),
        ("ttsRate", "rate", 0.5, 2.0, 1.0, lambda v: round(float(v), 2)),
        ("ttsPitch", "pitch", 0.5, 2.0, 1.0, lambda v: round(float(v), 2)),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.api_key = config.get("api_key")
        if not self.api_key:
            raise ValueError("api_key is required for CosyVoice TTS")

        self.model = config.get("model", "cosyvoice-v3.5-flash")
        self.voice = config.get("voice") or config.get("private_voice")
        if not self.voice:
            raise ValueError("voice is required for CosyVoice TTS")

        self.ws_url = config.get(
            "ws_url", "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
        ).strip()
        parsed_ws_url = urlparse(self.ws_url)
        if parsed_ws_url.scheme not in ("ws", "wss") or not parsed_ws_url.netloc:
            raise ValueError("ws_url must be a valid ws:// or wss:// URL")

        self.http_url = config.get("http_url")
        self.audio_file_type = "wav"
        self.output_file = config.get("output_dir", "tmp/")
        self.volume = int(config.get("volume", 50))
        self.rate = float(config.get("rate", 1.0))
        self.pitch = float(config.get("pitch", 1.0))
        self._apply_percentage_params(config)

    @staticmethod
    def _audio_format(sample_rate):
        from dashscope.audio.tts_v2 import AudioFormat

        formats = {
            8000: AudioFormat.WAV_8000HZ_MONO_16BIT,
            16000: AudioFormat.WAV_16000HZ_MONO_16BIT,
            22050: AudioFormat.WAV_22050HZ_MONO_16BIT,
            24000: AudioFormat.WAV_24000HZ_MONO_16BIT,
            44100: AudioFormat.WAV_44100HZ_MONO_16BIT,
            48000: AudioFormat.WAV_48000HZ_MONO_16BIT,
        }
        return formats.get(int(sample_rate), AudioFormat.WAV_16000HZ_MONO_16BIT)

    @staticmethod
    def _pcm_audio_format(sample_rate):
        from dashscope.audio.tts_v2 import AudioFormat

        formats = {
            8000: AudioFormat.PCM_8000HZ_MONO_16BIT,
            16000: AudioFormat.PCM_16000HZ_MONO_16BIT,
            22050: AudioFormat.PCM_22050HZ_MONO_16BIT,
            24000: AudioFormat.PCM_24000HZ_MONO_16BIT,
            44100: AudioFormat.PCM_44100HZ_MONO_16BIT,
            48000: AudioFormat.PCM_48000HZ_MONO_16BIT,
        }
        return formats.get(int(sample_rate), AudioFormat.PCM_16000HZ_MONO_16BIT)

    def to_tts_stream(self, text, opus_handler=None):
        """通过 SDK 回调边接收 PCM 边编码并推送，避免等待整段 WAV。"""
        import threading

        import dashscope
        from dashscope.audio.tts_v2 import ResultCallback, SpeechSynthesizer

        original_text = text
        text = MarkdownCleaner.clean_markdown(text)
        if self._correct_words_pattern:
            text = self._correct_words_pattern.sub(
                lambda match: self.correct_words[match.group(0)], text
            )
        if not text:
            return None

        metrics = getattr(self.conn, "current_metrics", None)
        if metrics:
            metrics.mark("tts_segment_start", chars=len(text))

        provider = self
        received_bytes = 0
        callback_error = None
        callback_done = threading.Event()
        first_chunk = True
        sentence_id = getattr(self, "current_sentence_id", None)

        class StreamingCallback(ResultCallback):
            def on_data(self, data):
                nonlocal received_bytes, first_chunk
                if not data or provider.conn.client_abort:
                    return
                if sentence_id and sentence_id != provider.conn.sentence_id:
                    return
                if first_chunk:
                    provider.tts_audio_queue.put(
                        (SentenceType.FIRST, [], original_text, sentence_id)
                    )
                    first_chunk = False
                received_bytes += len(data)
                provider.opus_encoder.encode_pcm_to_opus_stream(
                    data,
                    end_of_stream=False,
                    callback=opus_handler or provider.handle_opus,
                )

            def on_complete(self):
                try:
                    if (
                        provider.conn.client_abort
                        or (sentence_id and sentence_id != provider.conn.sentence_id)
                    ):
                        provider.opus_encoder.reset_state()
                    else:
                        provider.opus_encoder.encode_pcm_to_opus_stream(
                            b"",
                            end_of_stream=True,
                            callback=opus_handler or provider.handle_opus,
                        )
                finally:
                    callback_done.set()

            def on_error(self, message):
                nonlocal callback_error
                callback_error = str(message)
                callback_done.set()

        dashscope.api_key = self.api_key
        dashscope.base_websocket_api_url = self.ws_url
        if self.http_url:
            dashscope.base_http_api_url = self.http_url

        synthesizer = SpeechSynthesizer(
            model=self.model,
            voice=self.voice,
            format=self._pcm_audio_format(self.conn.sample_rate),
            volume=self.volume,
            speech_rate=self.rate,
            pitch_rate=self.pitch,
            callback=StreamingCallback(),
            url=self.ws_url,
        )
        synthesizer.streaming_call(text)
        synthesizer.streaming_complete(
            complete_timeout_millis=self.tts_timeout * 1000
        )
        if not callback_done.wait(timeout=self.tts_timeout):
            raise TimeoutError("CosyVoice TTS 回调结束等待超时")
        if callback_error:
            raise RuntimeError(f"CosyVoice TTS 回调失败: {callback_error}")
        if received_bytes <= 0:
            raise RuntimeError(
                "CosyVoice TTS 回调未返回音频，"
                f"request_id={synthesizer.get_last_request_id()}"
            )
        if metrics:
            metrics.tts_segments += 1
            metrics.mark("tts_segment_generated", bytes=received_bytes)
        return None

    async def text_to_speak(self, text, output_file):
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        dashscope.api_key = self.api_key
        if self.http_url:
            dashscope.base_http_api_url = self.http_url

        synthesizer = SpeechSynthesizer(
            model=self.model,
            voice=self.voice,
            format=self._audio_format(self.conn.sample_rate),
            volume=self.volume,
            speech_rate=self.rate,
            pitch_rate=self.pitch,
            url=self.ws_url,
        )
        audio_data = synthesizer.call(text, timeout_millis=self.tts_timeout * 1000)
        if not audio_data:
            raise RuntimeError(
                "CosyVoice TTS 请求未返回音频，"
                f"request_id={synthesizer.get_last_request_id()}"
            )
        if output_file:
            with open(output_file, "wb") as audio_file:
                audio_file.write(audio_data)
            return output_file
        return audio_data
