"""Letterboxd auto-sync.

Polls a Letterboxd diary RSS feed, diffs against state in Redis, and
saves any new entries via ``persistent_memory.save_note``. Optionally
announces each new entry to a Discord channel via
``agents.tombombadil.delivery.publish``.

Wired to fire from Galadriel's worker on ``payload.text=="letterboxd_sync"``
system events (see ``ensure_letterboxd_sync_cron``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from agents.galadriel.models import Job, JobDelivery, JobPayload, JobSchedule
from agents.galadriel.scheduler import Schedule, next_run_ms
from agents.galadriel.store import read_job, save_job
from agents.tombombadil import delivery, metrics
from agents.tombombadil.letterboxd_rss import DiaryEntry, parse_feed_text
from agents.tombombadil.persistent_memory import save_note
from core.logging import get_logger
from core.redis_client import get_redis_sync

log = get_logger("agents.tombombadil.sync_job")

WATERMARK_KEY = "tom:letterboxd:last_watched_iso"
LETTERBOXD_SYNC_JOB_ID = "tom_letterboxd_sync"
FEED_URL_TEMPLATE = "https://letterboxd.com/{username}/rss/"
HTTP_TIMEOUT_SECONDS = 15


@dataclass
class SyncResult:
    fetched: int
    new: int
    skipped: int
    saved: int
    errors: list[str]


def _read_watermark(redis) -> str | None:
    raw = redis.get(WATERMARK_KEY)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return raw or None


def _write_watermark(redis, iso: str) -> None:
    redis.set(WATERMARK_KEY, iso)


def _entry_key_iso(entry: DiaryEntry) -> str:
    return entry.watched_iso or ""


def _format_announcement(watcher: str, entry: DiaryEntry) -> str:
    rating_part = f" ({entry.rating:g}/10)" if entry.rating is not None else ""
    year_part = f" ({entry.year})" if entry.year else ""
    return f"{watcher} logged **{entry.title}**{year_part}{rating_part} on Letterboxd."


def run_sync(
    redis=None,
    *,
    username: str | None = None,
    viewer_name: str | None = None,
    feed_text: str | None = None,
    announce_channel_id: str | None = None,
    http_client: httpx.Client | None = None,
) -> SyncResult:
    """Pull the RSS feed and save any entries newer than the watermark.

    ``feed_text`` short-circuits the HTTP fetch and is the seam tests
    use. In production we fetch from ``FEED_URL_TEMPLATE`` using
    ``LETTERBOXD_USERNAME``.

    ``viewer_name`` is the canonical name to attribute notes to (must
    match the seed FILM_DATABASE / Letterboxd merge identity). Defaults
    to ``LETTERBOXD_VIEWER_NAME`` env var, or the username.

    ``announce_channel_id``: when set, each newly-logged entry is
    enqueued for the Discord delivery subscriber to post.
    """
    redis = redis or get_redis_sync()
    username = username or os.environ.get("LETTERBOXD_USERNAME") or ""
    viewer_name = viewer_name or os.environ.get("LETTERBOXD_VIEWER_NAME") or username
    announce_channel_id = announce_channel_id or os.environ.get("TOM_LETTERBOXD_ANNOUNCE_CHANNEL_ID") or None

    errors: list[str] = []

    if feed_text is None:
        if not username:
            return SyncResult(fetched=0, new=0, skipped=0, saved=0, errors=["LETTERBOXD_USERNAME not set"])
        url = FEED_URL_TEMPLATE.format(username=username)
        try:
            client = http_client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True)
            try:
                resp = client.get(url, headers={"User-Agent": "arda-tom-bombadil/0.1"})
                resp.raise_for_status()
                feed_text = resp.text
            finally:
                if http_client is None:
                    client.close()
        except Exception as exc:
            log.error("letterboxd_fetch_failed", url=url, exc=str(exc))
            return SyncResult(fetched=0, new=0, skipped=0, saved=0, errors=[f"fetch_failed: {exc}"])

    entries = parse_feed_text(feed_text)
    watermark = _read_watermark(redis)
    new_entries: list[DiaryEntry] = []
    skipped = 0
    for e in entries:
        key = _entry_key_iso(e)
        if not key:
            skipped += 1
            continue
        if watermark is not None and key <= watermark:
            skipped += 1
            continue
        new_entries.append(e)

    new_entries.sort(key=lambda e: _entry_key_iso(e))

    saved = 0
    for e in new_entries:
        if e.rating is None:
            log.info("letterboxd_skip_unrated", title=e.title, year=e.year)
            continue
        ok, msg = save_note(
            redis,
            film=e.title,
            watcher=viewer_name or "Letterboxd User",
            rating=e.rating,
            reaction="",
            themes="",
        )
        if ok:
            saved += 1
            if announce_channel_id:
                try:
                    delivery.publish(
                        announce_channel_id,
                        _format_announcement(viewer_name or username, e),
                        redis=redis,
                    )
                except Exception as exc:
                    errors.append(f"announce_failed:{e.title}:{exc}")
                    log.warning("letterboxd_announce_failed", title=e.title, exc=str(exc))
        else:
            errors.append(f"save_failed:{e.title}:{msg}")

    if new_entries:
        latest_iso = max(_entry_key_iso(e) for e in new_entries)
        _write_watermark(redis, latest_iso)

    result = SyncResult(
        fetched=len(entries),
        new=len(new_entries),
        skipped=skipped,
        saved=saved,
        errors=errors,
    )
    if result.saved:
        metrics.LETTERBOXD_SYNC.labels(kind="saved_films").inc(result.saved)
    if result.errors:
        metrics.LETTERBOXD_SYNC.labels(kind="errors").inc(len(result.errors))

    log.info(
        "letterboxd_sync_complete",
        username=username,
        viewer=viewer_name,
        fetched=result.fetched,
        new=result.new,
        skipped=result.skipped,
        saved=result.saved,
        errors=len(result.errors),
    )
    return result


def ensure_letterboxd_sync_cron(
    redis,
    *,
    cron_expr: str = "0 6 * * *",
    tz: str = "America/Los_Angeles",
) -> Job:
    """Idempotently install the daily 6am sync job.

    The job uses ``payload.kind="systemEvent"`` with ``text="letterboxd_sync"``
    so Galadriel's worker can dispatch it locally (no HTTP round-trip).

    Genuinely idempotent: if the job already exists it is returned untouched.
    This runs from the API lifespan (``api/main.py``), so it fires on every
    container restart. Rebuilding and ``save_job``-ing a fresh Job there is a
    full overwrite -- an operator who disabled this job or edited its cron
    expression to stop a misbehaving sync would find it silently re-enabled
    on the default schedule at the next restart.
    """
    existing = read_job(redis, LETTERBOXD_SYNC_JOB_ID)
    if existing is not None:
        log.info(
            "letterboxd_sync_cron_already_present",
            job_id=existing.id,
            enabled=existing.enabled,
            cron=existing.schedule.expr,
        )
        return existing

    sched = Schedule(kind="cron", expr=cron_expr, tz=tz)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    job = Job(
        id=LETTERBOXD_SYNC_JOB_ID,
        name="Letterboxd auto-sync",
        schedule=JobSchedule(kind="cron", expr=cron_expr, tz=tz),
        payload=JobPayload(
            kind="systemEvent",
            text="letterboxd_sync",
            timeout_seconds=60,
        ),
        delivery=JobDelivery(mode="none"),
        delete_after_run=False,
        enabled=True,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        next_run_at_ms=next_run_ms(sched),
    )
    save_job(redis, job)
    log.info("letterboxd_sync_cron_ensured", job_id=job.id, cron=cron_expr, tz=tz)
    return job


def cli_main() -> None:
    """Manual entrypoint for one-off syncs from a shell."""
    result = run_sync()
    print(
        f"fetched={result.fetched} new={result.new} skipped={result.skipped} "
        f"saved={result.saved} errors={len(result.errors)}"
    )
    for err in result.errors:
        print(f"  ! {err}")


if __name__ == "__main__":
    cli_main()
