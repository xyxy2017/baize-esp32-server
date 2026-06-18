from config.logger import setup_logging
from core.providers.tts.base import TTSProviderBase


TAG = __name__
logger = setup_logging()


class TTSProvider(TTSProviderBase):
    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume", 0, 100, 50, int),
        ("ttsRate", "rate", 0.5, 2.0, 1.0, lambda v: round(float(v), 2)),
        ("ttsPitch", "pitch", 0.5, 2.0, 1.0, lambda v: round(float(v), 2)),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.api_key = config.get("api_key")
        self.model = config.get("model", "sambert-zhiying-v1")
        self.audio_file_type = config.get("format", "wav")
        self.output_file = config.get("output_dir", "tmp/")

        volume = config.get("volume", "55")
        self.volume = int(volume) if volume else 55

        rate = config.get("rate", "0.95")
        self.rate = float(rate) if rate else 0.95

        pitch = config.get("pitch", "1.08")
        self.pitch = float(pitch) if pitch else 1.08

        self._apply_percentage_params(config)

        if not self.api_key:
            logger.bind(tag=TAG).error("AliBL Sambert TTS api_key is required")

    async def text_to_speak(self, text, output_file):
        import dashscope
        dashscope.api_key = self.api_key

        from dashscope.audio.tts import SpeechSynthesizer

        result = SpeechSynthesizer.call(
            model=self.model,
            text=text,
            format=self.audio_file_type,
            sample_rate=self.conn.sample_rate,
            volume=self.volume,
            rate=self.rate,
            pitch=self.pitch,
        )
        audio_data = result.get_audio_data()
        if audio_data is None:
            raise Exception(f"AliBL Sambert TTS请求失败: {result.get_response()}")
        if output_file:
            with open(output_file, "wb") as audio_file:
                audio_file.write(audio_data)
            return output_file
        return audio_data
