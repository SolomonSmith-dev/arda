from __future__ import annotations

import time

import fakeredis
import pytest

from agents.tombombadil import guards


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


# ----- prompt length -------------------------------------------------

def test_check_prompt_length_allows_normal():
    assert guards.check_prompt_length("hi") is None
    assert guards.check_prompt_length("x" * guards.MAX_PROMPT_CHARS) is None


def test_check_prompt_length_rejects_too_long():
    msg = guards.check_prompt_length("x" * (guards.MAX_PROMPT_CHARS + 1))
    assert msg is not None
    assert str(guards.MAX_PROMPT_CHARS) in msg


# ----- ban list ------------------------------------------------------

def test_ban_unban_roundtrip(r):
    assert guards.is_banned(r, "111") is False
    guards.ban(r, "111")
    assert guards.is_banned(r, "111") is True
    guards.unban(r, "111")
    assert guards.is_banned(r, "111") is False


# ----- rate limit ----------------------------------------------------

def test_rate_limit_allows_within_budget(r):
    for _ in range(guards.RATE_LIMIT_MAX_TOKENS):
        assert guards.check_and_consume(r, "222") is None


def test_rate_limit_blocks_after_budget(r):
    for _ in range(guards.RATE_LIMIT_MAX_TOKENS):
        guards.check_and_consume(r, "333")
    refusal = guards.check_and_consume(r, "333")
    assert refusal is not None
    assert "Easy there" in refusal or "cooldown" in refusal


def test_rate_limit_owner_bypasses(r):
    for _ in range(guards.RATE_LIMIT_MAX_TOKENS * 3):
        assert guards.check_and_consume(r, "111", is_owner=True) is None


def test_rate_limit_refills_over_time(r):
    # Burn the budget at t=0...
    for _ in range(guards.RATE_LIMIT_MAX_TOKENS):
        guards.check_and_consume(r, "444")
    assert guards.check_and_consume(r, "444") is not None

    # ...then walk the bucket's `ts` back so refill catches up.
    refill_seconds = guards.RATE_LIMIT_REFILL_SECONDS
    r.hset("tom:rl:444", "ts", str(time.time() - refill_seconds))
    assert guards.check_and_consume(r, "444") is None


def test_rate_limit_fails_open_when_redis_errors():
    class BrokenRedis:
        def hgetall(self, _key):
            raise RuntimeError("boom")

        def hset(self, *_args, **_kwargs):
            return 0

        def expire(self, *_args, **_kwargs):
            return 0

    # Even though hgetall raises, the guard must not reject the user.
    assert guards.check_and_consume(BrokenRedis(), "555") is None
