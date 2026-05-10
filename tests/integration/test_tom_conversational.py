"""Spec 4.1: Tom Bombadil conversational flows."""

from __future__ import annotations

import pytest

from agents.tombombadil import bot as tom_bot
from agents.tombombadil import guards, memory


def test_harness_imports_cleanly(identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel):
    """Spec smoke: shared fixtures wire up without exceptions."""
    assert solomon.name == "Solomon Smith"
    assert guild_channel.id == 42
    assert fake_bot_user.id == 1487666626919792740
    assert fake_redis.ping()


async def _send_mention(channel, user, text, *, bot_user):
    """Build a mention message addressed to Tom and drive on_message."""
    content = f"<@{bot_user.id}> {text}"
    from tests.integration.conftest import make_message
    msg = make_message(user, channel, content, mentions=[bot_user])
    await tom_bot.on_message(msg)
    return msg


@pytest.mark.asyncio
async def test_solomon_mention_replies_with_mockllm_marker(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.1: a mention from the owner produces one reply via MockLLM."""
    msg = await _send_mention(guild_channel, solomon, "tell me about Ran", bot_user=fake_bot_user)
    assert len(msg.reply_log) == 1
    assert "[mock:" in msg.reply_log[0]


@pytest.mark.asyncio
async def test_regular_mention_replies(
    identity_yaml, fake_redis, fake_bot_user, brian, guild_channel
):
    """Spec 4.1.1: a regular member mention also produces a reply."""
    msg = await _send_mention(guild_channel, brian, "what should we watch?", bot_user=fake_bot_user)
    assert len(msg.reply_log) == 1


@pytest.mark.asyncio
async def test_stranger_mention_replies(
    identity_yaml, fake_redis, fake_bot_user, stranger, guild_channel
):
    """Spec 4.1.1: strangers still get a reply (no LLM call refusal)."""
    msg = await _send_mention(guild_channel, stranger, "hi", bot_user=fake_bot_user)
    assert len(msg.reply_log) == 1


@pytest.mark.asyncio
async def test_mention_appends_user_and_assistant_turns(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.1 side effects: two turns persist after a successful reply."""
    await _send_mention(guild_channel, solomon, "hello", bot_user=fake_bot_user)
    turns = memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}")
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[0].content == "hello"
    assert turns[1].role == "assistant"
    # V6: assistant turn must NOT begin with `[ViewerName] ` (old viewer-prefix
    # format that was a regression). MockLLM legitimately returns "[mock:...]"
    # so we test for the specific forbidden pattern rather than any "[".
    assert "[Solomon Smith]" not in turns[1].content[:40]


@pytest.mark.asyncio
async def test_banned_user_gets_canned_refusal_no_llm_call(
    identity_yaml, fake_redis, fake_bot_user, stranger, guild_channel
):
    """Spec 4.1.1 ban path: banned user receives the canned refusal and
    no history is written."""
    guards.ban(fake_redis, str(stranger.id))
    msg = await _send_mention(guild_channel, stranger, "hi", bot_user=fake_bot_user)
    assert msg.reply_log == ["I've been asked not to engage with you. Sorry."]
    assert memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}") == []


@pytest.mark.asyncio
async def test_prompt_too_long_is_refused(
    identity_yaml, fake_redis, fake_bot_user, brian, guild_channel
):
    """Spec 4.1.1 length cap: payloads over MAX_PROMPT_CHARS are refused."""
    long_text = "x" * (guards.MAX_PROMPT_CHARS + 5)
    msg = await _send_mention(guild_channel, brian, long_text, bot_user=fake_bot_user)
    assert len(msg.reply_log) == 1
    assert str(guards.MAX_PROMPT_CHARS) in msg.reply_log[0]


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_budget(
    identity_yaml, fake_redis, fake_bot_user, brian, guild_channel
):
    """Spec 4.1.1 rate limit: non-owner exhausting RATE_LIMIT_MAX_TOKENS
    sees the canned cooldown reply."""
    for _ in range(guards.RATE_LIMIT_MAX_TOKENS):
        await _send_mention(guild_channel, brian, "hi", bot_user=fake_bot_user)
    msg = await _send_mention(guild_channel, brian, "hi again", bot_user=fake_bot_user)
    assert "Easy there" in msg.reply_log[0]


@pytest.mark.asyncio
async def test_owner_bypasses_rate_limit(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 3.1 / 4.1.1: owner tier bypasses rate-limiting."""
    for _ in range(guards.RATE_LIMIT_MAX_TOKENS * 2):
        msg = await _send_mention(guild_channel, solomon, "hi", bot_user=fake_bot_user)
        assert "Easy there" not in msg.reply_log[-1]


@pytest.mark.asyncio
async def test_mention_resolution_substitutes_display_names(
    identity_yaml, fake_redis, fake_bot_user, solomon, wes, guild_channel
):
    """Spec V4 / 4.1.1: <@id> tokens for non-bot users are substituted
    with display names before the LLM call."""
    from tests.integration.conftest import make_message
    captured: dict = {}

    def fake_invoke(messages):
        captured["last_human"] = next(
            m.content for m in reversed(messages) if m.__class__.__name__ == "HumanMessage"
        )
        from agents._mock_llm import _MockResponse
        return _MockResponse(content="ok")

    from unittest.mock import patch

    from agents.tombombadil import agent as tom_agent
    content = f"<@{fake_bot_user.id}> Say hello to <@{wes.id}>"
    msg = make_message(solomon, guild_channel, content, mentions=[fake_bot_user, wes])
    with patch.object(tom_agent._llm, "invoke", side_effect=fake_invoke):
        await tom_bot.on_message(msg)
    assert "Wes Prater" in captured["last_human"]
    assert f"<@{wes.id}>" not in captured["last_human"]
    assert f"<@{fake_bot_user.id}>" not in captured["last_human"]
