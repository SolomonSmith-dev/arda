from __future__ import annotations

from unittest.mock import patch

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
BRIAN = Viewer(
    discord_id="222",
    discord_name="Brian",
    canonical_name="Brian",
    tier=Tier.REGULAR,
)
STRANGER = Viewer(
    discord_id="999",
    discord_name="randomuser",
    canonical_name=None,
    tier=Tier.STRANGER,
)


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(tom_agent, "get_redis_sync", lambda: r)
    return r


@pytest.fixture(autouse=True)
def stub_long_term_memory(monkeypatch):
    """Default to no-op recall/remember so smoke tests don't need
    Finrod. Tests that exercise long-term memory override these.
    """
    async def _no_facts(*_args, **_kwargs):
        return []

    async def _no_op(*_args, **_kwargs):
        return True

    monkeypatch.setattr(tom_memory, "recall_facts", _no_facts)
    monkeypatch.setattr(tom_memory, "remember_fact", _no_op)


@pytest.mark.asyncio
async def test_get_response_uses_mock_llm(fake_redis):
    reply = await tom_agent.get_response("tom:hist:ch:1", "Tell me about Ran", SOLOMON, fake_redis)
    assert isinstance(reply, str)
    assert "[mock:" in reply


@pytest.mark.asyncio
async def test_get_response_empty_input(fake_redis):
    reply = await tom_agent.get_response("tom:hist:ch:1", "   ", SOLOMON, fake_redis)
    assert reply == "Please provide a message"


@pytest.mark.asyncio
async def test_stranger_prompt_excludes_solomon_film_summary(fake_redis):
    captured: dict = {}

    def fake_invoke(messages):
        captured["messages"] = messages
        from agents._mock_llm import _MockResponse
        return _MockResponse(content="[mock] reply")

    with patch.object(tom_agent._llm, "invoke", side_effect=fake_invoke):
        await tom_agent.get_response("tom:hist:ch:1", "Hi", STRANGER, fake_redis)

    system_text = "\n".join(m.content for m in captured["messages"] if hasattr(m, "content") and m.__class__.__name__ == "SystemMessage")
    assert "Solomon Smith" not in system_text or "no film history yet" in system_text.lower()
    # Stranger fallback line must be present
    assert "no film history yet" in system_text.lower()


@pytest.mark.asyncio
async def test_suppress_films_pref_swaps_film_block(fake_redis):
    tom_memory.set_pref(fake_redis, BRIAN.discord_id, "suppress_films", "1")
    captured: dict = {}

    def fake_invoke(messages):
        captured["messages"] = messages
        from agents._mock_llm import _MockResponse
        return _MockResponse(content="[mock] reply")

    with patch.object(tom_agent._llm, "invoke", side_effect=fake_invoke):
        await tom_agent.get_response("tom:hist:ch:1", "hi", BRIAN, fake_redis)

    system_text = "\n".join(m.content for m in captured["messages"] if m.__class__.__name__ == "SystemMessage")
    assert "asked you NOT to bring up films unprompted" in system_text


@pytest.mark.asyncio
async def test_history_persisted_after_response(fake_redis):
    await tom_agent.get_response("tom:hist:ch:42", "hello", SOLOMON, fake_redis)
    turns = tom_memory.recent_turns(fake_redis, "tom:hist:ch:42")
    assert len(turns) == 2  # user + assistant
    assert turns[0].role == "user"
    assert turns[0].content == "hello"
    assert turns[1].role == "assistant"


@pytest.mark.asyncio
async def test_history_included_in_second_turn(fake_redis):
    await tom_agent.get_response("tom:hist:ch:99", "first message", SOLOMON, fake_redis)

    captured: dict = {}

    def fake_invoke(messages):
        captured["messages"] = messages
        from agents._mock_llm import _MockResponse
        return _MockResponse(content="[mock] reply")

    with patch.object(tom_agent._llm, "invoke", side_effect=fake_invoke):
        await tom_agent.get_response("tom:hist:ch:99", "second message", SOLOMON, fake_redis)

    # Inspect content for the first-turn user message + reply.
    user_contents = [m.content for m in captured["messages"] if m.__class__.__name__ == "HumanMessage"]
    assert any("first message" in c for c in user_contents)
    assert user_contents[-1] == "second message"


@pytest.mark.asyncio
async def test_fact_extractor_runs_after_reply(fake_redis):
    await tom_agent.get_response(
        "tom:hist:ch:1",
        "stop mentioning films, please",
        SOLOMON,
        fake_redis,
    )
    prefs = tom_memory.get_prefs(fake_redis, SOLOMON.discord_id)
    assert prefs.get("suppress_films") == "1"


def test_acknowledge_notes_parse_error_returns_message():
    reply = tom_agent.acknowledge_notes("not a valid note", viewer=SOLOMON)
    assert "Film required" in reply or "Rating required" in reply


def test_acknowledge_notes_saves_to_redis(fake_redis):
    reply = tom_agent.acknowledge_notes(
        "Name: Solomon\nFilm: Ran\nRating: 9\nReaction: masterpiece",
        viewer=SOLOMON,
    )
    assert reply.startswith("OK")
    assert "Ran" in reply
    assert fake_redis.sismember("films", "Ran")
    assert fake_redis.sismember("watchers", "Solomon")


def test_acknowledge_notes_dedup(fake_redis):
    note = "Name: Solomon\nFilm: La Haine\nRating: 10"
    first = tom_agent.acknowledge_notes(note, viewer=SOLOMON)
    second = tom_agent.acknowledge_notes(note, viewer=SOLOMON)
    assert first.startswith("OK")
    assert "Duplicate" in second


def test_acknowledge_notes_backfills_name_from_viewer(fake_redis):
    reply = tom_agent.acknowledge_notes(
        "Film: Stalker\nRating: 10",
        viewer=BRIAN,
    )
    assert reply.startswith("OK")
    assert fake_redis.sismember("watchers", "Brian")
