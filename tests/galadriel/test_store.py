from __future__ import annotations

import fakeredis
import pytest

from agents.galadriel.models import Job, JobDelivery, JobPayload, JobSchedule
from agents.galadriel.store import (
    QUEUE_KEY,
    delete_job,
    list_jobs,
    pop_due,
    read_job,
    save_job,
)


@pytest.fixture
def r():
    return fakeredis.FakeRedis()


def _make_job(job_id: str = "abc", next_run_at_ms: int | None = 1_000) -> Job:
    return Job(
        id=job_id,
        name="test",
        schedule=JobSchedule(kind="cron", expr="0 8 * * *", tz="UTC"),
        payload=JobPayload(kind="agentTurn", message="hello"),
        delivery=JobDelivery(),
        created_at_ms=0,
        updated_at_ms=0,
        next_run_at_ms=next_run_at_ms,
    )


def test_save_and_read_roundtrip(r):
    job = _make_job()
    save_job(r, job)
    got = read_job(r, "abc")
    assert got == job


def test_save_adds_to_queue_when_enabled_and_scheduled(r):
    job = _make_job(next_run_at_ms=5_000)
    save_job(r, job)
    assert r.zscore(QUEUE_KEY, "abc") == 5_000


def test_save_omits_disabled_job_from_queue(r):
    job = _make_job(next_run_at_ms=5_000).model_copy(update={"enabled": False})
    save_job(r, job)
    assert r.zscore(QUEUE_KEY, "abc") is None


def test_save_omits_unscheduled_job_from_queue(r):
    job = _make_job(next_run_at_ms=None)
    save_job(r, job)
    assert r.zscore(QUEUE_KEY, "abc") is None


def test_read_missing_returns_none(r):
    assert read_job(r, "nope") is None


def test_list_jobs_returns_all(r):
    save_job(r, _make_job("a"))
    save_job(r, _make_job("b"))
    jobs = list_jobs(r)
    ids = sorted(j.id for j in jobs)
    assert ids == ["a", "b"]


def test_delete_job(r):
    save_job(r, _make_job())
    assert delete_job(r, "abc") is True
    assert read_job(r, "abc") is None
    assert r.zscore(QUEUE_KEY, "abc") is None


def test_delete_missing_returns_false(r):
    assert delete_job(r, "nope") is False


def test_pop_due_claims_only_jobs_at_or_before_now_ms(r):
    save_job(r, _make_job("past", next_run_at_ms=1_000))
    save_job(r, _make_job("now", next_run_at_ms=2_000))
    save_job(r, _make_job("future", next_run_at_ms=10_000))

    claimed = pop_due(r, now_ms=2_000)
    ids = sorted(j.id for j in claimed)
    assert ids == ["now", "past"]
    # Future job remains queued
    assert r.zscore(QUEUE_KEY, "future") == 10_000


def test_pop_due_removes_claimed_from_queue(r):
    save_job(r, _make_job("a", next_run_at_ms=1_000))
    pop_due(r, now_ms=2_000)
    assert r.zscore(QUEUE_KEY, "a") is None
    # The job blob itself is still present (caller is responsible for re-scheduling or deleting)
    assert read_job(r, "a") is not None


def test_pop_due_skips_disabled_jobs(r):
    job = _make_job("a", next_run_at_ms=1_000).model_copy(update={"enabled": False})
    # Force into queue manually since save_job() would skip it
    r.set(f"cron:job:a", job.model_dump_json())
    r.zadd(QUEUE_KEY, {"a": 1_000})

    claimed = pop_due(r, now_ms=2_000)
    assert claimed == []
