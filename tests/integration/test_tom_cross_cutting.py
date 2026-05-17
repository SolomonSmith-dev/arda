"""Spec 5: Tom Bombadil cross-cutting concerns."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.tombombadil import agent as tom_agent
from agents.tombombadil import memory
from agents.tombombadil.identity import resolve as resolve_viewer
from tests.integration.conftest import SAMPLE_LETTERBOXD_FEED as SAMPLE_FEED
from tests.integration.conftest import _send_mention


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
    reason=(
        "D7: stranger onboarding paragraph not specialised. This test can "
        "only pass against a real LLM that follows the onboarding system "
        "prompt; under MockLLM the assertions fail regardless of the code "
        "path. Strict-xfail until D7 ships AND a non-mock LLM (or a "
        "templated onboarding response) drives this branch."
    ),
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


# ---------------------------------------------------------------------------
# Spec 5.4: Privacy invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dm_reply_does_not_mention_other_users_facts(
    identity_yaml, fake_redis, fake_bot_user, finrod_in_memory,
    solomon, brian, dm_channel
):
    """Spec 5.4: Tom's reply to Solomon in a DM never surfaces a fact
    attributed to Brian in the system prompt."""
    bv = resolve_viewer(str(brian.id), str(brian))
    await memory.remember_fact(bv, "brian secretly hates Tarkovsky", source_channel="x")

    captured: dict = {}

    def fake_invoke(messages):
        captured["sys"] = "\n".join(
            m.content for m in messages if m.__class__.__name__ == "SystemMessage"
        )
        from agents._mock_llm import _MockResponse
        return _MockResponse(content="ok")

    with patch.object(tom_agent._llm, "invoke", side_effect=fake_invoke):
        await _send_mention(dm_channel, solomon, "what do you remember about Brian?",
                             bot_user=fake_bot_user)

    assert "brian secretly hates" not in captured["sys"].lower()


def test_history_ltrim_caps_at_two_times_max_turns(identity_yaml, fake_redis, solomon):
    """Spec 5.4 / memory invariants: scope history never exceeds
    2 * HISTORY_MAX_TURNS entries."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    scope = "tom:hist:ch:cap"
    for i in range(memory.HISTORY_MAX_TURNS * 4):
        memory.append_turn(fake_redis, scope, viewer, "user" if i % 2 == 0 else "assistant", f"msg {i}")
    raw = fake_redis.lrange(scope, 0, -1)
    assert len(raw) <= memory.HISTORY_MAX_TURNS * 2


# ---------------------------------------------------------------------------
# Spec 5.5: Operator surface
# ---------------------------------------------------------------------------


def test_operator_ban_blocks_user(identity_yaml, fake_redis, brian):
    """Spec 5.5: SADD tom:bans <id> -> subsequent guard_check returns
    the ban refusal string."""
    from agents.tombombadil import guards
    fake_redis.sadd(guards.BAN_SET_KEY, str(brian.id))
    assert guards.is_banned(fake_redis, str(brian.id)) is True


def test_operator_unban_restores_access(identity_yaml, fake_redis, brian):
    """Spec 5.5: SREM tom:bans <id> reverses the ban."""
    from agents.tombombadil import guards
    guards.ban(fake_redis, str(brian.id))
    guards.unban(fake_redis, str(brian.id))
    assert guards.is_banned(fake_redis, str(brian.id)) is False


def test_operator_watermark_reset_re_pulls_letterboxd(fake_redis):
    """Spec 5.5: DEL tom:letterboxd:last_watched_iso causes the next
    sync to see all entries as 'new' again."""
    from agents.tombombadil import sync_job
    sync_job.run_sync(
        fake_redis, username="x", viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED,
    )
    fake_redis.delete(sync_job.WATERMARK_KEY)
    result = sync_job.run_sync(
        fake_redis, username="x", viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED,
    )
    # Both films re-seen as new because watermark was wiped.
    assert result.new == 2
