"""HTTP tests for /cron CRUD routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import fakeredis
import pytest
from fastapi.testclient import TestClient

from api.routes import cron as cron_routes
from core import redis_client as core_redis

API_KEY = "arda-dev-key-2026"


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(core_redis, "get_redis_sync", lambda: r)
    monkeypatch.setattr(cron_routes, "get_redis_sync", lambda: r)
    return r


@pytest.fixture
def client(fake_redis):
    from api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def _auth() -> dict:
    return {"x-api-key": API_KEY}


def _daily_8am_body() -> dict:
    return {
        "name": "daily-audit",
        "schedule": {"kind": "cron", "expr": "0 8 * * *", "tz": "America/Los_Angeles"},
        "payload": {"kind": "agentTurn", "message": "check disk usage", "timeout_seconds": 60},
    }


def test_create_cron_job_201_with_id_and_next_run(client: TestClient):
    resp = client.post("/cron", headers=_auth(), json=_daily_8am_body())
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"]
    assert data["name"] == "daily-audit"
    assert data["next_run_at_ms"] is not None
    assert data["enabled"] is True
    assert data["consecutive_errors"] == 0


def test_create_at_in_future_succeeds(client: TestClient):
    target = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    resp = client.post(
        "/cron",
        headers=_auth(),
        json={
            "name": "one-shot",
            "schedule": {"kind": "at", "at_iso": target},
            "payload": {"kind": "systemEvent", "text": "remind me"},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["next_run_at_ms"] is not None


def test_create_at_in_past_returns_400(client: TestClient):
    target = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    resp = client.post(
        "/cron",
        headers=_auth(),
        json={
            "name": "stale",
            "schedule": {"kind": "at", "at_iso": target},
            "payload": {"kind": "systemEvent", "text": "x"},
        },
    )
    assert resp.status_code == 400
    assert "future" in resp.json()["detail"].lower()


def test_create_invalid_cron_expr_returns_400(client: TestClient):
    body = _daily_8am_body()
    body["schedule"]["expr"] = None  # missing
    resp = client.post("/cron", headers=_auth(), json=body)
    assert resp.status_code == 400


def test_get_job_404_when_missing(client: TestClient):
    resp = client.get("/cron/does-not-exist", headers=_auth())
    assert resp.status_code == 404


def test_list_jobs_returns_created(client: TestClient):
    client.post("/cron", headers=_auth(), json=_daily_8am_body())
    body2 = _daily_8am_body()
    body2["name"] = "second"
    client.post("/cron", headers=_auth(), json=body2)

    resp = client.get("/cron", headers=_auth())
    assert resp.status_code == 200
    names = sorted(j["name"] for j in resp.json()["jobs"])
    assert names == ["daily-audit", "second"]


def test_get_job_roundtrip(client: TestClient):
    create = client.post("/cron", headers=_auth(), json=_daily_8am_body())
    job_id = create.json()["id"]

    resp = client.get(f"/cron/{job_id}", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id


def test_delete_job(client: TestClient):
    create = client.post("/cron", headers=_auth(), json=_daily_8am_body())
    job_id = create.json()["id"]

    resp = client.delete(f"/cron/{job_id}", headers=_auth())
    assert resp.status_code == 204

    follow = client.get(f"/cron/{job_id}", headers=_auth())
    assert follow.status_code == 404


def test_delete_missing_returns_404(client: TestClient):
    resp = client.delete("/cron/nope", headers=_auth())
    assert resp.status_code == 404


def test_cron_routes_require_auth(client: TestClient):
    resp = client.get("/cron")
    assert resp.status_code == 401
