from __future__ import annotations

import json
import time
from typing import Any, ClassVar

from agents.base import BaseAgent
from core.logging import get_logger
from core.models import AgentResult, AgentTask, TaskStatus
from core.redis_client import (
    RESULT_TTL_SECONDS,
    TASK_QUEUE_KEY,
    get_redis_sync,
    task_result_key,
)

log = get_logger("agents.earendil.agent")

WAIT_TIMEOUT_SECONDS = 15
WAIT_FAST_PATH_DELAY = 0.5
WAIT_POLL_INTERVAL = 0.5


def plan_task(message: str) -> dict[str, Any]:
    """Keyword-based planner. Intentional thin executor — no LLM call."""
    msg = message.lower().strip()

    if "system status" in msg:
        return {"workflow": [{"command": "uptime"}, {"command": "df -h"}, {"command": "free -m"}]}
    if any(x in msg for x in ["whoami", "who am i", "current user"]):
        return {"command": "whoami"}
    if any(x in msg for x in ["list files", "ls", "list directory", "show files"]):
        return {"command": "ls -la"}
    if any(x in msg for x in ["pwd", "current directory", "where am i"]):
        return {"command": "pwd"}
    if any(x in msg for x in ["date", "time", "what time", "current time"]):
        return {"command": "date"}
    return {"command": message}


def normalize_task(plan_output: dict) -> dict | list[dict]:
    if "command" in plan_output:
        return {
            "type": "system",
            "action": "run_command",
            "payload": {"command": plan_output["command"]},
        }
    if "workflow" in plan_output:
        return [
            {
                "type": "system",
                "action": "run_command",
                "payload": {"command": step["command"]},
            }
            for step in plan_output["workflow"]
        ]
    raise ValueError("Invalid plan output")


def enqueue_task(r, task: dict, task_id: str | None = None) -> str:
    from uuid import uuid4

    if not task_id:
        task_id = str(uuid4())
    task["task_id"] = task_id

    r.set(
        task_result_key(task_id),
        json.dumps({"status": TaskStatus.QUEUED, "result": None, "error": None}),
        ex=RESULT_TTL_SECONDS,
    )
    r.rpush(TASK_QUEUE_KEY, json.dumps(task))
    return task_id


def wait_for_tasks(r, task_ids: list[str], timeout: int = WAIT_TIMEOUT_SECONDS) -> dict:
    start = time.time()
    time.sleep(WAIT_FAST_PATH_DELAY)

    while True:
        results = []
        all_done = True

        for tid in task_ids:
            raw = r.get(task_result_key(tid))
            if not raw:
                all_done = False
                continue
            data = json.loads(raw)
            result_data = data.get("result")
            results.append(
                {
                    "task_id": tid,
                    "status": data["status"],
                    "output": result_data.get("result")
                    if isinstance(result_data, dict)
                    else result_data,
                    "error": data.get("error"),
                }
            )
            if data["status"] not in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                all_done = False

        if all_done and len(results) == len(task_ids):
            return {"status": "completed", "results": results}

        if time.time() - start > timeout:
            return {"status": "partial", "results": results}

        time.sleep(WAIT_POLL_INTERVAL)


class Earendil(BaseAgent):
    """System executor agent.

    Accepts an AgentTask whose payload contains either a natural-language
    `message` (planned, normalized, enqueued) or a pre-formed `task` dict
    (enqueued directly). When `payload["wait"]` is truthy, blocks until
    results land or `WAIT_TIMEOUT_SECONDS` elapses.
    """

    tier: ClassVar[str] = "executor"
    name: ClassVar[str] = "earendil"

    async def run(self, task: AgentTask) -> AgentResult:
        r = get_redis_sync()
        payload = task.payload
        wait = bool(payload.get("wait"))

        try:
            if "message" in payload:
                plan_output = plan_task(payload["message"])
                normalized = normalize_task(plan_output)
                queued = normalized if isinstance(normalized, list) else [normalized]
            elif "task" in payload:
                t = payload["task"]
                queued = t if isinstance(t, list) else [t]
            else:
                return AgentResult(
                    task_id=task.task_id,
                    agent=self.name,
                    status=TaskStatus.FAILED,
                    error="payload must contain 'message' or 'task'",
                )

            task_ids = [enqueue_task(r, t) for t in queued]
            log.info("tasks_enqueued", agent_task_id=task.task_id, queued=task_ids)

            if wait:
                wait_result = wait_for_tasks(r, task_ids)
                status = (
                    TaskStatus.COMPLETED
                    if wait_result["status"] == "completed"
                    else TaskStatus.RUNNING
                )
                return AgentResult(
                    task_id=task.task_id,
                    agent=self.name,
                    status=status,
                    result={"task_ids": task_ids, "results": wait_result["results"]},
                )

            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.QUEUED,
                result={"task_ids": task_ids},
            )

        except Exception as e:
            log.error("earendil_run_failed", agent_task_id=task.task_id, exception=str(e))
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error=str(e),
            )
