#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

import dashscope
import openai
from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config_loader import merge_configs, read_config


def resolve_path(config, path):
    value = config
    for segment in path.split("."):
        value = value[segment]
    return value


def smoke_llm(config):
    name = config["selected_module"]["LLM"]
    llm = config["LLM"][name]
    client = openai.OpenAI(
        api_key=llm["api_key"],
        base_url=llm["base_url"],
        timeout=60,
    )
    try:
        response = client.chat.completions.create(
            model=llm["model_name"],
            messages=[{"role": "user", "content": "只回复：模型切换成功"}],
            max_tokens=24,
            temperature=1.0,
            extra_body={"enable_thinking": False},
        )
        text = response.choices[0].message.content or ""
        if not text.strip():
            raise RuntimeError("LLM 冒烟返回空文本")
        return len(text)
    finally:
        client.close()


def smoke_tts(config):
    name = config["selected_module"]["TTS"]
    tts = config["TTS"][name]
    api_key = None
    if tts.get("api_key_from"):
        api_key = resolve_path(config, tts["api_key_from"])
    if not api_key and tts.get("api_key_env"):
        api_key = os.getenv(tts["api_key_env"])
    if not api_key:
        api_key = tts.get("api_key")
    if not api_key:
        raise ValueError("TTS API Key 未配置")

    dashscope.api_key = api_key
    dashscope.base_websocket_api_url = tts["ws_url"]
    if tts.get("http_url"):
        dashscope.base_http_api_url = tts["http_url"]

    synthesizer = SpeechSynthesizer(
        model=tts["model"],
        voice=tts["voice"],
        format=AudioFormat.WAV_16000HZ_MONO_16BIT,
        volume=int(tts.get("volume", 50)),
        speech_rate=float(tts.get("rate", 1.0)),
        pitch_rate=float(tts.get("pitch", 1.0)),
        url=tts["ws_url"],
    )
    audio_data = synthesizer.call("你好，我是白泽。", timeout_millis=30000)
    if not audio_data:
        raise RuntimeError("TTS 冒烟未收到音频")
    return len(audio_data)


def main():
    parser = argparse.ArgumentParser(description="验证白泽线上 LLM/TTS 模型")
    parser.add_argument("--config", required=True, help="待验证的私有配置")
    args = parser.parse_args()

    config = merge_configs(read_config("config.yaml"), read_config(args.config))
    llm_chars = smoke_llm(config)
    tts_bytes = smoke_tts(config)
    print(
        json.dumps(
            {
                "llm_model": config["LLM"][config["selected_module"]["LLM"]][
                    "model_name"
                ],
                "llm_chars": llm_chars,
                "tts_model": config["TTS"][config["selected_module"]["TTS"]][
                    "model"
                ],
                "tts_bytes": tts_bytes,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
