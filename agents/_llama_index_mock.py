"""Deterministic hash-based BaseEmbedding for tests that need semantic
discrimination (i.e., identical text -> cosine ~1, unrelated text ~0).

LlamaIndex ships a `MockEmbedding` that returns a constant vector for
every input -- fine for "did retrieval return *something*" plumbing
tests, but it kills tests that depend on a score floor or on ranking.
This subclass mirrors the old hand-rolled `MockEmbedder` semantics
(SHA-256 -> 384 floats -> L2-normalize) over the LlamaIndex
`BaseEmbedding` interface.
"""

from __future__ import annotations

import hashlib
import math

from llama_index.core.embeddings import BaseEmbedding

DEFAULT_DIM = 384


def _hash_embed(text: str, dim: int = DEFAULT_DIM) -> list[float]:
    seed = text.encode("utf-8")
    floats: list[float] = []
    counter = 0
    while len(floats) < dim:
        digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for i in range(0, len(digest), 2):
            if len(floats) >= dim:
                break
            v = int.from_bytes(digest[i : i + 2], "big") / 65535.0 - 0.5
            floats.append(v)
        counter += 1
    norm = math.sqrt(sum(f * f for f in floats)) or 1.0
    return [f / norm for f in floats]


class HashEmbedding(BaseEmbedding):
    """L2-normalized SHA-256-derived embeddings. Identical text -> cosine 1.0;
    unrelated text -> cosine near zero. Deterministic, no network."""

    def _get_query_embedding(self, query: str) -> list[float]:
        return _hash_embed(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return _hash_embed(text)

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embed(t) for t in texts]

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return _hash_embed(query)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return _hash_embed(text)
