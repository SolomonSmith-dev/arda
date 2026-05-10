"""Pending note-draft store keyed by Discord message id.

When Tom sees a loose-form rating ("I just watched Inception, 8/10"),
his fact extractor builds a :class:`NoteDraft`. Rather than auto-save
(which was an unconfirmed write in PR 1), Tom posts a follow-up message
asking the user to react ✅, and stashes the draft here under the
follow-up's ``message_id``. On the matching :class:`on_reaction_add` we
commit via ``persistent_memory.save_note``.

Two key namespaces:

- ``tom:drafts:scope:{scope_key}`` LIST  -- handoff from
  ``agent.get_response`` to ``bot.on_message``. The agent doesn't know
  the follow-up message id (the reply hasn't been sent yet), so it
  pushes drafts here. The bot pops, posts, and re-keys.
- ``tom:draft:{message_id}`` HASH  -- one pending draft awaiting
  confirmation. Expires after 24h so stale drafts don't accumulate.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING

from core.logging import get_logger

if TYPE_CHECKING:
    from agents.tombombadil.fact_extractor import NoteDraft


log = get_logger("agents.tombombadil.draft_store")

DRAFT_TTL_SECONDS = 24 * 3600


def _scope_key(scope: str) -> str:
    return f"tom:drafts:scope:{scope}"


def _draft_key(message_id: str | int) -> str:
    return f"tom:draft:{message_id}"


def push_pending(redis, scope: str, draft: NoteDraft) -> None:
    """Stash a draft from ``agent.get_response`` so the bot can pick it
    up after the reply lands.
    """
    payload = json.dumps(asdict(draft))
    redis.rpush(_scope_key(scope), payload)
    redis.expire(_scope_key(scope), DRAFT_TTL_SECONDS)


def pop_pending(redis, scope: str) -> NoteDraft | None:
    """Return the oldest pending draft for ``scope`` (or ``None``)."""
    from agents.tombombadil.fact_extractor import NoteDraft

    raw = redis.lpop(_scope_key(scope))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        obj = json.loads(raw)
        return NoteDraft(**obj)
    except (json.JSONDecodeError, TypeError) as exc:
        log.warning("draft_decode_failed", scope=scope, exc=str(exc))
        return None


def bind_to_message(
    redis,
    message_id: str | int,
    draft: NoteDraft,
    requester_discord_id: str,
    scope: str,
) -> None:
    """Persist a pending draft under the follow-up message id."""
    redis.hset(
        _draft_key(message_id),
        mapping={
            "film": draft.film,
            "rating": str(draft.rating),
            "viewer": draft.viewer,
            "requester_discord_id": str(requester_discord_id),
            "scope": scope,
        },
    )
    redis.expire(_draft_key(message_id), DRAFT_TTL_SECONDS)


def get_draft(redis, message_id: str | int) -> dict[str, str] | None:
    """Return the pending draft bound to ``message_id``, or ``None``."""
    raw = redis.hgetall(_draft_key(message_id)) or {}
    if not raw:
        return None
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, bytes):
            k = k.decode("utf-8")
        if isinstance(v, bytes):
            v = v.decode("utf-8")
        out[k] = v
    return out


def delete_draft(redis, message_id: str | int) -> None:
    redis.delete(_draft_key(message_id))
