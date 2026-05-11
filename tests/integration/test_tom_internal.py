"""Spec 4.4: Tom Bombadil internal (per-message) flows."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.tombombadil import agent as tom_agent
from agents.tombombadil import memory
from agents.tombombadil.identity import resolve as resolve_viewer


@pytest.mark.asyncio
async def test_recall_returns_stored_fact_for_owner(
    identity_yaml, fake_redis, finrod_in_memory, solomon
):
    """Spec 4.4.1: a previously remembered fact comes back when queried
    with the same wording (MockEmbedder = cosine 1.0 on identity)."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    await memory.remember_fact(viewer, "user loves Tarkovsky", source_channel="x")
    recalled = await memory.recall_facts(viewer, "[Solomon Smith] user loves Tarkovsky")
    assert any("tarkovsky" in r.lower() for r in recalled)


@pytest.mark.asyncio
async def test_recall_filters_by_viewer(
    identity_yaml, fake_redis, finrod_in_memory, solomon, brian
):
    """Spec 4.4.1 / 5.4: recall returns only the requesting viewer's
    facts, never another user's."""
    sv = resolve_viewer(str(solomon.id), str(solomon))
    bv = resolve_viewer(str(brian.id), str(brian))
    await memory.remember_fact(sv, "solomon loves Tarkovsky", source_channel="x")
    await memory.remember_fact(bv, "brian loves Get Out", source_channel="x")
    # Cross-namespace query returns no Solomon-attributed entries to Brian.
    crossover = await memory.recall_facts(bv, "[Solomon Smith] solomon loves Tarkovsky")
    assert all("solomon" not in r.lower() for r in crossover)


@pytest.mark.asyncio
async def test_recall_score_floor_filters_irrelevant(
    identity_yaml, fake_redis, finrod_in_memory, solomon
):
    """Spec 4.4.1: matches below RECALL_SCORE_FLOOR (0.35) are dropped."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    await memory.remember_fact(viewer, "user prefers slow cinema", source_channel="x")
    recalled = await memory.recall_facts(viewer, "completely unrelated random topic 1234")
    assert recalled == []


@pytest.mark.asyncio
async def test_stranger_recall_returns_empty(
    identity_yaml, fake_redis, finrod_in_memory, stranger
):
    """Spec 4.4.1: strangers have no canonical_name and therefore no facts."""
    viewer = resolve_viewer(str(stranger.id), str(stranger))
    recalled = await memory.recall_facts(viewer, "anything")
    assert recalled == []


@pytest.mark.asyncio
async def test_suppress_films_pref_swaps_film_block(
    identity_yaml, fake_redis, fake_bot_user, brian, guild_channel
):
    """Spec 4.4.2: suppress_films=1 replaces the film summary with the
    'has asked you NOT to bring up films' line in the system prompt."""
    viewer = resolve_viewer(str(brian.id), str(brian))
    memory.set_pref(fake_redis, viewer.discord_id, "suppress_films", "1")
    captured: dict = {}

    def fake_invoke(messages):
        captured["sys"] = "\n".join(
            m.content for m in messages if m.__class__.__name__ == "SystemMessage"
        )
        from agents._mock_llm import _MockResponse
        return _MockResponse(content="ok")

    with patch.object(tom_agent._llm, "invoke", side_effect=fake_invoke):
        await tom_agent.get_response(
            f"tom:hist:ch:{guild_channel.id}", "hi", viewer, fake_redis
        )
    assert "asked you NOT to bring up films unprompted" in captured["sys"]


@pytest.mark.asyncio
async def test_do_not_log_pref_skips_fact_extractor(
    identity_yaml, fake_redis, solomon
):
    """Spec 4.4.2: do_not_log=1 means a 'remember that...' message never
    persists a fact."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    memory.set_pref(fake_redis, viewer.discord_id, "do_not_log", "1")
    await tom_agent.get_response(
        "tom:hist:ch:1", "remember that I'm allergic to subtitles",
        viewer, fake_redis,
    )
    # No NoteDraft queue entry, no pref change beyond do_not_log itself.
    assert fake_redis.llen("tom:drafts:scope:tom:hist:ch:1") == 0


@pytest.mark.asyncio
async def test_roster_block_lists_all_film_db_people(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.4.3: the system prompt includes every FILM_DATABASE person
    so Tom can answer cross-user questions (e.g. 'how did Anthony rate?')
    without inventing data."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    captured: dict = {}

    def fake_invoke(messages):
        captured["sys"] = "\n".join(
            m.content for m in messages if m.__class__.__name__ == "SystemMessage"
        )
        from agents._mock_llm import _MockResponse
        return _MockResponse(content="ok")

    with patch.object(tom_agent._llm, "invoke", side_effect=fake_invoke):
        await tom_agent.get_response("tom:hist:ch:99", "hi", viewer, fake_redis)
    for name in ("Solomon Smith", "Anthony Taylor", "Brian", "Gavin", "Isis", "G"):
        assert name in captured["sys"]
