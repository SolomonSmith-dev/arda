"""Abuse guards and rate limits for Tom Bombadil.

Three layered defenses, all cheap and all stateless except for Redis:

1. **Max prompt length** (``MAX_PROMPT_CHARS``). Discord messages are
   capped at 2000 chars but `@mentions` plus pasted blocks can still
   blow up the LLM token bill. Reject anything over the limit before
   the LLM call.
2. **Per-user token bucket rate limit** (``RATE_LIMIT_MAX_TOKENS`` per
   ``RATE_LIMIT_REFILL_SECONDS`` seconds). Each
   ``check_and_consume(redis, discord_id)`` deducts one token and
   returns whether the request should proceed. Solomon is exempt.
3. **Static ban list**. ``tom:bans`` Redis SET keyed by Discord id.
   Banned users get a polite refusal and zero LLM cost.

All limits return a short string for the bot to reply with when they
trip; ``None`` means "proceed".
"""

from __future__ import annotations

import time

from core.logging import get_logger

log = get_logger("agents.tombombadil.guards")

MAX_PROMPT_CHARS = 4000
RATE_LIMIT_MAX_TOKENS = 12
RATE_LIMIT_REFILL_SECONDS = 60
BAN_SET_KEY = "tom:bans"


def _bucket_key(discord_id: str) -> str:
    return f"tom:rl:{discord_id}"


def check_prompt_length(text: str) -> str | None:
    """Return a refusal string if ``text`` exceeds the prompt cap."""
    if len(text) > MAX_PROMPT_CHARS:
        return (
            f"That message is {len(text)} characters; I cap inputs at "
            f"{MAX_PROMPT_CHARS}. Trim it down and try again."
        )
    return None


def is_banned(redis, discord_id: str) -> bool:
    try:
        return bool(redis.sismember(BAN_SET_KEY, str(discord_id)))
    except Exception as exc:
        log.warning("ban_check_failed", discord_id=discord_id, exc=str(exc))
        return False


def ban(redis, discord_id: str) -> None:
    redis.sadd(BAN_SET_KEY, str(discord_id))


def unban(redis, discord_id: str) -> None:
    redis.srem(BAN_SET_KEY, str(discord_id))


def check_and_consume(
    redis,
    discord_id: str,
    *,
    is_owner: bool = False,
    max_tokens: int = RATE_LIMIT_MAX_TOKENS,
    refill_seconds: int = RATE_LIMIT_REFILL_SECONDS,
) -> str | None:
    """Token bucket rate limit. Returns ``None`` on allow, a refusal
    string on deny. Owner accounts bypass the limit (so Solomon never
    gets rate-limited by his own bot).

    The bucket is stored as a Redis HASH with two fields:

    - ``tokens``: current token count (float, refilled lazily)
    - ``ts``: last refill timestamp in float seconds

    Lazy refill: on each check we compute ``elapsed * max_tokens /
    refill_seconds`` and top up the bucket, clamped to ``max_tokens``.
    """
    if is_owner:
        return None

    key = _bucket_key(discord_id)
    now = time.time()
    try:
        raw = redis.hgetall(key) or {}
    except Exception as exc:
        log.warning("rate_limit_read_failed", discord_id=discord_id, exc=str(exc))
        return None  # fail-open: prefer the bot to keep working

    def _get(field: str, default: float) -> float:
        v = raw.get(field) or raw.get(field.encode("utf-8") if isinstance(field, str) else field)
        if isinstance(v, bytes):
            v = v.decode("utf-8")
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    tokens = _get("tokens", float(max_tokens))
    ts = _get("ts", now)

    refill_rate = max_tokens / refill_seconds
    elapsed = max(0.0, now - ts)
    tokens = min(float(max_tokens), tokens + elapsed * refill_rate)

    if tokens < 1.0:
        wait = (1.0 - tokens) / refill_rate
        try:
            redis.hset(key, mapping={"tokens": tokens, "ts": now})
            redis.expire(key, refill_seconds * 4)
        except Exception:
            pass
        return (
            "Easy there -- you're hitting Tom faster than the cooldown "
            f"allows. Try again in {wait:.0f}s."
        )

    tokens -= 1.0
    try:
        redis.hset(key, mapping={"tokens": tokens, "ts": now})
        redis.expire(key, refill_seconds * 4)
    except Exception as exc:
        log.warning("rate_limit_write_failed", discord_id=discord_id, exc=str(exc))
    return None
