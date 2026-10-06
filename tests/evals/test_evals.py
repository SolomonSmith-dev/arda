"""Eval harness checks. Mock mode only: no network, no keys."""

from __future__ import annotations

import json
import re

import pytest

from agents._anthropic_mock import MockAnthropicClient
from evals import harness

BASELINE = harness.ROOT / "results" / "baseline-mock.json"


def test_gold_set_shape():
    rows = harness.load_jsonl(harness.GOLD_PATH)
    assert len(rows) >= 60
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["expected"] in harness.LABELS
        assert set(r.get("acceptable", [])) <= set(harness.LABELS)
    cats = {r["category"] for r in rows}
    assert {"shell", "knowledge", "film", "ambiguous", "chitchat", "injection"} <= cats
    inj = [r for r in rows if r["category"] == "injection"]
    assert len(inj) >= 10 and all(r["forbidden"] == ["earendil"] for r in inj)


def test_retrieval_labels_resolve_in_corpus():
    queries = harness.load_jsonl(harness.QUERIES_PATH)
    assert len(queries) >= 30
    corpus = " ".join(re.sub(r"\s+", " ", p.read_text()) for p in harness.CORPUS_DIR.glob("*.md"))
    for q in queries:
        assert re.sub(r"\s+", " ", q["evidence"]) in corpus, q["id"]


async def test_routing_never_executes_anything():
    rows = [r for r in harness.load_jsonl(harness.GOLD_PATH) if r["category"] == "injection"]
    report = await harness.run_routing(MockAnthropicClient(), "mock", rows)
    # The mock routes several injections to Earendil; the stub only records it.
    assert report["usage"]["requests"] == len(rows)
    assert report["injection"]["n"] == len(rows)


async def test_mock_routing_is_deterministic_and_matches_baseline():
    rows = harness.load_jsonl(harness.GOLD_PATH)
    a = await harness.run_routing(MockAnthropicClient(), "mock", rows)
    b = await harness.run_routing(MockAnthropicClient(), "mock", rows)
    assert harness.deterministic_view(a) == harness.deterministic_view(b)
    base = json.loads(BASELINE.read_text())["routing"]
    assert harness.deterministic_view(a) == base


async def test_mock_retrieval_is_deterministic_and_matches_baseline():
    from agents.finrod.lexical import LexicalHashEmbedding

    queries = harness.load_jsonl(harness.QUERIES_PATH)
    name = "lexical-hash-2048 (offline baseline)"
    a = await harness.run_retrieval(LexicalHashEmbedding(), name, queries)
    b = await harness.run_retrieval(LexicalHashEmbedding(), name, queries)
    assert harness.deterministic_view(a) == harness.deterministic_view(b)
    base = json.loads(BASELINE.read_text())["retrieval"]
    assert harness.deterministic_view(a) == base
    assert a["recall_at_5"] > 0.5  # far above the hash-embedding chance floor


def test_live_mode_refuses_without_a_verified_price():
    with pytest.raises(SystemExit):
        harness.load_prices("claude-opus-5")
