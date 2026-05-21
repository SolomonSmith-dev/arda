from __future__ import annotations

import fakeredis
import pytest

from agents.tombombadil import agent as tom_agent


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(tom_agent, "get_redis_sync", lambda: r)
    return r


@pytest.mark.asyncio
async def test_get_response_uses_mock_llm():
    reply = await tom_agent.get_response("chan-1", "Tell me about Ran")
    assert isinstance(reply, str)
    assert "[mock:" in reply


@pytest.mark.asyncio
async def test_get_response_empty_input():
    reply = await tom_agent.get_response("chan-1", "   ")
    assert reply == "Please provide a message"


def test_acknowledge_notes_parse_error_returns_message():
    reply = tom_agent.acknowledge_notes("not a valid note")
    assert "Film required" in reply or "Rating required" in reply


def test_acknowledge_notes_saves_to_redis(fake_redis):
    reply = tom_agent.acknowledge_notes(
        "Name: Solomon\nFilm: Ran\nRating: 9\nReaction: masterpiece"
    )
    assert reply.startswith("OK")
    assert "Ran" in reply
    assert fake_redis.sismember("films", "Ran")
    assert fake_redis.sismember("watchers", "Solomon")


def test_acknowledge_notes_dedup(fake_redis):
    note = "Name: Solomon\nFilm: La Haine\nRating: 10"
    first = tom_agent.acknowledge_notes(note)
    second = tom_agent.acknowledge_notes(note)
    assert first.startswith("OK")
    assert "Duplicate" in second


def test_direct_film_facts_extracts_known_title():
    # "Ran" is in the seed FILM_DATABASE with Solomon Smith as a watcher.
    facts = tom_agent._direct_film_facts("What did I rate Ran?")
    assert facts is not None
    assert "Ran" in facts
    assert "Solomon Smith rated this" in facts


def test_direct_film_facts_word_boundary_avoids_substring_match():
    # "Ran" should not match inside "ranking" or "errant".
    assert tom_agent._direct_film_facts("show me my ranking") is None
    assert tom_agent._direct_film_facts("errant query") is None


def test_direct_film_facts_returns_none_for_unknown_film():
    assert tom_agent._direct_film_facts("how about CompletelyMadeUpFilm9000?") is None


def test_direct_film_facts_handles_multi_word_title():
    facts = tom_agent._direct_film_facts("thoughts on La Haine?")
    assert facts is not None
    assert "La Haine" in facts


def test_system_messages_injects_facts_block_when_text_mentions_film():
    msgs = tom_agent._system_messages("How did I rate La Haine?")
    contents = "\n".join(m.content for m in msgs)
    assert "VERIFIED RATINGS" in contents
    assert "La Haine" in contents


def test_system_messages_omits_facts_block_when_no_film_mentioned():
    msgs = tom_agent._system_messages("how are you today?")
    contents = "\n".join(m.content for m in msgs)
    assert "VERIFIED RATINGS" not in contents
