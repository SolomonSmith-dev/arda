"""Spec 5: Tom Bombadil cross-cutting concerns."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.tombombadil import agent as tom_agent
from agents.tombombadil import bot as tom_bot
from agents.tombombadil import memory


async def _send_mention(channel, user, text, *, bot_user):
    from tests.integration.conftest import make_message
    content = f"<@{bot_user.id}> {text}"
    msg = make_message(user, channel, content, mentions=[bot_user])
    await tom_bot.on_message(msg)
    return msg


@pytest.mark.asyncio
async def test_concurrent_mentions_history_interleaves(
    identity_yaml, fake_redis, fake_bot_user, solomon, brian, guild_channel
):
    """Spec 5.2: two users mentioning Tom back-to-back both get replies
    and both turn-pairs land in the shared channel history list in
    arrival order."""
    await _send_mention(guild_channel, solomon, "hello from solomon", bot_user=fake_bot_user)
    await _send_mention(guild_channel, brian, "hello from brian", bot_user=fake_bot_user)
    turns = memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}")
    # 4 turns: solomon-user, solomon-assistant, brian-user, brian-assistant
    assert len(turns) == 4
    assert turns[0].viewer == "Solomon Smith"
    assert turns[2].viewer == "Brian"


@pytest.mark.asyncio
async def test_separate_rate_limit_buckets_per_user(
    identity_yaml, fake_redis, fake_bot_user, brian, wes, guild_channel
):
    """Spec 5.2: per-user token buckets isolate rate limits. Burning
    Brian's budget doesn't lock out Wes."""
    from agents.tombombadil import guards
    for _ in range(guards.RATE_LIMIT_MAX_TOKENS):
        await _send_mention(guild_channel, brian, "hi", bot_user=fake_bot_user)
    blocked = await _send_mention(guild_channel, brian, "again", bot_user=fake_bot_user)
    free = await _send_mention(guild_channel, wes, "hi", bot_user=fake_bot_user)
    assert "Easy there" in blocked.reply_log[-1]
    assert "Easy there" not in free.reply_log[-1]


@pytest.mark.asyncio
async def test_dm_history_isolated_from_channel(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel, dm_channel
):
    """Spec 5.2 / 5.4: DM context never leaks into a channel and vice versa."""
    await _send_mention(dm_channel, solomon, "private question", bot_user=fake_bot_user)
    await _send_mention(guild_channel, solomon, "public question", bot_user=fake_bot_user)
    dm_turns = memory.recent_turns(fake_redis, f"tom:hist:dm:{solomon.id}")
    ch_turns = memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}")
    assert any("private question" in t.content for t in dm_turns)
    assert all("public question" not in t.content for t in dm_turns)
    assert any("public question" in t.content for t in ch_turns)
    assert all("private question" not in t.content for t in ch_turns)


@pytest.mark.xfail(
    strict=True,
    reason="D7: stranger onboarding paragraph not specialised. First-contact "
    "currently uses the generic film-aware reply; spec 5.1 wants greeting + "
    "concrete next action.",
)
@pytest.mark.asyncio
async def test_stranger_first_contact_includes_greeting_and_suggestion(
    identity_yaml, fake_redis, fake_bot_user, stranger, guild_channel
):
    """Spec 5.1: a stranger's very first message produces a reply that:
    - greets by display name
    - names what Tom does
    - suggests one concrete next action (mentions /whoami or 'tell me what you've watched')
    """
    from agents.tombombadil import bot as tom_bot
    from tests.integration.conftest import make_message
    content = f"<@{fake_bot_user.id}> hi"
    msg = make_message(stranger, guild_channel, content, mentions=[fake_bot_user])
    await tom_bot.on_message(msg)
    reply = msg.reply_log[0].lower()
    assert stranger.display_name.lower() in reply
    assert "film" in reply or "club" in reply
    assert "/whoami" in reply or "what you've been watching" in reply or "tell me" in reply


@pytest.mark.asyncio
async def test_llm_timeout_returns_canned_no_history(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 5.3 LLM timeout: returns 'LLM timeout, try again' and writes
    NO history (so retries don't accumulate phantom turns)."""
    def raise_timeout(*_a, **_k):
        raise TimeoutError("simulated")

    with patch.object(tom_agent._llm, "invoke", side_effect=raise_timeout):
        msg = await _send_mention(guild_channel, solomon, "hi", bot_user=fake_bot_user)
    assert "LLM timeout" in msg.reply_log[-1]
    assert memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}") == []


@pytest.mark.asyncio
async def test_llm_empty_content_returns_canned_no_history(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 5.3 LLM empty: 'No response generated', no history."""
    from agents._mock_llm import _MockResponse

    with patch.object(tom_agent._llm, "invoke", return_value=_MockResponse(content="")):
        msg = await _send_mention(guild_channel, solomon, "hi", bot_user=fake_bot_user)
    assert msg.reply_log[-1] == "No response generated"
    assert memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}") == []


@pytest.mark.asyncio
async def test_llm_arbitrary_exception_returns_canned(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 5.3 LLM crash: 'Error processing your request', no history."""
    with patch.object(tom_agent._llm, "invoke", side_effect=RuntimeError("boom")):
        msg = await _send_mention(guild_channel, solomon, "hi", bot_user=fake_bot_user)
    assert "Error processing" in msg.reply_log[-1]
    assert memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}") == []


@pytest.mark.asyncio
async def test_finrod_query_failure_falls_back_to_no_recall(
    identity_yaml, fake_redis, fake_bot_user, finrod_in_memory, solomon, guild_channel
):
    """Spec 5.3 Finrod outage: recall_facts returns [] on backend errors
    and the reply continues without recall context."""
    async def broken_run(*_a, **_k):
        from core.models import AgentResult, TaskStatus
        return AgentResult(task_id="x", agent="finrod", status=TaskStatus.FAILED, error="down")

    finrod_in_memory.run = broken_run  # type: ignore[method-assign]
    msg = await _send_mention(guild_channel, solomon, "tell me about Ran", bot_user=fake_bot_user)
    assert msg.reply_log  # still replies; recall failure is non-fatal
