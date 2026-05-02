from __future__ import annotations

import math

from agents.finrod.embeddings import EMBED_DIM, MockEmbedder


def test_embed_returns_correct_dim():
    e = MockEmbedder()
    out = e.embed(["hello"])
    assert len(out) == 1
    assert len(out[0]) == EMBED_DIM


def test_embed_is_deterministic():
    e = MockEmbedder()
    a = e.embed(["same text"])
    b = e.embed(["same text"])
    assert a == b


def test_embed_normalizes_to_unit_length():
    e = MockEmbedder()
    [vec] = e.embed(["normalize me"])
    norm = math.sqrt(sum(v * v for v in vec))
    assert abs(norm - 1.0) < 1e-6


def test_embed_distinguishes_different_texts():
    e = MockEmbedder()
    a = e.embed(["alpha"])[0]
    b = e.embed(["beta"])[0]
    cos = sum(x * y for x, y in zip(a, b, strict=True))
    assert abs(cos) < 0.5


def test_embed_batch_matches_single():
    e = MockEmbedder()
    batch = e.embed(["one", "two"])
    one = e.embed(["one"])[0]
    two = e.embed(["two"])[0]
    assert batch[0] == one
    assert batch[1] == two
