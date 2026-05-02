from __future__ import annotations

import json

import fakeredis
import pytest

from agents.earendil import worker
from core.models import TaskStatus
from core.redis_client import TASK_QUEUE_KEY, task_result_key


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


def test_dequeue_returns_none_on_empty_queue(fake_redis):
    assert worker.dequeue_task(fake_redis) is None


def test_dequeue_pops_json_task(fake_redis):
    task = {"task_id": "abc", "type": "system", "action": "run_command"}
    fake_redis.rpush(TASK_QUEUE_KEY, json.dumps(task))
    assert worker.dequeue_task(fake_redis) == task


def test_process_task_writes_completed_for_run_command(fake_redis):
    task = {
        "task_id": "t1",
        "type": "system",
        "action": "run_command",
        "payload": {"command": "echo hello"},
    }
    worker.process_task(fake_redis, task)

    raw = fake_redis.get(task_result_key("t1"))
    assert raw is not None
    payload = json.loads(raw)
    assert payload["status"] == TaskStatus.COMPLETED
    assert payload["result"]["status"] == "success"
    assert "hello" in payload["result"]["result"]


def test_process_task_marks_failed_on_unsupported_type(fake_redis):
    task = {"task_id": "t2", "type": "wat", "action": "run_command"}
    worker.process_task(fake_redis, task)

    payload = json.loads(fake_redis.get(task_result_key("t2")))
    assert payload["status"] == TaskStatus.FAILED
    assert "Unsupported" in payload["error"]


def test_execute_system_task_missing_command():
    result = worker.execute_system_task({})
    assert result["status"] == "error"
    assert "no command" in result["error"]


def test_full_enqueue_dequeue_process_cycle(fake_redis):
    task = {
        "task_id": "cycle-1",
        "type": "system",
        "action": "run_command",
        "payload": {"command": "printf done"},
    }
    fake_redis.rpush(TASK_QUEUE_KEY, json.dumps(task))

    dequeued = worker.dequeue_task(fake_redis)
    assert dequeued is not None
    worker.process_task(fake_redis, dequeued)

    payload = json.loads(fake_redis.get(task_result_key("cycle-1")))
    assert payload["status"] == TaskStatus.COMPLETED
    assert payload["result"]["result"] == "done"
