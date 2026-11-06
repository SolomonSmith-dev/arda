from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from core.logging import get_logger

log = get_logger("agents.tombombadil.persistent_memory")


def _safe_key(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def _decode_hash(raw: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (raw or {}).items():
        if isinstance(k, bytes):
            k = k.decode("utf-8")
        if isinstance(v, bytes):
            v = v.decode("utf-8")
        out[str(k)] = str(v)
    return out


def save_note(redis, film, watcher, rating, reaction="", themes=""):
    """Save a film note to Redis with atomic deduplication.

    Returns: (success: bool, message: str)
    """
    try:
        if not isinstance(rating, int | float):
            return False, "Rating must be numeric"

        rating = float(rating)
        if not (0 <= rating <= 10):
            return False, "Rating must be 0-10"

        film = film.strip()
        watcher = watcher.strip()

        now = datetime.now(UTC)
        ts = int(now.timestamp())
        iso = now.isoformat()

        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()

        unique_key = f"unique:{_safe_key(film, watcher, week_start)}"
        note_id = str(uuid4())

        if not redis.set(unique_key, note_id, nx=True, ex=7 * 24 * 3600):
            log.warning("duplicate_submission_rejected", film=film, watcher=watcher)
            return False, "Duplicate submission"

        log.debug("dedup_check_passed", note_id=note_id, film=film, watcher=watcher)

        pipe = redis.pipeline(transaction=True)
        try:
            pipe.hset(
                f"note:{note_id}",
                mapping={
                    "film": film,
                    "watcher": watcher,
                    "rating": rating,
                    "timestamp": iso,
                    "reaction": reaction,
                    "themes": themes,
                },
            )
            pipe.zadd(f"film:{film}:notes", {note_id: ts})
            pipe.zadd(f"watcher:{watcher}:notes", {note_id: ts})
            pipe.zadd("notes:all", {note_id: ts})

            pipe.hincrby(f"stats:film:{film}", "count", 1)
            pipe.hincrbyfloat(f"stats:film:{film}", "sum", rating)

            pipe.hincrby(f"stats:watcher:{watcher}", "count", 1)
            pipe.hincrbyfloat(f"stats:watcher:{watcher}", "sum", rating)

            pipe.sadd("films", film)
            pipe.sadd("watchers", watcher)

            pipe.execute()

            log.info(
                "note_saved",
                note_id=note_id,
                film=film,
                watcher=watcher,
                rating=rating,
            )
            return True, "Saved"

        except Exception as e:
            redis.delete(unique_key)
            log.error("pipeline_failed", exception=str(e), film=film, watcher=watcher)
            return False, f"Pipeline failed: {e}"

    except Exception as e:
        log.error("save_note_exception", exception=str(e), film=film, watcher=watcher)
        return False, str(e)


def delete_note(redis, film: str, watcher: str) -> tuple[bool, str]:
    """Delete the most recent note for ``(film, watcher)``.

    Cascades through ``note:*``, the film/watcher/all ZSETs, and stats
    counters. Also clears the current-week dedup key so ``/rate`` can
    re-log the same film in the same week (D9 / ``/unrate``).
    """
    film = (film or "").strip()
    watcher = (watcher or "").strip()
    if not film:
        return False, "Film is required."
    if not watcher:
        return False, "Watcher is required."

    note_ids = redis.zrevrange(f"film:{film}:notes", 0, -1) or []
    # Also try case-insensitive scan of notes:all if the exact film key
    # is empty (titles may differ in casing from what the user types).
    if not note_ids:
        note_ids = redis.zrevrange("notes:all", 0, -1) or []

    target_id: str | None = None
    stored_film = film
    rating = 0.0
    for raw_id in note_ids:
        note_id = raw_id.decode("utf-8") if isinstance(raw_id, bytes) else str(raw_id)
        data = _decode_hash(redis.hgetall(f"note:{note_id}") or {})
        if not data:
            continue
        if data.get("watcher", "") != watcher:
            continue
        if data.get("film", "").lower() != film.lower():
            continue
        target_id = note_id
        stored_film = data.get("film", film)
        try:
            rating = float(data.get("rating", "0"))
        except ValueError:
            rating = 0.0
        break

    if target_id is None:
        return False, f"No note found for **{film}**."

    pipe = redis.pipeline(transaction=True)
    pipe.delete(f"note:{target_id}")
    pipe.zrem(f"film:{stored_film}:notes", target_id)
    pipe.zrem(f"watcher:{watcher}:notes", target_id)
    pipe.zrem("notes:all", target_id)
    pipe.hincrby(f"stats:film:{stored_film}", "count", -1)
    pipe.hincrbyfloat(f"stats:film:{stored_film}", "sum", -rating)
    pipe.hincrby(f"stats:watcher:{watcher}", "count", -1)
    pipe.hincrbyfloat(f"stats:watcher:{watcher}", "sum", -rating)
    pipe.execute()

    # Drop empty index sets / zeroed stats so subsequent queries stay clean.
    film_count = int(redis.hget(f"stats:film:{stored_film}", "count") or 0)
    if film_count <= 0:
        redis.delete(f"stats:film:{stored_film}")
        if redis.zcard(f"film:{stored_film}:notes") == 0:
            redis.delete(f"film:{stored_film}:notes")
            redis.srem("films", stored_film)

    watcher_count = int(redis.hget(f"stats:watcher:{watcher}", "count") or 0)
    if watcher_count <= 0:
        redis.delete(f"stats:watcher:{watcher}")
        if redis.zcard(f"watcher:{watcher}:notes") == 0:
            redis.delete(f"watcher:{watcher}:notes")
            redis.srem("watchers", watcher)

    now = datetime.now(UTC)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    redis.delete(f"unique:{_safe_key(stored_film, watcher, week_start)}")

    log.info(
        "note_deleted",
        note_id=target_id,
        film=stored_film,
        watcher=watcher,
    )
    return True, "Deleted"
