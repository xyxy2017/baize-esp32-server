import json
import unittest
from unittest.mock import patch

from core.memory_embedding import (
    OpenAICompatibleEmbeddingAdapter,
    cosine_similarity,
    create_embedding_adapter,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class MemoryEmbeddingTest(unittest.TestCase):
    def test_openai_compatible_adapter_orders_vectors_and_sends_auth(self):
        adapter = OpenAICompatibleEmbeddingAdapter(
            {
                "base_url": "https://embedding.example/v1",
                "model": "embedding-model",
                "api_key": "test-key",
                "timeout_seconds": 3,
            }
        )
        response = _Response(
            {
                "data": [
                    {"index": 1, "embedding": [0, 1]},
                    {"index": 0, "embedding": [1, 0]},
                ]
            }
        )
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            vectors = adapter.embed(["问题", "记忆"])

        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://embedding.example/v1/embeddings")
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        self.assertEqual(json.loads(request.data)["model"], "embedding-model")

    def test_factory_validation_and_cosine_similarity(self):
        with self.assertRaises(ValueError):
            create_embedding_adapter({"provider": "unsupported"})
        self.assertEqual(cosine_similarity([1, 0], [1, 0]), 1.0)
        self.assertEqual(cosine_similarity([1, 0], [0, 1]), 0.0)
        self.assertEqual(cosine_similarity([], []), 0.0)


if __name__ == "__main__":
    unittest.main()
