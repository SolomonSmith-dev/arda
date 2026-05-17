from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from agents.earendil.agent import (
    enqueue_task,
    normalize_task,
    wait_for_tasks,
)
from agents.sauron.planner import plan as sauron_plan
from api.middleware.auth import require_api_key
from core.logging import get_logger
from core.models import AgentTask, TaskStatus
from core.redis_client import RESULT_TTL_SECONDS, get_redis_sync, task_result_key

log = get_logger("api.routes.tasks")

router = APIRouter(dependencies=[Depends(require_api_key)])


class PlanRequest(BaseModel):
    message: str


class TaskRequest(BaseModel):
    type: str
    action: str | None = None
    payload: dict = {}


class ExecuteResultRequest(BaseModel):
    tasks: list[str]


def _shell_command_from_message(message: str) -> dict:
    """Map an earendil-intent message to a runnable command dict.

    Mirrors the legacy keyword router; Sauron's planner already chose
    'earendil' as the intent so we only need to emit the actual shell
    invocation. For unknown phrasing we treat the message itself as
    the command (legacy behavior).
    """
    msg = message.lower().strip()
    if "system status" in msg:
        return {"workflow": [{"command": "uptime"}, {"command": "df -h"}, {"command": "free -m"}]}
    if any(x in msg for x in ["whoami", "who am i", "current user"]):
        return {"command": "whoami"}
    if any(x in msg for x in ["list files", "list directory", "show files"]) or msg == "ls":
        return {"command": "ls -la"}
    if any(x in msg for x in ["pwd", "current directory", "where am i"]):
        return {"command": "pwd"}
    if msg in ("date", "time", "what time", "current time"):
        return {"command": "date"}
    return {"command": message}


@router.post("/plan")
def handle_plan(req: PlanRequest) -> dict:
    p = sauron_plan(req.message)
    return {
        "message": req.message,
        "intent": p.intent,
        "subtasks": [
            {"specialist": s.specialist, "payload": s.payload} for s in p.subtasks
        ],
    }


@router.post("/task")
def handle_task(req: TaskRequest) -> dict:
    r = get_redis_sync()
    try:
        if req.type == "system" and req.action == "run_command":
            task_id = enqueue_task(
                r,
                {"type": "system", "action": "run_command", "payload": req.payload},
            )
            return {"status": "queued", "task_id": task_id, "executor": "earendil_worker"}

        if req.type == "memory":
            if req.action == "set":
                r.set(req.payload.get("key"), req.payload.get("value"))
                return {"status": "success", "message": "stored"}
            if req.action == "get":
                value = r.get(req.payload.get("key"))
                return {"status": "success", "value": value}

        if req.type == "async":
            enqueue_task(r, req.payload)
            return {"status": "queued", "queue": "task_queue"}

        return {"status": "error", "error": "unsupported task type/action"}
    except Exception as e:
        log.error("handle_task_failed", exception=str(e))
        return {"status": "error", "error": str(e)}


@router.post("/execute")
async def handle_execute(req: PlanRequest, request: Request) -> dict:
    """Plan + dispatch.

    Shell intents go through the Earendil worker queue so legacy clients
    that poll /result/{task_id} keep working byte-for-byte. Non-shell
    intents run Sauron synchronously; we still allocate a task_id and
    persist the result to Redis so /result/{task_id} returns the same
    shape regardless of path.
    """
    sauron = request.app.state.sauron
    p = sauron_plan(req.message)
    r = get_redis_sync()

    if p.intent == "earendil":
        plan_output = _shell_command_from_message(req.message)
        normalized = normalize_task(plan_output)
        if isinstance(normalized, list):
            task_ids = [enqueue_task(r, t) for t in normalized]
            return {
                "status": "queued",
                "message": req.message,
                "task_ids": task_ids,
                "executor": "earendil_worker",
            }
        task_id = enqueue_task(r, normalized)
        return {
            "status": "queued",
            "message": req.message,
            "task_id": task_id,
            "executor": "earendil_worker",
        }

    task_id = str(uuid4())
    task = AgentTask(
        task_id=task_id,
        agent="sauron",
        type="orchestrate",
        payload={"message": req.message},
    )
    result = await sauron.run(task)
    r.set(
        task_result_key(task_id),
        json.dumps(
            {
                "status": str(result.status),
                "result": result.result,
                "error": result.error,
            }
        ),
        ex=RESULT_TTL_SECONDS,
    )
    return {
        "status": str(result.status),
        "message": req.message,
        "task_id": task_id,
        "executor": p.intent,
        "result": result.result,
        "error": result.error,
    }


@router.post("/execute/wait")
async def handle_execute_wait(req: PlanRequest, request: Request) -> dict:
    """Plan + execute + block until results land (or timeout)."""
    sauron = request.app.state.sauron
    p = sauron_plan(req.message)
    r = get_redis_sync()

    if p.intent == "earendil":
        plan_output = _shell_command_from_message(req.message)
        normalized = normalize_task(plan_output)
        normalized_list = normalized if isinstance(normalized, list) else [normalized]
        task_ids = [enqueue_task(r, t) for t in normalized_list]
        wait_result = wait_for_tasks(r, task_ids)
        wait_result["message"] = req.message
        wait_result["tasks"] = task_ids
        return wait_result

    task_id = str(uuid4())
    task = AgentTask(
        task_id=task_id,
        agent="sauron",
        type="orchestrate",
        payload={"message": req.message},
    )
    result = await sauron.run(task)
    r.set(
        task_result_key(task_id),
        json.dumps(
            {
                "status": str(result.status),
                "result": result.result,
                "error": result.error,
            }
        ),
        ex=RESULT_TTL_SECONDS,
    )
    return {
        "status": "completed" if result.status == TaskStatus.COMPLETED else "partial",
        "message": req.message,
        "tasks": [task_id],
        "results": [
            {
                "task_id": task_id,
                "status": str(result.status),
                "output": result.result,
                "error": result.error,
            }
        ],
    }


@router.post("/execute/result")
def handle_execute_result(req: ExecuteResultRequest) -> dict:
    r = get_redis_sync()
    results = []
    for tid in req.tasks:
        raw = r.get(task_result_key(tid))
        if not raw:
            results.append({"task_id": tid, "status": "not_found"})
            continue
        data = json.loads(raw)
        results.append(
            {
                "task_id": tid,
                "status": data.get("status"),
                "result": data.get("result"),
                "error": data.get("error"),
            }
        )

    if all(x.get("status") == TaskStatus.COMPLETED for x in results):
        overall = "completed"
    elif any(x.get("status") == TaskStatus.FAILED for x in results):
        overall = "failed"
    else:
        overall = "running"

    return {"status": overall, "results": results}


@router.get("/result/{task_id}")
def get_result(task_id: str) -> dict:
    r = get_redis_sync()
    raw = r.get(task_result_key(task_id))
    if not raw:
        return {"task_id": task_id, "status": "not_found", "result": None, "error": None}
    state = json.loads(raw)
    return {
        "task_id": task_id,
        "status": state.get("status"),
        "result": state.get("result"),
        "error": state.get("error"),
    }
