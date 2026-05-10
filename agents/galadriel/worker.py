"""Galadriel cron scheduler worker.

Polls ``cron:queue`` every :data:`POLL_INTERVAL_SECONDS`, claims due jobs
via :func:`agents.galadriel.store.pop_due`, executes them, and reschedules.

Started as a separate compose service: ``python -m agents.galadriel.worker``.
"""

from __future__ import annotations

import time

import httpx

from agents.galadriel.models import Job
from agents.galadriel.scheduler import Schedule, is_one_shot, next_run_ms
from agents.galadriel.store import delete_job, pop_due, save_job
from core.config import settings
from core.logging import get_logger
from core.redis_client import get_redis_sync

log = get_logger("agents.galadriel.worker")

POLL_INTERVAL_SECONDS = 5
ERROR_BACKOFF_SECONDS = 10
EXEC_TIMEOUT_BUFFER_SECONDS = 5


def execute_agent_turn(client: httpx.Client, job: Job) -> dict:
    """POST to /execute/wait with the job's message; return parsed response."""
    if not job.payload.message:
        raise ValueError("agentTurn payload requires message")
    timeout = job.payload.timeout_seconds + EXEC_TIMEOUT_BUFFER_SECONDS
    resp = client.post("/execute/wait", json={"message": job.payload.message}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def announce(job: Job, result: dict) -> None:
    """Stub delivery hook. Replaced when the Telegram/Discord transport lands."""
    if job.delivery.mode != "announce" or not job.delivery.to:
        return
    log.info(
        "delivery_announce",
        job_id=job.id,
        to=job.delivery.to,
        result_status=result.get("status"),
    )


def reschedule(redis, job: Job) -> None:
    """Compute next_run_at_ms and either re-queue or retire the job."""
    sched = Schedule(
        kind=job.schedule.kind,
        expr=job.schedule.expr,
        tz=job.schedule.tz,
        at_iso=job.schedule.at_iso,
    )

    if is_one_shot(sched) and job.delete_after_run:
        delete_job(redis, job.id)
        log.info("job_deleted_after_run", job_id=job.id)
        return

    if is_one_shot(sched):
        updated = job.model_copy(update={"next_run_at_ms": None, "enabled": False})
    else:
        updated = job.model_copy(update={"next_run_at_ms": next_run_ms(sched)})

    save_job(redis, updated)


def run_one(client: httpx.Client, redis, job: Job) -> Job:
    started = time.monotonic()
    log.info("job_run_start", job_id=job.id, name=job.name, kind=job.payload.kind)

    try:
        if job.payload.kind == "agentTurn":
            result = execute_agent_turn(client, job)
        else:
            result = {"status": "logged", "text": job.payload.text}
            log.info("system_event", job_id=job.id, text=job.payload.text)

        announce(job, result)
        duration_ms = int((time.monotonic() - started) * 1000)
        job = job.model_copy(
            update={
                "last_run_at_ms": int(time.time() * 1000),
                "last_status": "ok",
                "last_duration_ms": duration_ms,
                "last_error": None,
                "consecutive_errors": 0,
            }
        )
        log.info("job_run_ok", job_id=job.id, duration_ms=duration_ms)

    except Exception as e:
        duration_ms = int((time.monotonic() - started) * 1000)
        job = job.model_copy(
            update={
                "last_run_at_ms": int(time.time() * 1000),
                "last_status": "error",
                "last_duration_ms": duration_ms,
                "last_error": str(e),
                "consecutive_errors": job.consecutive_errors + 1,
            }
        )
        log.error("job_run_failed", job_id=job.id, exc=str(e))

    reschedule(redis, job)
    return job


def run_forever() -> None:
    redis = get_redis_sync()
    base_url = settings.internal_api_url
    headers = {"x-api-key": settings.arda_api_key}

    log.info("galadriel_worker_starting", base_url=base_url)

    with httpx.Client(base_url=base_url, headers=headers) as client:
        while True:
            try:
                now_ms = int(time.time() * 1000)
                for job in pop_due(redis, now_ms):
                    run_one(client, redis, job)
                time.sleep(POLL_INTERVAL_SECONDS)
            except Exception as e:
                log.error("worker_loop_error", exc=str(e))
                time.sleep(ERROR_BACKOFF_SECONDS)


if __name__ == "__main__":
    run_forever()
