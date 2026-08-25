#!/usr/bin/env python3
import argparse
import os
import stat
import tempfile
from pathlib import Path

import yaml


LLM_BASE_URL = (
    "https://llm-ej3dxplcnblp9594.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
LLM_MODEL = "deepseek-v4-flash-0731"
TTS_WS_URL = (
    "wss://llm-ej3dxplcnblp9594.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
)
TTS_HTTP_URL = (
    "https://llm-ej3dxplcnblp9594.cn-beijing.maas.aliyuncs.com/api/v1"
)
TTS_MODEL = "cosyvoice-v3.5-flash"
TTS_VOICE = (
    "cosyvoice-v3.5-flash-vd-bailian-1a8fa0b31f764a76879ccc1a20ad7b73"
)


def apply_model_config(config):
    selected = config.setdefault("selected_module", {})
    selected["LLM"] = "AliLLM"
    selected["TTS"] = "AliBLTTS"

    llm = config.setdefault("LLM", {}).setdefault("AliLLM", {})
    if not llm.get("api_key"):
        raise ValueError("LLM.AliLLM.api_key 未配置，不能切换线上模型")
    llm["type"] = "openai"
    llm["base_url"] = LLM_BASE_URL
    llm["model_name"] = LLM_MODEL

    tts = config.setdefault("TTS", {}).setdefault("AliBLTTS", {})
    tts.update(
        {
            "type": "alibl_tts_v2",
            "api_key_from": "LLM.AliLLM.api_key",
            "ws_url": TTS_WS_URL,
            "http_url": TTS_HTTP_URL,
            "model": TTS_MODEL,
            "voice": TTS_VOICE,
            "format": "pcm",
            "volume": 50,
            "rate": 1.0,
            "pitch": 1.0,
            "output_dir": "tmp/",
        }
    )
    tts.pop("api_key", None)
    tts.pop("api_key_env", None)
    return config


def write_yaml_atomic(config, output_path):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = stat.S_IMODE(output.stat().st_mode) if output.exists() else 0o600
    fd, temp_path = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            yaml.safe_dump(
                config,
                file,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        os.chmod(temp_path, existing_mode)
        os.replace(temp_path, output)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def main():
    parser = argparse.ArgumentParser(description="更新白泽线上 LLM/TTS 模型配置")
    parser.add_argument("--source", required=True, help="现有私有配置文件")
    parser.add_argument("--output", required=True, help="候选或最终配置文件")
    args = parser.parse_args()

    with open(args.source, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    write_yaml_atomic(apply_model_config(config), args.output)
    print(f"model config updated: {args.output}")


if __name__ == "__main__":
    main()
