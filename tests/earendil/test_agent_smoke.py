from __future__ import annotations

import json

import fakeredis
import pytest

from agents.earendil import agent as earendil_agent
from agents.earendil.agent import Earendil, enqueue_task, normalize_task, plan_task
from core.models import AgentTask, TaskStatus
from core.redis_client import TASK_QUEUE_KEY, task_result_key


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(earendil_agent, "get_redis_sync", lambda: r)
    return r


def test_plan_task_keyword_uptime():
    assert plan_task("show me system status") == {
        "workflow": [{"command": "uptime"}, {"command": "df -h"}, {"command": "free -m"}]
    }


def test_plan_task_keyword_whoami():
    assert plan_task("whoami") == {"command": "whoami"}


def test_plan_task_fallback_passes_message_through():
    assert plan_task("echo hello") == {"command": "echo hello"}


def test_normalize_task_single_command():
    out = normalize_task({"command": "uptime"})
    assert out == {
        "type": "system",
        "action": "run_command",
        "payload": {"command": "uptime"},
    }


def test_normalize_task_workflow_returns_list():
    out = normalize_task({"workflow": [{"command": "a"}, {"command": "b"}]})
    assert isinstance(out, list)
    assert len(out) == 2
    assert out[0]["payload"]["command"] == "a"


def test_normalize_task_invalid_raises():
    with pytest.raises(ValueError):
        normalize_task({"junk": True})


def test_enqueue_task_writes_queue_and_status(fake_redis):
    tid = enqueue_task(fake_redis, {"type": "system", "action": "run_command", "payload": {}})
    assert fake_redis.llen(TASK_QUEUE_KEY) == 1
    raw = fake_redis.get(task_result_key(tid))
    assert json.loads(raw)["status"] == TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_earendil_run_with_message_enqueues(fake_redis):
    e = Earendil()
    task = AgentTask(agent="earendil", type="execute", payload={"message": "uptime"})
    result = await e.run(task)
    assert result.status == TaskStatus.QUEUED
    assert len(result.result["task_ids"]) == 1
    assert fake_redis.llen(TASK_QUEUE_KEY) == 1


@pytest.mark.asyncio
async def test_earendil_run_with_workflow_message_enqueues_multiple(fake_redis):
    e = Earendil()
    task = AgentTask(agent="earendil", type="execute", payload={"message": "system status"})
    result = await e.run(task)
    assert result.status == TaskStatus.QUEUED
    assert len(result.result["task_ids"]) == 3
    assert fake_redis.llen(TASK_QUEUE_KEY) == 3


@pytest.mark.asyncio
async def test_earendil_run_with_direct_task(fake_redis):
    e = Earendil()
    direct = {"type": "system", "action": "run_command", "payload": {"command": "echo hi"}}
    task = AgentTask(agent="earendil", type="task", payload={"task": direct})
    result = await e.run(task)
    assert result.status == TaskStatus.QUEUED
    assert len(result.result["task_ids"]) == 1


@pytest.mark.asyncio
async def test_earendil_run_missing_payload_keys_fails(fake_redis):
    e = Earendil()
    task = AgentTask(agent="earendil", type="execute", payload={})
    result = await e.run(task)
    assert result.status == TaskStatus.FAILED
    assert "message" in result.error or "task" in result.error
