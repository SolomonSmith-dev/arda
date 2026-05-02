from __future__ import annotations

from agents.finrod.embeddings import MockEmbedder
from agents.finrod.store import InMemoryStore


def test_add_returns_count():
    store = InMemoryStore()
    e = MockEmbedder()
    n = store.add(["a", "b"], ["text a", "text b"], e.embed(["text a", "text b"]))
    assert n == 2
    assert store.count() == 2


def test_search_returns_top_k_in_score_order():
    store = InMemoryStore()
    e = MockEmbedder()
    texts = ["alpha", "beta", "gamma", "delta"]
    store.add(texts, texts, e.embed(texts))

    [query_vec] = e.embed(["alpha"])
    results = store.search(query_vec, top_k=2)
    assert len(results) == 2
    assert results[0].text == "alpha"
    assert results[0].score >= results[1].score


def test_search_self_query_returns_perfect_match():
    store = InMemoryStore()
    e = MockEmbedder()
    store.add(["doc-1"], ["unique target text"], e.embed(["unique target text"]))

    [query_vec] = e.embed(["unique target text"])
    results = store.search(query_vec, top_k=1)
    assert results[0].id == "doc-1"
    assert abs(results[0].score - 1.0) < 1e-6


def test_search_empty_store_returns_empty():
    store = InMemoryStore()
    e = MockEmbedder()
    results = store.search(e.embed(["anything"])[0], top_k=5)
    assert results == []


def test_metadata_round_trips():
    store = InMemoryStore()
    e = MockEmbedder()
    store.add(["x"], ["body"], e.embed(["body"]), [{"source": "fixture"}])
    [hit] = store.search(e.embed(["body"])[0], top_k=1)
    assert hit.metadata == {"source": "fixture"}
