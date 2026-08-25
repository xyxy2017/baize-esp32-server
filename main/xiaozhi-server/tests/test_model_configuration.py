import asyncio
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import yaml

# 部署回归会先加载 app 测试；该测试为避免本地原生 Opus 依赖会预注册空模块。
# 在这里补齐导入 TTS Provider 所需的最小符号，不执行实际音频编码。
opus_module = sys.modules.get("opuslib_next")
if opus_module is not None and not hasattr(opus_module, "Encoder"):
    opus_module.Encoder = type("Encoder", (), {})
    opus_module.constants = types.SimpleNamespace(
        APPLICATION_AUDIO=2049,
        SIGNAL_VOICE=3001,
    )

from config.config_loader import merge_configs
from core.providers.tts.alibl_tts_v2 import TTSProvider
from core.utils.modules_initialize import initialize_tts
from scripts.update_model_config import (
    LLM_BASE_URL,
    LLM_MODEL,
    TTS_MODEL,
    TTS_VOICE,
    TTS_HTTP_URL,
    TTS_WS_URL,
    apply_model_config,
)


class ModelConfigurationTest(unittest.TestCase):
    def test_cosyvoice_accepts_private_websocket_endpoint(self):
        provider = TTSProvider(
            {
                "api_key": "test-key",
                "ws_url": "wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
                "model": "cosyvoice-v3.5-flash",
                "voice": "cosyvoice-v3.5-flash-vd-test",
            },
            True,
        )
        self.assertEqual(provider.model, "cosyvoice-v3.5-flash")
        self.assertEqual(provider.voice, "cosyvoice-v3.5-flash-vd-test")
        self.assertEqual(
            provider.ws_url,
            "wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
        )

    def test_cosyvoice_rejects_invalid_websocket_endpoint(self):
        with self.assertRaisesRegex(ValueError, "ws_url"):
            TTSProvider(
                {
                    "api_key": "test-key",
                    "voice": "cosyvoice-v3.5-flash-vd-test",
                    "ws_url": "https://example.com/api",
                },
                True,
            )

    def test_cosyvoice_uses_official_tts_v2_sdk(self):
        provider = TTSProvider(
            {
                "api_key": "test-key",
                "ws_url": "wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
                "http_url": "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
                "model": "cosyvoice-v3.5-flash",
                "voice": "cosyvoice-v3.5-flash-vd-test",
            },
            True,
        )
        provider.conn = types.SimpleNamespace(sample_rate=16000)
        synthesizer = MagicMock()
        synthesizer.call.return_value = b"test-wav-audio"

        with patch(
            "dashscope.audio.tts_v2.SpeechSynthesizer",
            return_value=synthesizer,
        ) as synthesizer_class:
            audio = asyncio.run(provider.text_to_speak("你好", None))

        self.assertEqual(audio, b"test-wav-audio")
        synthesizer_class.assert_called_once()
        synthesizer.call.assert_called_once_with(
            "你好", timeout_millis=provider.tts_timeout * 1000
        )

    def test_cosyvoice_streams_callback_pcm_into_audio_queue(self):
        provider = TTSProvider(
            {
                "api_key": "test-key",
                "ws_url": "wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
                "http_url": "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
                "model": "cosyvoice-v3.5-flash",
                "voice": "cosyvoice-v3.5-flash-vd-test",
            },
            True,
        )
        provider.conn = types.SimpleNamespace(
            sample_rate=16000,
            client_abort=False,
            sentence_id="sentence-1",
            current_metrics=None,
        )
        provider.current_sentence_id = "sentence-1"
        provider.opus_encoder = MagicMock()
        provider.opus_encoder.encode_pcm_to_opus_stream.side_effect = (
            lambda data, end_of_stream, callback: callback(b"opus") if data else None
        )

        def start_stream(text):
            callback = synthesizer_class.call_args.kwargs["callback"]
            callback.on_data(b"\x00\x00" * 960)

        def complete_stream(complete_timeout_millis):
            callback = synthesizer_class.call_args.kwargs["callback"]
            callback.on_complete()

        synthesizer = MagicMock()
        synthesizer.streaming_call.side_effect = start_stream
        synthesizer.streaming_complete.side_effect = complete_stream
        with patch(
            "dashscope.audio.tts_v2.SpeechSynthesizer",
            return_value=synthesizer,
        ) as synthesizer_class:
            provider.to_tts_stream("你好", opus_handler=provider.handle_opus)

        first = provider.tts_audio_queue.get_nowait()
        audio = provider.tts_audio_queue.get_nowait()
        self.assertEqual(first[0].name, "FIRST")
        self.assertEqual(first[2], "你好")
        self.assertEqual(first[3], "sentence-1")
        self.assertEqual(audio[0].name, "MIDDLE")
        self.assertEqual(audio[1], b"opus")
        self.assertEqual(audio[3], "sentence-1")
        self.assertEqual(
            provider.opus_encoder.encode_pcm_to_opus_stream.call_count, 2
        )
        synthesizer.streaming_call.assert_called_once_with("你好")
        synthesizer.streaming_complete.assert_called_once_with(
            complete_timeout_millis=provider.tts_timeout * 1000
        )

    def test_tts_can_reuse_llm_api_key_without_duplicating_secret(self):
        config = {
            "selected_module": {"TTS": "AliBLTTS"},
            "LLM": {"AliLLM": {"api_key": "shared-key"}},
            "TTS": {
                "AliBLTTS": {
                    "type": "alibl_tts_v2",
                    "api_key_from": "LLM.AliLLM.api_key",
                    "voice": "cosyvoice-v3.5-flash-vd-test",
                }
            },
        }
        provider = initialize_tts(config)
        self.assertEqual(provider.api_key, "shared-key")

    def test_tts_prefers_environment_api_key(self):
        config = {
            "selected_module": {"TTS": "AliBLTTS"},
            "LLM": {"AliLLM": {"api_key": "fallback-key"}},
            "TTS": {
                "AliBLTTS": {
                    "type": "alibl_tts_v2",
                    "api_key_env": "DASHSCOPE_API_KEY",
                    "api_key_from": "LLM.AliLLM.api_key",
                    "voice": "cosyvoice-v3.5-flash-vd-test",
                }
            },
        }
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "env-key"}):
            provider = initialize_tts(config)
        self.assertEqual(provider.api_key, "env-key")

    def test_deployment_example_selects_requested_models(self):
        with open("config.yaml", "r", encoding="utf-8") as file:
            default_config = yaml.safe_load(file)
        with open("data/.config.example.yaml", "r", encoding="utf-8") as file:
            example_config = yaml.safe_load(file)
        config = merge_configs(default_config, example_config)

        self.assertEqual(config["selected_module"]["LLM"], "AliLLM")
        self.assertEqual(config["selected_module"]["TTS"], "AliBLTTS")
        self.assertEqual(
            config["LLM"]["AliLLM"]["model_name"],
            "deepseek-v4-flash-0731",
        )
        self.assertEqual(
            config["TTS"]["AliBLTTS"]["model"], "cosyvoice-v3.5-flash"
        )
        self.assertEqual(
            config["TTS"]["AliBLTTS"]["voice"],
            "cosyvoice-v3.5-flash-vd-bailian-1a8fa0b31f764a76879ccc1a20ad7b73",
        )

    def test_private_config_update_preserves_credentials_and_switches_models(self):
        config = {
            "selected_module": {"LLM": "AliLLM", "TTS": "AliBLSambertTTS"},
            "LLM": {
                "AliLLM": {
                    "type": "openai",
                    "api_key": "keep-secret",
                    "base_url": "https://old.example/v1",
                    "model_name": "old-model",
                }
            },
            "TTS": {"AliBLSambertTTS": {"api_key": "legacy-secret"}},
        }
        updated = apply_model_config(config)

        self.assertEqual(updated["LLM"]["AliLLM"]["api_key"], "keep-secret")
        self.assertEqual(updated["LLM"]["AliLLM"]["base_url"], LLM_BASE_URL)
        self.assertEqual(updated["LLM"]["AliLLM"]["model_name"], LLM_MODEL)
        self.assertEqual(updated["selected_module"]["TTS"], "AliBLTTS")
        self.assertEqual(updated["TTS"]["AliBLTTS"]["type"], "alibl_tts_v2")
        self.assertEqual(updated["TTS"]["AliBLTTS"]["ws_url"], TTS_WS_URL)
        self.assertEqual(updated["TTS"]["AliBLTTS"]["http_url"], TTS_HTTP_URL)
        self.assertEqual(updated["TTS"]["AliBLTTS"]["model"], TTS_MODEL)
        self.assertEqual(updated["TTS"]["AliBLTTS"]["voice"], TTS_VOICE)
        self.assertNotIn("api_key", updated["TTS"]["AliBLTTS"])
        self.assertNotIn("api_key_env", updated["TTS"]["AliBLTTS"])


if __name__ == "__main__":
    unittest.main()
