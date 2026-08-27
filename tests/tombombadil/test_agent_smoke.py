from __future__ import annotations

from unittest.mock import patch

import fakeredis
import pytest

from agents._anthropic_mock import MockMessage, TextBlock
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


def _capture_create(captured: dict):
    """Return an async fake for ``client.messages.create`` that captures
    the system / messages args and replies with a deterministic stub."""

    async def _fake(*, system, messages, **_kwargs):
        captured["system"] = system
        captured["messages"] = messages
        return MockMessage(content=[TextBlock(text="[mock] reply")], stop_reason="end_turn")

    return _fake


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
    with patch.object(tom_agent._llm.messages, "create", side_effect=_capture_create(captured)):
        await tom_agent.get_response("tom:hist:ch:1", "Hi", STRANGER, fake_redis)

    system_text = captured["system"]
    # The stranger fallback line must be present...
    assert "no film history yet" in system_text.lower()
    # ...and no rating data may leak into a stranger's prompt. The old guard
    # here read:
    #     assert "Solomon Smith" not in system_text or "no film history yet" in ...
    # whose right-hand side is asserted unconditionally on the line above, so
    # it could never fail no matter what leaked.
    #
    # It cannot simply assert the owner's name is absent either: the club
    # roster block deliberately names every member so Tom attributes opinions
    # to the right speaker. What must not appear is the rating index.
    assert "/10" not in system_text
    assert "All rated films" not in system_text
    assert "Highest-rated" not in system_text


@pytest.mark.asyncio
async def test_suppress_films_pref_swaps_film_block(fake_redis):
    tom_memory.set_pref(fake_redis, BRIAN.discord_id, "suppress_films", "1")
    captured: dict = {}
    with patch.object(tom_agent._llm.messages, "create", side_effect=_capture_create(captured)):
        await tom_agent.get_response("tom:hist:ch:1", "hi", BRIAN, fake_redis)

    assert "asked you NOT to bring up films unprompted" in captured["system"]


@pytest.mark.asyncio
async def test_history_persisted_after_response(fake_redis):
    await tom_agent.get_response("tom:hist:ch:42", "hello", SOLOMON, fake_redis)
    turns = tom_memory.recent_turns(fake_redis, "tom:hist:ch:42")
    assert len(turns) == 2  # user + assistant
    assert turns[0].role == "user"
    assert turns[0].content == "hello"
    assert turns[1].role == "assistant"


@pytest.mark.asyncio
async def test_leaked_speaker_prefix_stripped_before_persist(fake_redis):
    """D1 / V6: LLM imitation of ``[Name] …`` must not land in Redis."""

    async def _leaky(*, system, messages, **_kwargs):
        return MockMessage(
            content=[TextBlock(text="[@Solomon Smith] Hello!")],
            stop_reason="end_turn",
        )

    with patch.object(tom_agent._llm.messages, "create", side_effect=_leaky):
        reply = await tom_agent.get_response("tom:hist:ch:d1", "hi", SOLOMON, fake_redis)

    assert reply == "Hello!"
    turns = tom_memory.recent_turns(fake_redis, "tom:hist:ch:d1")
    assert turns[-1].content == "Hello!"


@pytest.mark.asyncio
async def test_stale_prefixed_assistant_history_healed_on_read(fake_redis):
    """D1: pre-V6 Redis assistant rows are stripped before LLM reinjection."""
    tom_memory.append_turn(fake_redis, "tom:hist:ch:stale", SOLOMON, "user", "old")
    tom_memory.append_turn(
        fake_redis, "tom:hist:ch:stale", SOLOMON, "assistant", "[viewer] stale leak"
    )

    captured: dict = {}
    with patch.object(tom_agent._llm.messages, "create", side_effect=_capture_create(captured)):
        await tom_agent.get_response("tom:hist:ch:stale", "next", SOLOMON, fake_redis)

    assistant_msgs = [m["content"] for m in captured["messages"] if m["role"] == "assistant"]
    assert assistant_msgs
    assert assistant_msgs[0] == "stale leak"
    assert "[viewer]" not in assistant_msgs[0]


@pytest.mark.asyncio
async def test_history_included_in_second_turn(fake_redis):
    await tom_agent.get_response("tom:hist:ch:99", "first message", SOLOMON, fake_redis)

    captured: dict = {}
    with patch.object(tom_agent._llm.messages, "create", side_effect=_capture_create(captured)):
        await tom_agent.get_response("tom:hist:ch:99", "second message", SOLOMON, fake_redis)

    # Inspect content for the first-turn user message + reply.
    user_contents = [m["content"] for m in captured["messages"] if m["role"] == "user"]
    assert any("first message" in c for c in user_contents)
    assert user_contents[-1] == "second message"


@pytest.mark.asyncio
async def test_fact_extractor_persists_prefs_after_reply(fake_redis):
    await tom_agent.get_response(
        "tom:hist:ch:1",
        "stop mentioning films, please",
        SOLOMON,
        fake_redis,
    )
    prefs = tom_memory.get_prefs(fake_redis, SOLOMON.discord_id)
    assert prefs.get("suppress_films") == "1"


@pytest.mark.asyncio
async def test_rating_phrase_queues_note_draft_instead_of_saving(fake_redis):
    """PR 2: NoteDrafts go to the per-scope pending queue, not directly
    to ``save_note``. Reaction confirmation is required.
    """
    from agents.tombombadil import draft_store

    await tom_agent.get_response(
        "tom:hist:ch:42",
        "I rated Inception 9/10 last night",
        SOLOMON,
        fake_redis,
    )

    # save_note hasn't been called — film/watcher sets are empty.
    assert not fake_redis.sismember("films", "Inception")

    # The draft is sitting in the per-scope queue waiting for the bot.
    pending = draft_store.pop_pending(fake_redis, "tom:hist:ch:42")
    assert pending is not None
    assert pending.film.lower().startswith("inception")
    assert pending.rating == 9.0
    assert pending.viewer == "Solomon Smith"


@pytest.mark.asyncio
async def test_owner_prompt_does_include_the_rating_index(fake_redis):
    """Positive control for test_stranger_prompt_excludes_solomon_film_summary.

    That test asserts the rating index is absent for a stranger. Without
    this one, those assertions would still pass if the index stopped being
    generated for anybody -- which is exactly how the previous, vacuous
    guard survived.
    """
    captured: dict = {}
    with patch.object(tom_agent._llm.messages, "create", side_effect=_capture_create(captured)):
        await tom_agent.get_response("tom:hist:ch:9", "what did I rate Ran", SOLOMON, fake_redis)

    system_text = captured["system"]
    assert "/10" in system_text
    assert "Highest-rated" in system_text


@pytest.mark.asyncio
async def test_stranger_first_message_optout_is_honoured(fake_redis):
    """D7 regression: the stranger-onboarding branch returned a template
    before the fact extractor ran, so a stranger whose *first* message was an
    opt-out got the onboarding paragraph and the pref was silently dropped.
    An opt-out in a first message is the one most worth honouring.
    """
    await tom_agent.get_response(
        "tom:hist:ch:newbie",
        "stop talking about films",
        STRANGER,
        fake_redis,
        offer_stranger_onboarding=True,
    )
    prefs = tom_memory.get_prefs(fake_redis, STRANGER.discord_id)
    assert prefs.get("suppress_films") == "1", prefs
