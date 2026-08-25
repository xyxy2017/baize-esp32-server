import sys
import types
import unittest
from unittest.mock import patch

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
from core.providers.tts.alibl_stream import TTSProvider
from core.utils.modules_initialize import initialize_tts
from scripts.update_model_config import (
    LLM_BASE_URL,
    LLM_MODEL,
    TTS_MODEL,
    TTS_VOICE,
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
                {"api_key": "test-key", "ws_url": "https://example.com/api"},
                True,
            )

    def test_tts_can_reuse_llm_api_key_without_duplicating_secret(self):
        config = {
            "selected_module": {"TTS": "AliBLTTS"},
            "LLM": {"AliLLM": {"api_key": "shared-key"}},
            "TTS": {
                "AliBLTTS": {
                    "type": "alibl_stream",
                    "api_key_from": "LLM.AliLLM.api_key",
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
                    "type": "alibl_stream",
                    "api_key_env": "DASHSCOPE_API_KEY",
                    "api_key_from": "LLM.AliLLM.api_key",
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
        self.assertEqual(updated["TTS"]["AliBLTTS"]["ws_url"], TTS_WS_URL)
        self.assertEqual(updated["TTS"]["AliBLTTS"]["model"], TTS_MODEL)
        self.assertEqual(updated["TTS"]["AliBLTTS"]["voice"], TTS_VOICE)
        self.assertNotIn("api_key", updated["TTS"]["AliBLTTS"])


if __name__ == "__main__":
    unittest.main()
