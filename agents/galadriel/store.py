"""Redis-backed cron job store.

Layout:
  - ``cron:job:<id>`` (string) — JSON blob of the :class:`Job`
  - ``cron:queue`` (zset)     — score = ``next_run_at_ms``, member = job id

``pop_due`` atomically claims due jobs by removing them from ``cron:queue``;
the winner of the race executes, the loser sees a no-op.
"""

from __future__ import annotations

from typing import Any, cast

from redis import Redis

from agents.galadriel.models import Job


def _as_text(raw: Any) -> str:
    """redis-py types every command as ``Awaitable[Any] | Any`` because the
    sync and async clients share one command mixin. Every call in this module
    is on a sync client, so narrow once here instead of scattering casts.
    """
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


JOB_KEY_PREFIX = "cron:job:"
QUEUE_KEY = "cron:queue"


def _job_key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}{job_id}"


def save_job(redis: Redis, job: Job) -> None:
    redis.set(_job_key(job.id), job.model_dump_json())
    if job.next_run_at_ms is not None and job.enabled:
        redis.zadd(QUEUE_KEY, {job.id: job.next_run_at_ms})
    else:
        redis.zrem(QUEUE_KEY, job.id)


def read_job(redis: Redis, job_id: str) -> Job | None:
    raw = redis.get(_job_key(job_id))
    if raw is None:
        return None
    return Job.model_validate_json(_as_text(raw))


def list_jobs(redis: Redis) -> list[Job]:
    out: list[Job] = []
    for key in redis.scan_iter(f"{JOB_KEY_PREFIX}*"):
        raw = redis.get(key)
        if raw is None:
            continue
        out.append(Job.model_validate_json(_as_text(raw)))
    return out


def delete_job(redis: Redis, job_id: str) -> bool:
    """Returns True if the job existed and was removed."""
    existed = cast(int, redis.delete(_job_key(job_id))) > 0
    redis.zrem(QUEUE_KEY, job_id)
    return existed


def pop_due(redis: Redis, now_ms: int, limit: int = 10) -> list[Job]:
    """Atomically claim and return jobs whose ``next_run_at_ms <= now_ms``."""
    candidates = cast(list[Any], redis.zrangebyscore(QUEUE_KEY, 0, now_ms, start=0, num=limit))
    claimed: list[Job] = []
    for raw_id in candidates:
        job_id = raw_id.decode("utf-8") if isinstance(raw_id, bytes) else raw_id
        if cast(int, redis.zrem(QUEUE_KEY, job_id)) == 0:
            continue  # lost the race to another worker
        job = read_job(redis, job_id)
        if job is not None and job.enabled:
            claimed.append(job)
    return claimed
