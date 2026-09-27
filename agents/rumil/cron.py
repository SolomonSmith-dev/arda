"""Idempotent seed for Rúmil's daily GitHub audit job.

Mirrors ``ensure_letterboxd_sync_cron``: called from the API lifespan, and
an existing job is returned untouched so an operator's edits (disabled, new
schedule, new recipient) survive restarts.

The job is a ``systemEvent`` that Galadriel dispatches to
``POST /agents/rumil/run``. It has to go through the API rather than run
in-process in the worker: the snapshot must land in the API's own Finrod,
the one ``finrod_query`` searches.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agents.galadriel.models import Job, JobDelivery, JobPayload, JobSchedule
from agents.galadriel.scheduler import Schedule, next_run_ms
from agents.galadriel.store import read_job, save_job
from core.config import settings
from core.logging import get_logger

log = get_logger("agents.rumil.cron")

GITHUB_AUDIT_JOB_ID = "rumil_github_audit"
GITHUB_AUDIT_EVENT = "github_audit"


def _first_chat_id() -> str | None:
    # The raw setting, not telegram_chat_allowlist: that is a frozenset, and
    # "first" has to mean the first one the operator listed.
    for part in settings.telegram_allowed_chat_ids.split(","):
        if part.strip():
            return part.strip()
    return None


def ensure_github_audit_cron(
    redis,
    *,
    cron_expr: str = "0 8 * * *",
    tz: str = "America/Los_Angeles",
) -> Job:
    existing = read_job(redis, GITHUB_AUDIT_JOB_ID)
    if existing is not None:
        log.info(
            "github_audit_cron_already_present",
            job_id=existing.id,
            enabled=existing.enabled,
            cron=existing.schedule.expr,
        )
        return existing

    chat_id = _first_chat_id()
    delivery = (
        JobDelivery(mode="telegram", to=chat_id) if chat_id else JobDelivery(mode="none")
    )
    if chat_id is None:
        log.warning("github_audit_cron_no_telegram_chat", note="set TELEGRAM_ALLOWED_CHAT_IDS")

    sched = Schedule(kind="cron", expr=cron_expr, tz=tz)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    job = Job(
        id=GITHUB_AUDIT_JOB_ID,
        name="GitHub daily audit",
        schedule=JobSchedule(kind="cron", expr=cron_expr, tz=tz),
        payload=JobPayload(kind="systemEvent", text=GITHUB_AUDIT_EVENT, timeout_seconds=90),
        delivery=delivery,
        enabled=True,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        next_run_at_ms=next_run_ms(sched),
    )
    save_job(redis, job)
    log.info("github_audit_cron_ensured", job_id=job.id, cron=cron_expr, tz=tz, to=chat_id)
    return job
