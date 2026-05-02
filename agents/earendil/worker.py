from __future__ import annotations

import json
import subprocess
import time
from typing import Any

from core.logging import get_logger
from core.models import TaskStatus
from core.redis_client import (
    RESULT_TTL_SECONDS,
    TASK_QUEUE_KEY,
    get_redis_sync,
    task_result_key,
)

log = get_logger("agents.earendil.worker")

POLL_INTERVAL_SECONDS = 1
ERROR_BACKOFF_SECONDS = 5
COMMAND_TIMEOUT_SECONDS = 30


def dequeue_task(r) -> dict | None:
    item = r.lpop(TASK_QUEUE_KEY)
    return json.loads(item) if item else None


def execute_system_task(payload: dict) -> dict[str, Any]:
    command = payload.get("command", "")
    if not command:
        return {"status": "error", "error": "no command specified"}

    try:
        result = subprocess.check_output(
            command,
            shell=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            stderr=subprocess.STDOUT,
        )
        return {"status": "success", "result": result.strip()}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "command timed out"}
    except subprocess.CalledProcessError as e:
        return {
            "status": "error",
            "error": f"command failed with exit code {e.returncode}",
            "output": e.output or "",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _store_result(r, task_id: str, payload: dict) -> None:
    r.set(task_result_key(task_id), json.dumps(payload), ex=RESULT_TTL_SECONDS)


def process_task(r, task: dict) -> None:
    task_id = task.get("task_id", "unknown")
    log.info("task_processing", task_id=task_id)

    _store_result(r, task_id, {"status": TaskStatus.RUNNING, "result": None, "error": None})
    log.info("task_status", task_id=task_id, status=TaskStatus.RUNNING)

    if task.get("type") == "system" and task.get("action") == "run_command":
        try:
            result = execute_system_task(task.get("payload", {}))
            _store_result(
                r,
                task_id,
                {"status": TaskStatus.COMPLETED, "result": result, "error": None},
            )
            log.info("task_status", task_id=task_id, status=TaskStatus.COMPLETED)
        except Exception as e:
            _store_result(
                r,
                task_id,
                {"status": TaskStatus.FAILED, "result": None, "error": str(e)},
            )
            log.error("task_failed", task_id=task_id, exception=str(e))
    else:
        _store_result(
            r,
            task_id,
            {
                "status": TaskStatus.FAILED,
                "result": None,
                "error": f"Unsupported task type: {task.get('type')}",
            },
        )
        log.warning("task_unsupported_type", task_id=task_id, type=task.get("type"))


def run_forever() -> None:
    r = get_redis_sync()
    log.info("worker_started")

    while True:
        try:
            task = dequeue_task(r)
            if task:
                process_task(r, task)
            time.sleep(POLL_INTERVAL_SECONDS)
        except Exception as e:
            log.error("worker_loop_error", exception=str(e))
            time.sleep(ERROR_BACKOFF_SECONDS)


if __name__ == "__main__":
    run_forever()
