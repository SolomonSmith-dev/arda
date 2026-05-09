from __future__ import annotations

import hashlib
import math
from typing import Protocol

EMBED_DIM = 384


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class MockEmbedder:
    """Deterministic hash-based embedder. Same text -> same vector.

    Vectors are L2-normalized 384-dim floats derived from SHA-256 of
    the text. Cosine similarity equals 1.0 for identical text and is
    effectively random for non-identical text. Good enough for
    structural / round-trip tests; not a substitute for real semantic
    embeddings.
    """

    def __init__(self, dim: int = EMBED_DIM):
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        seed = text.encode("utf-8")
        floats: list[float] = []
        counter = 0
        while len(floats) < self.dim:
            digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            for i in range(0, len(digest), 2):
                if len(floats) >= self.dim:
                    break
                v = int.from_bytes(digest[i : i + 2], "big") / 65535.0 - 0.5
                floats.append(v)
            counter += 1

        norm = math.sqrt(sum(f * f for f in floats)) or 1.0
        return [f / norm for f in floats]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class SentenceTransformerEmbedder:
    """Wraps sentence-transformers/all-MiniLM-L6-v2 (384 dim). Lazy-loaded."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.dim = EMBED_DIM
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        return model.encode(texts, normalize_embeddings=True).tolist()


def get_embedder() -> Embedder:
    from core.config import settings

    if settings.mock_embedder_enabled:
        return MockEmbedder()
    return SentenceTransformerEmbedder()
