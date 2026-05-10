from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import fakeredis
import httpx
import pytest

from agents.galadriel.models import Job, JobDelivery, JobPayload, JobSchedule
from agents.galadriel.store import QUEUE_KEY, read_job, save_job
from agents.galadriel.worker import announce, reschedule, run_one


@pytest.fixture
def r():
    return fakeredis.FakeRedis()


def _cron_job(**overrides) -> Job:
    base = dict(
        id="job-1",
        name="daily-audit",
        schedule=JobSchedule(kind="cron", expr="0 8 * * *", tz="UTC"),
        payload=JobPayload(kind="agentTurn", message="check disk usage"),
        delivery=JobDelivery(),
        created_at_ms=0,
        updated_at_ms=0,
        next_run_at_ms=1_000,
    )
    base.update(overrides)
    return Job(**base)


def _at_job(**overrides) -> Job:
    future_iso = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    base = dict(
        id="one-shot-1",
        name="one-shot",
        schedule=JobSchedule(kind="at", at_iso=future_iso),
        payload=JobPayload(kind="systemEvent", text="reminder"),
        delivery=JobDelivery(),
        created_at_ms=0,
        updated_at_ms=0,
        next_run_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000) - 1,
    )
    base.update(overrides)
    return Job(**base)


def _client_returning(payload: dict, status_code: int = 200) -> httpx.Client:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(status_code, json=payload),
    )
    return httpx.Client(transport=transport, base_url="http://test")


def _client_raising(exc: Exception) -> httpx.Client:
    def handler(req):
        raise exc
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")


def test_run_one_success_records_ok_and_reschedules(r):
    job = _cron_job()
    save_job(r, job)
    client = _client_returning({"status": "completed", "results": [{"output": "fine"}]})

    result_job = run_one(client, r, job)

    assert result_job.last_status == "ok"
    assert result_job.consecutive_errors == 0
    assert result_job.last_error is None

    persisted = read_job(r, "job-1")
    assert persisted is not None
    assert persisted.last_status == "ok"
    # Next run should be in the future relative to when we ran it
    assert persisted.next_run_at_ms is not None
    assert persisted.next_run_at_ms > 1_000


def test_run_one_failure_increments_error_counter(r):
    job = _cron_job(consecutive_errors=2)
    save_job(r, job)
    client = _client_raising(httpx.ConnectError("api down"))

    result_job = run_one(client, r, job)

    assert result_job.last_status == "error"
    assert result_job.consecutive_errors == 3
    assert "api down" in (result_job.last_error or "")
    # Still rescheduled — failure does not stop the job
    persisted = read_job(r, "job-1")
    assert persisted.next_run_at_ms is not None
    assert persisted.next_run_at_ms > 1_000


def test_run_one_one_shot_with_delete_after_run_removes_job(r):
    job = _at_job(delete_after_run=True)
    save_job(r, job)
    client = _client_returning({"status": "logged"})

    run_one(client, r, job)

    assert read_job(r, "one-shot-1") is None
    assert r.zscore(QUEUE_KEY, "one-shot-1") is None


def test_run_one_one_shot_without_delete_disables_job(r):
    job = _at_job(delete_after_run=False)
    save_job(r, job)
    client = _client_returning({"status": "logged"})

    run_one(client, r, job)

    persisted = read_job(r, "one-shot-1")
    assert persisted is not None
    assert persisted.enabled is False
    assert persisted.next_run_at_ms is None
    assert r.zscore(QUEUE_KEY, "one-shot-1") is None


def test_run_one_system_event_does_not_call_http(r):
    job = _cron_job(payload=JobPayload(kind="systemEvent", text="just a log line"))
    save_job(r, job)

    def explode(_):
        raise AssertionError("HTTP must not be called for systemEvent")
    client = httpx.Client(transport=httpx.MockTransport(explode), base_url="http://test")

    result_job = run_one(client, r, job)
    assert result_job.last_status == "ok"


def test_announce_logs_when_delivery_mode_announce(caplog):
    job = _cron_job(delivery=JobDelivery(mode="announce", to="12345"))
    announce(job, {"status": "ok"})
    # No assertion on log content — just verify the call doesn't raise.
    # Real delivery integration arrives with the Telegram agent.


def test_reschedule_cron_advances(r):
    job = _cron_job()
    reschedule(r, job)
    persisted = read_job(r, "job-1")
    assert persisted is not None
    assert persisted.next_run_at_ms is not None
    assert persisted.next_run_at_ms > 1_000
