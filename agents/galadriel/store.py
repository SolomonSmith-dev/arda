"""Redis-backed cron job store.

Layout:
  - ``cron:job:<id>`` (string) — JSON blob of the :class:`Job`
  - ``cron:queue`` (zset)     — score = ``next_run_at_ms``, member = job id

``pop_due`` atomically claims due jobs by removing them from ``cron:queue``;
the winner of the race executes, the loser sees a no-op.
"""

from __future__ import annotations

from redis import Redis

from agents.galadriel.models import Job

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
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return Job.model_validate_json(raw)


def list_jobs(redis: Redis) -> list[Job]:
    out: list[Job] = []
    for key in redis.scan_iter(f"{JOB_KEY_PREFIX}*"):
        raw = redis.get(key)
        if raw is None:
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        out.append(Job.model_validate_json(raw))
    return out


def delete_job(redis: Redis, job_id: str) -> bool:
    """Returns True if the job existed and was removed."""
    existed = redis.delete(_job_key(job_id)) > 0
    redis.zrem(QUEUE_KEY, job_id)
    return existed


def pop_due(redis: Redis, now_ms: int, limit: int = 10) -> list[Job]:
    """Atomically claim and return jobs whose ``next_run_at_ms <= now_ms``."""
    candidates = redis.zrangebyscore(QUEUE_KEY, 0, now_ms, start=0, num=limit)
    claimed: list[Job] = []
    for raw_id in candidates:
        job_id = raw_id.decode("utf-8") if isinstance(raw_id, bytes) else raw_id
        if redis.zrem(QUEUE_KEY, job_id) == 0:
            continue  # lost the race to another worker
        job = read_job(redis, job_id)
        if job is not None and job.enabled:
            claimed.append(job)
    return claimed
