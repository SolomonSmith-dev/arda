"""Deterministic lexical embedder for the offline retrieval eval.

The stock test embedders are useless for measuring retrieval: LlamaIndex's
`MockEmbedding` returns one constant vector and `HashEmbedding` hashes the whole
string, so neither puts related text near related text. This one hashes
unigrams and bigrams into a fixed-size vector (the hashing trick), so cosine
similarity tracks shared vocabulary. It is a lexical baseline, not a semantic
model, and the report says so. No network, no model download, same output on
every machine.
"""

from __future__ import annotations

import hashlib
import math
import re

from llama_index.core.embeddings import BaseEmbedding

DIM = 2048
_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their", "this", "to", "was", "what", "when", "where", "which", "who", "why", "will", "with", "you", "your"]
)


def _bucket(token: str) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
    n = int.from_bytes(digest, "big")
    return n % DIM, 1.0 if (n >> 63) & 1 else -1.0


def lexical_embed(text: str) -> list[float]:
    words = [w for w in _TOKEN.findall(text.lower()) if w not in _STOP]
    features = words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
    vec = [0.0] * DIM
    counts: dict[str, int] = {}
    for f in features:
        counts[f] = counts.get(f, 0) + 1
    for f, c in counts.items():
        idx, sign = _bucket(f)
        vec[idx] += sign * (1.0 + math.log(c))
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class LexicalHashEmbedding(BaseEmbedding):
    def _get_query_embedding(self, query: str) -> list[float]:
        return lexical_embed(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return lexical_embed(text)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return lexical_embed(query)
