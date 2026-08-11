"""Optional OpenAI-compatible embedding adapter for Memory V2."""

from __future__ import annotations

import json
import math
import os
import urllib.request
from typing import Iterable


class OpenAICompatibleEmbeddingAdapter:
    def __init__(self, settings: dict):
        base_url = str(settings.get("base_url", "")).strip().rstrip("/")
        model = str(settings.get("model", "")).strip()
        if not base_url or not model:
            raise ValueError("semantic.base_url 和 semantic.model 必须配置")
        self.endpoint = (
            base_url if base_url.endswith("/embeddings") else f"{base_url}/embeddings"
        )
        self.model = model
        env_name = str(settings.get("api_key_env", "")).strip()
        self.api_key = str(settings.get("api_key", "")).strip() or (
            os.environ.get(env_name, "") if env_name else ""
        )
        self.timeout = max(0.5, float(settings.get("timeout_seconds", 5)))

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        inputs = [str(text)[:2000] for text in texts]
        if not inputs:
            return []
        body = json.dumps(
            {"model": self.model, "input": inputs}, ensure_ascii=False
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint, data=body, headers=headers, method="POST"
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ValueError("embedding_response_missing_data")
        ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
        vectors = [item.get("embedding") for item in ordered]
        if len(vectors) != len(inputs) or any(not isinstance(item, list) for item in vectors):
            raise ValueError("embedding_response_invalid_vectors")
        return [[float(value) for value in vector] for vector in vectors]


def create_embedding_adapter(settings: dict):
    provider = str(settings.get("provider", "openai_compatible")).strip()
    if provider != "openai_compatible":
        raise ValueError(f"unsupported semantic provider: {provider}")
    return OpenAICompatibleEmbeddingAdapter(settings)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    similarity = sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
    return max(0.0, min(1.0, similarity))
