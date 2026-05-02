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
