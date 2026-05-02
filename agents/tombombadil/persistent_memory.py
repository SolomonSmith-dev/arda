from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from uuid import uuid4

from core.logging import get_logger

log = get_logger("agents.tombombadil.persistent_memory")


def _safe_key(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


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

        now = datetime.utcnow()
        ts = int(now.timestamp())
        iso = now.isoformat() + "Z"

        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat() + "Z"

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
