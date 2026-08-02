"""Spec 4.1: Tom Bombadil conversational flows."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents._anthropic_mock import MockMessage, TextBlock
from agents.tombombadil import agent as tom_agent
from agents.tombombadil import bot as tom_bot
from agents.tombombadil import guards, memory
from agents.tombombadil.bot import CONFIRM_EMOJI, REJECT_EMOJI
from tests.integration._doubles import FakeMessage, FakeReaction, FakeUser
from tests.integration.conftest import _send_mention, make_message


def _sole_draft_id(redis) -> int:
    """Return the message_id of the single pending draft. Fails fast if
    the draft store has 0 or >1 entries."""
    keys = redis.keys("tom:draft:*")
    assert len(keys) == 1, f"expected 1 draft, got {len(keys)}"
    return int(keys[0].split(":")[-1])


def test_harness_imports_cleanly(identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel):
    """Spec smoke: shared fixtures wire up without exceptions."""
    assert solomon.name == "Solomon Smith"
    assert guild_channel.id == 42
    assert fake_bot_user.id == 1487666626919792740
    assert fake_redis.ping()


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
        assert "Easy there" not in msg.reply_log[0]


@pytest.mark.asyncio
async def test_mention_resolution_substitutes_display_names(
    identity_yaml, fake_redis, fake_bot_user, solomon, wes, guild_channel
):
    """Spec V4 / 4.1.1: <@id> tokens for non-bot users are substituted
    with display names before the LLM call."""
    captured: dict = {}

    async def fake_create(*, messages, **_kwargs):
        captured["last_user"] = next(
            m["content"] for m in reversed(messages) if m["role"] == "user"
        )
        return MockMessage(content=[TextBlock(text="ok")], stop_reason="end_turn")

    content = f"<@{fake_bot_user.id}> Say hello to <@{wes.id}>"
    msg = make_message(solomon, guild_channel, content, mentions=[fake_bot_user, wes])
    with patch.object(tom_agent._llm.messages, "create", side_effect=fake_create):
        await tom_bot.on_message(msg)
    assert "Wes Prater" in captured["last_user"]
    assert f"<@{wes.id}>" not in captured["last_user"]
    assert f"<@{fake_bot_user.id}>" not in captured["last_user"]


# ---------------------------------------------------------------------------
# Spec 4.1.2 — Note capture: draft offer + react-to-confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rating_phrase_offers_draft(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.2: a natural-language rating produces a follow-up draft
    message after the primary reply."""
    msg = await _send_mention(
        guild_channel, solomon, "I rated Stalker 10/10", bot_user=fake_bot_user
    )
    # Two replies: the primary conversational one + the draft prompt.
    assert len(msg.reply_log) == 2
    assert "React" in msg.reply_log[1] and "Stalker" in msg.reply_log[1]


@pytest.mark.asyncio
async def test_pronoun_rating_does_not_offer_draft(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.2 / pronoun blacklist: 'I rated it 5/10' is dropped."""
    msg = await _send_mention(
        guild_channel, solomon, "I rated it 5/10", bot_user=fake_bot_user
    )
    assert len(msg.reply_log) == 1  # only the primary reply, no draft


@pytest.mark.asyncio
async def test_stranger_rating_does_not_offer_draft(
    identity_yaml, fake_redis, fake_bot_user, stranger, guild_channel
):
    """Spec 4.1.2: strangers can't produce drafts (no canonical_name)."""
    msg = await _send_mention(
        guild_channel, stranger, "I rated Inception 9/10", bot_user=fake_bot_user
    )
    assert len(msg.reply_log) == 1


async def _react(message_id, channel, user, emoji):
    """Drive on_reaction_add with a freshly-built FakeReaction."""
    tom = FakeUser(id=0, name="TomBombadil")
    target = FakeMessage(id=message_id, content="React ...", author=tom, channel=channel, mentions=[])
    await tom_bot.on_reaction_add(FakeReaction(emoji=emoji, message=target), user)
    return target


@pytest.mark.asyncio
async def test_check_reaction_commits_draft(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.2: ✅ reaction by the original drafter triggers save_note."""
    await _send_mention(
        guild_channel, solomon, "I rated Stalker 10/10", bot_user=fake_bot_user
    )
    # The bound draft was created against the second reply's id.
    bound_id = _sole_draft_id(fake_redis)

    target = await _react(bound_id, guild_channel, solomon, CONFIRM_EMOJI)
    assert any("logged" in r for r in target.reply_log)
    assert fake_redis.sismember("films", "Stalker")
    assert fake_redis.sismember("watchers", "Solomon Smith")
    # Draft is cleared after commit.
    assert fake_redis.exists(f"tom:draft:{bound_id}") == 0


@pytest.mark.asyncio
async def test_x_reaction_skips_draft(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.2: ❌ reaction discards the draft, no save_note."""
    await _send_mention(
        guild_channel, solomon, "I rated Stalker 10/10", bot_user=fake_bot_user
    )
    bound_id = _sole_draft_id(fake_redis)
    target = await _react(bound_id, guild_channel, solomon, REJECT_EMOJI)
    assert any("Skipped" in r for r in target.reply_log)
    assert not fake_redis.sismember("films", "Stalker")


@pytest.mark.asyncio
async def test_wrong_user_reaction_ignored(
    identity_yaml, fake_redis, fake_bot_user, solomon, brian, guild_channel
):
    """Spec 4.1.2: only the requester can confirm. Brian reacting to
    Solomon's draft is silently ignored."""
    await _send_mention(
        guild_channel, solomon, "I rated Stalker 10/10", bot_user=fake_bot_user
    )
    bound_id = _sole_draft_id(fake_redis)
    target = await _react(bound_id, guild_channel, brian, CONFIRM_EMOJI)
    # No save, no reply.
    assert target.reply_log == []
    assert not fake_redis.sismember("films", "Stalker")
    # Draft still pending.
    assert fake_redis.exists(f"tom:draft:{bound_id}") == 1


@pytest.mark.asyncio
async def test_concurrent_drafts_bind_to_original_drafters(
    identity_yaml, fake_redis, fake_bot_user, solomon, brian, guild_channel
):
    """Spec 4.1.2 / 5.2: when Solomon and Brian draft ratings in the
    same channel concurrently, each draft binds under its own
    drafter's discord_id even if the FIFO scope list is popped in a
    crossed order (asyncio.gather interleaves the two on_message
    coroutines).
    """
    import asyncio

    await asyncio.gather(
        _send_mention(
            guild_channel, solomon, "I rated Stalker 10/10", bot_user=fake_bot_user
        ),
        _send_mention(
            guild_channel, brian, "I rated Ran 9/10", bot_user=fake_bot_user
        ),
    )
    drafts = sorted(int(k.split(":")[-1]) for k in fake_redis.keys("tom:draft:*"))
    # Two drafts in flight.
    assert len(drafts) == 2
    # Each draft's requester_discord_id matches the film's originator,
    # not whichever reply finished first.
    d0 = fake_redis.hgetall(f"tom:draft:{drafts[0]}")
    d1 = fake_redis.hgetall(f"tom:draft:{drafts[1]}")
    by_film = {d["film"]: d for d in (d0, d1)}
    assert by_film["Stalker"]["requester_discord_id"] == str(solomon.id)
    assert by_film["Ran"]["requester_discord_id"] == str(brian.id)
    assert by_film["Stalker"]["viewer"] == "Solomon Smith"
    assert by_film["Ran"]["viewer"] == "Brian"
