#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import openai
import websockets

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


async def smoke_tts(config):
    name = config["selected_module"]["TTS"]
    tts = config["TTS"][name]
    api_key = os.getenv(tts.get("api_key_env", "")) or tts.get("api_key")
    if not api_key and tts.get("api_key_from"):
        api_key = resolve_path(config, tts["api_key_from"])
    if not api_key:
        raise ValueError("TTS API Key 未配置")

    task_id = uuid.uuid4().hex
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-DashScope-DataInspection": "enable",
    }
    audio_bytes = 0
    async with websockets.connect(
        tts["ws_url"],
        additional_headers=headers,
        open_timeout=20,
        close_timeout=10,
        max_size=10 * 1024 * 1024,
    ) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "header": {
                        "action": "run-task",
                        "task_id": task_id,
                        "streaming": "duplex",
                    },
                    "payload": {
                        "task_group": "audio",
                        "task": "tts",
                        "function": "SpeechSynthesizer",
                        "model": tts["model"],
                        "parameters": {
                            "text_type": "PlainText",
                            "voice": tts["voice"],
                            "format": "pcm",
                            "sample_rate": 16000,
                            "volume": tts.get("volume", 50),
                            "rate": tts.get("rate", 1.0),
                            "pitch": tts.get("pitch", 1.0),
                        },
                        "input": {},
                    },
                },
                ensure_ascii=False,
            )
        )

        while True:
            message = await asyncio.wait_for(websocket.recv(), timeout=30)
            if not isinstance(message, str):
                continue
            header = json.loads(message).get("header", {})
            event = header.get("event")
            if event == "task-failed":
                raise RuntimeError(
                    f"TTS 启动失败: {header.get('error_code')} {header.get('error_message')}"
                )
            if event == "task-started":
                break

        await websocket.send(
            json.dumps(
                {
                    "header": {
                        "action": "continue-task",
                        "task_id": task_id,
                        "streaming": "duplex",
                    },
                    "payload": {"input": {"text": "你好，我是白泽。"}},
                },
                ensure_ascii=False,
            )
        )
        await websocket.send(
            json.dumps(
                {
                    "header": {
                        "action": "finish-task",
                        "task_id": task_id,
                        "streaming": "duplex",
                    },
                    "payload": {"input": {}},
                }
            )
        )

        while True:
            message = await asyncio.wait_for(websocket.recv(), timeout=30)
            if isinstance(message, (bytes, bytearray)):
                audio_bytes += len(message)
                continue
            header = json.loads(message).get("header", {})
            event = header.get("event")
            if event == "task-failed":
                raise RuntimeError(
                    f"TTS 合成失败: {header.get('error_code')} {header.get('error_message')}"
                )
            if event == "task-finished":
                break
    if audio_bytes <= 0:
        raise RuntimeError("TTS 冒烟未收到音频")
    return audio_bytes


def main():
    parser = argparse.ArgumentParser(description="验证白泽线上 LLM/TTS 模型")
    parser.add_argument("--config", required=True, help="待验证的私有配置")
    args = parser.parse_args()

    config = merge_configs(read_config("config.yaml"), read_config(args.config))
    llm_chars = smoke_llm(config)
    tts_bytes = asyncio.run(smoke_tts(config))
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
