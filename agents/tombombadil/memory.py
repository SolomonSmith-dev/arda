"""Two-tier memory for Tom Bombadil.

Short-term: per-channel (or per-DM) Redis sliding window of recent
conversational turns. Cheap, ephemeral, scoped to where the chat happens
so DM context doesn't leak into the club channel and vice versa.

Long-term: significant facts (preferences, declared ratings, "remember
that..." asks) embedded into Finrod's vector store. Retrieved by the
current message at query time so the bot can recall what you told it
last week. Filtered server-/client-side by ``metadata.viewer`` so
Solomon's facts never show up in Brian's context.

The store is in-memory until Milvus standalone lands (PR 6), so the
long-term tier is best-effort and resets on container restart.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING
from uuid import uuid4

from core.logging import get_logger
from core.models import AgentTask

if TYPE_CHECKING:
    from agents.tombombadil.identity import Viewer

log = get_logger("agents.tombombadil.memory")

HISTORY_MAX_TURNS = 20  # 20 user/assistant pairs = 40 list entries
HISTORY_TTL_SECONDS = 7 * 24 * 3600
RECALL_SCORE_FLOOR = 0.35
PREF_KEYS = frozenset({"suppress_films", "preferred_tone", "do_not_log"})


@dataclass(frozen=True)
class Turn:
    role: str
    viewer: str
    discord_id: str
    content: str
    ts: int


# ----- scope keys ---------------------------------------------------

def history_scope_key(event) -> str:
    """Return the Redis key namespace for ``event``'s history.

    ``event`` is duck-typed: either a ``discord.Message`` (has
    ``author``) or a ``discord.Interaction`` (has ``user``). DMs use
    ``tom:hist:dm:{user_id}`` so personal context stays isolated from
    guild-channel chatter. Everything else uses
    ``tom:hist:ch:{channel_id}`` (Discord threads share the parent
    channel id; per-thread scoping can land in a later PR).
    """
    channel = getattr(event, "channel", None)
    is_dm = getattr(channel, "type", None) and str(channel.type).endswith("private")
    actor = getattr(event, "author", None) or getattr(event, "user", None)
    if is_dm and actor is not None:
        return f"tom:hist:dm:{actor.id}"
    channel_id = getattr(channel, "id", None) or getattr(event, "channel_id", "unknown")
    return f"tom:hist:ch:{channel_id}"


# ----- short-term: per-channel turns --------------------------------

def append_turn(redis, scope_key: str, viewer: Viewer, role: str, content: str) -> None:
    if role not in ("user", "assistant"):
        raise ValueError(f"role must be 'user' or 'assistant', got {role!r}")
    if not content.strip():
        return
    payload = json.dumps(
        {
            "role": role,
            "viewer": viewer.canonical_name or viewer.discord_name,
            "discord_id": viewer.discord_id,
            "content": content,
            "ts": int(time.time()),
        }
    )
    pipe = redis.pipeline()
    pipe.rpush(scope_key, payload)
    pipe.ltrim(scope_key, -HISTORY_MAX_TURNS * 2, -1)
    pipe.expire(scope_key, HISTORY_TTL_SECONDS)
    pipe.execute()


def recent_turns(redis, scope_key: str, limit: int = HISTORY_MAX_TURNS) -> list[Turn]:
    raw = redis.lrange(scope_key, -limit * 2, -1) or []
    turns: list[Turn] = []
    for entry in raw:
        if isinstance(entry, bytes):
            entry = entry.decode("utf-8")
        try:
            obj = json.loads(entry)
        except json.JSONDecodeError:
            log.warning("history_entry_corrupt", scope_key=scope_key)
            continue
        turns.append(
            Turn(
                role=obj.get("role", "user"),
                viewer=obj.get("viewer", ""),
                discord_id=str(obj.get("discord_id", "")),
                content=obj.get("content", ""),
                ts=int(obj.get("ts", 0)),
            )
        )
    return turns


def clear_history(redis, scope_key: str) -> None:
    redis.delete(scope_key)


# ----- per-user preferences -----------------------------------------

def _pref_key(discord_id: str) -> str:
    return f"tom:pref:{discord_id}"


def get_prefs(redis, discord_id: str) -> dict[str, str]:
    raw = redis.hgetall(_pref_key(discord_id)) or {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, bytes):
            k = k.decode("utf-8")
        if isinstance(v, bytes):
            v = v.decode("utf-8")
        out[k] = v
    return out


def set_pref(redis, discord_id: str, key: str, value: str) -> None:
    if key not in PREF_KEYS:
        raise ValueError(f"unknown pref {key!r}; allowed: {sorted(PREF_KEYS)}")
    redis.hset(_pref_key(discord_id), key, value)


def clear_prefs(redis, discord_id: str) -> None:
    redis.delete(_pref_key(discord_id))


# ----- long-term: Finrod-backed recall -------------------------------

@lru_cache(maxsize=1)
def _get_finrod():
    # Lazy import + lazy construction so unit tests that don't touch
    # long-term memory don't pay the embedder/store init cost.
    from agents.finrod.agent import Finrod

    return Finrod()


def _set_finrod_for_tests(finrod) -> None:
    """Test seam: inject a Finrod instance built with InMemoryStore +
    MockEmbedder so tests don't go through ``get_store()``.
    """
    _get_finrod.cache_clear()
    _get_finrod.__wrapped__ = lambda: finrod  # type: ignore[attr-defined]


async def remember_fact(
    viewer: Viewer,
    fact: str,
    source_channel: str,
) -> bool:
    """Embed ``fact`` into Finrod so it can be recalled later.

    Returns ``True`` on success. ``viewer.canonical_name`` is required
    (strangers don't get long-term memory yet — we don't know who to
    file the fact under).
    """
    if not viewer.canonical_name:
        return False
    if not fact.strip():
        return False

    text = f"[{viewer.canonical_name}] {fact.strip()}"
    metadata = {
        "viewer": viewer.canonical_name,
        "discord_id": viewer.discord_id,
        "kind": "tom_fact",
        "ts": int(time.time()),
        "source_channel": source_channel,
    }
    task = AgentTask(
        agent="finrod",
        type="ingest",
        payload={
            "action": "ingest",
            "doc_id": f"tom:viewer:{viewer.canonical_name}:{uuid4()}",
            "text": text,
            "metadata": metadata,
        },
    )
    finrod = _get_finrod()
    result = await finrod.run(task)
    ok = result.status.value == "completed"
    if not ok:
        log.warning("remember_fact_failed", viewer=viewer.canonical_name, error=result.error)
    return ok


async def forget_facts(viewer: Viewer) -> int:
    """Drop every long-term fact attributed to ``viewer``. Returns the
    number of stored chunks removed. Strangers (no canonical name) have
    no facts to forget.
    """
    if not viewer.canonical_name:
        return 0
    finrod = _get_finrod()
    return await finrod.forget({"kind": "tom_fact", "viewer": viewer.canonical_name})


async def recall_facts(viewer: Viewer, query: str, top_k: int = 5) -> list[str]:
    """Return up to ``top_k`` previously-remembered facts for ``viewer``
    relevant to ``query``. Strangers get an empty list.
    """
    if not viewer.canonical_name or not query.strip():
        return []

    task = AgentTask(
        agent="finrod",
        type="query",
        payload={"action": "query", "message": query, "top_k": top_k * 3},
    )
    finrod = _get_finrod()
    result = await finrod.run(task)
    if result.status.value != "completed":
        log.warning("recall_facts_failed", viewer=viewer.canonical_name, error=result.error)
        return []

    sources = (result.result or {}).get("sources", []) or []
    filtered: list[tuple[float, str]] = []
    for src in sources:
        meta = src.get("metadata") or {}
        if meta.get("kind") != "tom_fact":
            continue
        if meta.get("viewer") != viewer.canonical_name:
            continue
        score = float(src.get("score") or 0.0)
        if score < RECALL_SCORE_FLOOR:
            continue
        text = src.get("text") or ""
        # Strip the canonical-name prefix we wrote at ingest time.
        prefix = f"[{viewer.canonical_name}] "
        if text.startswith(prefix):
            text = text[len(prefix):]
        filtered.append((score, text))

    filtered.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in filtered[:top_k]]
