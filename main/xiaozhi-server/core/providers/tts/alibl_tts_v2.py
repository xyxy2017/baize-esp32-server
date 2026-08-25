from urllib.parse import urlparse

from config.logger import setup_logging
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
