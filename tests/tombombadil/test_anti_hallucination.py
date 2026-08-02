"""Anti-hallucination guards for Tom's film ratings.

Tom confabulates ratings even with the full rating list in context: he
rounds 9/10 to 10/10, and invents scores for films nobody logged. The
7K-token summary is too big a haystack to retrieve from reliably.

The fix is to do the retrieval deterministically -- scan the incoming
message for known titles and inject a short, pre-resolved block of
verified facts for *this* query -- and to drop sampling temperature so
the model stops embellishing what it was handed.

Ported from the deploy host (f5192f9), which was written against the
now-removed Groq client. See PR #52 for that divergence.
"""

from __future__ import annotations

import fakeredis
import pytest

from agents.tombombadil import agent as tom_agent
from agents.tombombadil import memory as tom_memory
from agents.tombombadil.identity import Tier, Viewer

SOLOMON = Viewer(
    discord_id="111",
    discord_name="Solomon",
    canonical_name="Solomon Smith",
    tier=Tier.SOLOMON,
)


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(tom_agent, "get_redis_sync", lambda: r)
    return r


@pytest.fixture(autouse=True)
def stub_long_term_memory(monkeypatch):
    async def _no_facts(*_args, **_kwargs):
        return []

    async def _no_op(*_args, **_kwargs):
        return True

    monkeypatch.setattr(tom_memory, "recall_facts", _no_facts)
    monkeypatch.setattr(tom_memory, "remember_fact", _no_op)


# --- _verified_film_facts ------------------------------------------------


def test_quotes_the_exact_rating_without_rounding():
    """9 must surface as 9/10. Rounding to 10/10 is the actual bug."""
    facts = tom_agent._verified_film_facts("what did I think of Ran?", "Solomon Smith")

    assert facts is not None
    assert "Ran: Solomon Smith rated this 9/10" in facts


def test_reports_unrated_film_rather_than_inventing_a_score():
    facts = tom_agent._verified_film_facts("thoughts on Ghost Dog?", "Brian")

    assert facts is not None
    assert "has NOT rated this" in facts
    assert "/10" not in facts


def test_returns_none_when_no_known_title_is_mentioned():
    assert tom_agent._verified_film_facts("how are you today?", "Solomon Smith") is None


def test_matches_on_word_boundaries_not_substrings():
    """`Ran` must not fire inside `Ranger`, or every message trips it."""
    assert tom_agent._verified_film_facts("I met a park Ranger", "Solomon Smith") is None


def test_matches_case_insensitively():
    facts = tom_agent._verified_film_facts("rewatched la haine last night", "Solomon Smith")

    assert facts is not None
    assert "La Haine: Solomon Smith rated this 10/10" in facts


def test_deduplicates_repeated_titles():
    facts = tom_agent._verified_film_facts("Ran, and again Ran", "Solomon Smith")

    assert facts is not None
    assert facts.count("Ran: Solomon Smith rated") == 1


# --- system prompt wiring ------------------------------------------------


def test_system_prompt_carries_verified_block_for_a_mentioned_film():
    prompt = tom_agent._build_system_prompt(SOLOMON, {}, [], "did I like Ran?")

    assert "VERIFIED RATINGS FOR THIS QUERY" in prompt
    assert "Ran: Solomon Smith rated this 9/10" in prompt


def test_system_prompt_omits_verified_block_without_a_film_mention():
    prompt = tom_agent._build_system_prompt(SOLOMON, {}, [], "hey there")

    assert "VERIFIED RATINGS FOR THIS QUERY" not in prompt


def test_film_summary_block_states_the_strict_lookup_rules():
    block = tom_agent._film_summary_block(SOLOMON, {})

    assert block is not None
    assert "ONLY source of truth" in block
    assert "Do NOT guess" in block


def test_verified_block_suppressed_when_user_opted_out_of_films():
    """suppress_films must win; the guard cannot smuggle ratings back in."""
    prompt = tom_agent._build_system_prompt(
        SOLOMON, {"suppress_films": "1"}, [], "did I like Ran?"
    )

    assert "VERIFIED RATINGS FOR THIS QUERY" not in prompt


# --- sampling ------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_call_uses_low_temperature(fake_redis):
    await tom_agent.get_response("scope-temp", "hello there", SOLOMON, redis_client=fake_redis)

    assert tom_agent._llm.calls, "expected the mock chat client to record a call"
    assert tom_agent._llm.calls[-1]["kwargs"]["temperature"] == 0.2
