"""Round-trip test for long-term memory through Finrod.

MockEmbedder is hash-based: identical text -> cosine 1.0, anything else
-> effectively random. So this test uses the exact same text for ingest
and query to verify the *plumbing* (Finrod integration, metadata
filtering, score-floor gate). Semantic quality is the job of the real
sentence-transformers embedder.
"""

from __future__ import annotations

import pytest

from agents.finrod.agent import Finrod
from agents.finrod.embeddings import MockEmbedder
from agents.finrod.store import InMemoryStore
from agents.tombombadil import memory
from agents.tombombadil.identity import Tier, Viewer


@pytest.fixture
def finrod_in_memory(monkeypatch):
    instance = Finrod(store=InMemoryStore(), embedder=MockEmbedder())
    monkeypatch.setattr(memory, "_get_finrod", lambda: instance)
    return instance


def _viewer(canonical: str | None, discord_id: str = "111") -> Viewer:
    if canonical:
        return Viewer(discord_id=discord_id, discord_name=canonical, canonical_name=canonical, tier=Tier.SOLOMON)
    return Viewer(discord_id=discord_id, discord_name="stranger", canonical_name=None, tier=Tier.STRANGER)


@pytest.mark.asyncio
async def test_remember_then_recall_roundtrip(finrod_in_memory):
    solomon = _viewer("Solomon Smith")
    fact = "user loves Tarkovsky"
    ok = await memory.remember_fact(solomon, fact, source_channel="tom:hist:ch:1")
    assert ok

    # Use the same text Finrod stored ("[Solomon Smith] {fact}") to get
    # a cosine of 1.0 from the hash-based mock embedder.
    recalled = await memory.recall_facts(solomon, f"[{solomon.canonical_name}] {fact}")
    assert recalled, "expected the fact to be recalled"
    assert any("tarkovsky" in r.lower() for r in recalled)


@pytest.mark.asyncio
async def test_recall_filters_by_viewer(finrod_in_memory):
    solomon = _viewer("Solomon Smith", discord_id="111")
    brian = _viewer("Brian", discord_id="222")

    await memory.remember_fact(solomon, "user loves Tarkovsky", source_channel="x")
    await memory.remember_fact(brian, "user loves Tarkovsky", source_channel="x")

    # Query with Brian's namespaced text; only Brian's fact should come back.
    recalled = await memory.recall_facts(brian, "[Brian] user loves Tarkovsky")
    assert recalled
    # And not Solomon's, even though the underlying text matches.
    recalled_solo = await memory.recall_facts(solomon, "[Solomon Smith] user loves Tarkovsky")
    assert recalled_solo
    # The cross-namespace query must not return the other viewer's entry.
    crossover = await memory.recall_facts(brian, "[Solomon Smith] user loves Tarkovsky")
    assert all("solomon" not in r.lower() for r in crossover)


@pytest.mark.asyncio
async def test_stranger_cannot_remember_or_recall(finrod_in_memory):
    stranger = _viewer(None)
    ok = await memory.remember_fact(stranger, "loved that movie", source_channel="x")
    assert ok is False
    recalled = await memory.recall_facts(stranger, "loved that movie")
    assert recalled == []


@pytest.mark.asyncio
async def test_recall_score_floor_filters_low_relevance(finrod_in_memory):
    solomon = _viewer("Solomon Smith")
    await memory.remember_fact(solomon, "user prefers slow cinema", source_channel="x")

    # Query with completely unrelated text -> mock embedder gives ~0.
    recalled = await memory.recall_facts(solomon, "completely unrelated random topic 1234")
    assert recalled == []
