"""HTTP-level tests for the unified ARDA API.

Uses TestClient against api.main.app with mock LLM (default), fakeredis
patched into every redis-touching module, and Finrod's in-memory store
fallback. Verifies legacy contracts the MCP server depends on.
"""

from __future__ import annotations

import json
import threading
import time

import fakeredis
import pytest
from fastapi.testclient import TestClient

from agents.earendil import agent as earendil_module
from agents.earendil import worker as earendil_worker
from agents.tombombadil import agent as tombombadil_module
from api.routes import query as query_routes
from api.routes import tasks as tasks_routes
from core import redis_client as core_redis
from core.redis_client import TASK_QUEUE_KEY, task_result_key

API_KEY = "arda-dev-key-2026"


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(core_redis, "get_redis_sync", lambda: r)
    monkeypatch.setattr(earendil_module, "get_redis_sync", lambda: r)
    monkeypatch.setattr(earendil_worker, "get_redis_sync", lambda: r)
    monkeypatch.setattr(tombombadil_module, "get_redis_sync", lambda: r)
    monkeypatch.setattr(tasks_routes, "get_redis_sync", lambda: r)
    monkeypatch.setattr(query_routes, "get_redis_sync", lambda: r)
    return r


@pytest.fixture
def client(fake_redis):
    from api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def _auth() -> dict:
    return {"x-api-key": API_KEY}


def test_health_no_auth(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "online"
    assert body["agent"] == "earendil"  # legacy compat — see plan
    assert "version" in body


def test_unauthorized_returns_401(client: TestClient):
    resp = client.post("/plan", json={"message": "uptime"})
    assert resp.status_code == 401


def test_plan_returns_intent_and_subtasks(client: TestClient):
    resp = client.post("/plan", json={"message": "uptime"}, headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "earendil"
    assert body["subtasks"][0]["specialist"] == "earendil"


def test_task_system_run_command_enqueues(client: TestClient, fake_redis):
    resp = client.post(
        "/task",
        json={
            "type": "system",
            "action": "run_command",
            "payload": {"command": "echo hi"},
        },
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["executor"] == "earendil_worker"
    assert "task_id" in body
    assert fake_redis.llen(TASK_QUEUE_KEY) == 1


def test_task_memory_set_get(client: TestClient):
    set_resp = client.post(
        "/task",
        json={"type": "memory", "action": "set", "payload": {"key": "k1", "value": "v1"}},
        headers=_auth(),
    )
    assert set_resp.json()["status"] == "success"

    get_resp = client.post(
        "/task",
        json={"type": "memory", "action": "get", "payload": {"key": "k1"}},
        headers=_auth(),
    )
    assert get_resp.json() == {"status": "success", "value": "v1"}


def test_execute_shell_returns_task_id_clients_can_poll(client: TestClient, fake_redis):
    resp = client.post("/execute", json={"message": "whoami"}, headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["executor"] == "earendil_worker"
    task_id = body["task_id"]

    # Worker hasn't run; result should be QUEUED in redis
    poll = client.get(f"/result/{task_id}", headers=_auth()).json()
    assert poll["status"] == "queued"


def test_execute_non_shell_runs_sauron_synchronously(client: TestClient, fake_redis):
    resp = client.post(
        "/execute",
        json={"message": "Name: Solomon\nFilm: Ran\nRating: 9"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["executor"] == "tombombadil"
    assert body["status"] == "completed"
    assert body["task_id"]
    # And the synthetic task_id is poll-able
    poll = client.get(f"/result/{body['task_id']}", headers=_auth()).json()
    assert poll["status"] == "completed"


def test_execute_wait_drains_queue(client: TestClient, fake_redis, monkeypatch):
    """Execute_wait should block until a worker drains the queue."""

    def drain_after_delay():
        time.sleep(0.2)
        from agents.earendil.worker import process_task

        while True:
            raw = fake_redis.lpop(TASK_QUEUE_KEY)
            if not raw:
                break
            process_task(fake_redis, json.loads(raw))

    t = threading.Thread(target=drain_after_delay, daemon=True)
    t.start()

    resp = client.post("/execute/wait", json={"message": "echo arda"}, headers=_auth())
    t.join(timeout=20)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["results"][0]["status"] == "completed"


def test_execute_result_aggregates(client: TestClient, fake_redis):
    fake_redis.set(
        task_result_key("a"),
        json.dumps({"status": "completed", "result": {"x": 1}, "error": None}),
    )
    fake_redis.set(
        task_result_key("b"),
        json.dumps({"status": "running", "result": None, "error": None}),
    )
    resp = client.post("/execute/result", json={"tasks": ["a", "b", "missing"]}, headers=_auth())
    body = resp.json()
    assert body["status"] == "running"
    assert {r["task_id"] for r in body["results"]} == {"a", "b", "missing"}


def test_result_missing_returns_not_found(client: TestClient):
    resp = client.get("/result/does-not-exist", headers=_auth())
    body = resp.json()
    assert body["status"] == "not_found"


def test_query_system_status_shape(client: TestClient, fake_redis):
    resp = client.post(
        "/query",
        json={"type": "system", "action": "status"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.json()
    # The MCP server's arda_status reads exactly these keys — see legacy lines 423-431
    for key in ("api", "redis", "worker", "openclaw_gateway", "queue_depth", "tracked_tasks"):
        assert key in body
    assert body["api"] == "online"


def test_query_redis_lookup(client: TestClient, fake_redis):
    fake_redis.set("hello", json.dumps({"k": "v"}))
    resp = client.post("/query", json={"type": "redis", "key": "hello"}, headers=_auth())
    body = resp.json()
    assert body["exists"] is True
    assert body["value"] == {"k": "v"}


def test_query_queue_length(client: TestClient, fake_redis):
    fake_redis.rpush(TASK_QUEUE_KEY, "a", "b", "c")
    resp = client.post(
        "/query", json={"type": "redis", "action": "queue_length"}, headers=_auth()
    )
    assert resp.json()["length"] == 3


def test_agents_health_lists_all_four(client: TestClient):
    resp = client.get("/agents/health", headers=_auth())
    body = resp.json()
    names = {a["agent"] for a in body["agents"]}
    assert names == {"sauron", "earendil", "finrod", "tombombadil"}


def test_agent_direct_run(client: TestClient, fake_redis):
    resp = client.post(
        "/agents/finrod/run",
        json={
            "agent": "finrod",
            "type": "ingest",
            "payload": {"action": "ingest", "doc_id": "d1", "text": "ARDA is great."},
        },
        headers=_auth(),
    )
    body = resp.json()
    assert body["status"] == "completed"
    assert body["result"]["doc_id"] == "d1"


def test_memory_ingest_then_query(client: TestClient):
    ing = client.post(
        "/memory/ingest",
        json={"doc_id": "arda-overview", "text": "ARDA is the unified multi-agent system."},
        headers=_auth(),
    )
    assert ing.json()["status"] == "completed"

    q = client.post("/memory/query", json={"message": "what is ARDA?"}, headers=_auth())
    body = q.json()
    assert body["status"] == "completed"
    assert "answer" in body["result"]
