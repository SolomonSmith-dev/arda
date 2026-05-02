#!/usr/bin/env python3
"""
Earendil API Bridge
Minimal HTTP interface between Claude (MacBook) and local execution (Mac Mini)

Endpoints:
  POST /plan    - Natural language -> structured task (auth required)
  POST /task    - Execute structured task (auth required)
  GET  /result  - Poll task result (auth required)
  POST /query   - Read-only system state inspection (auth required)
  GET  /health  - Health check (open)
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import subprocess
import sys
import redis
import json
import uuid
import os
import time

app = FastAPI(title="Earendil", version="0.2.0")

# API authentication
API_KEY = os.getenv("EARENDIL_API_KEY", "earendil-dev-key-2026")


def check_api_key(request: Request) -> bool:
    """Verify X-API-Key header"""
    key = request.headers.get("x-api-key")
    return key == API_KEY


def plan_task(message: str) -> dict:
    """
    Simple planner: convert natural language request to structured task.
    Later: swap this for Claude/Sauron without breaking API.
    """
    msg = message.lower().strip()

    if "system status" in msg:
        return {"workflow": [
            {"command": "uptime"},
            {"command": "df -h"},
            {"command": "free -m"}
        ]}

    if any(x in msg for x in ["whoami", "who am i", "current user"]):
        return {"command": "whoami"}

    if any(x in msg for x in ["list files", "ls", "list directory", "show files"]):
        return {"command": "ls -la"}

    if any(x in msg for x in ["pwd", "current directory", "where am i"]):
        return {"command": "pwd"}

    if any(x in msg for x in ["date", "time", "what time", "current time"]):
        return {"command": "date"}

    # Fallback: treat message as literal command
    return {"command": message}



def normalize_task(plan_output: dict):
    """
    Adapter: convert planner output to execution-ready task(s).
    Planner is flexible, execution is strict.
    """
    if "command" in plan_output:
        return {
            "type": "system",
            "action": "run_command",
            "payload": {"command": plan_output["command"]}
        }

    if "workflow" in plan_output:
        return [
            {
                "type": "system",
                "action": "run_command",
                "payload": {"command": step["command"]}
            }
            for step in plan_output["workflow"]
        ]

    raise ValueError("Invalid plan output")

# Redis connection
r = redis.Redis(host="localhost", port=6379, decode_responses=True)


def enqueue_task(task, task_id=None):
    """Push task to queue with tracking ID and initial status"""
    if not task_id:
        task_id = str(uuid.uuid4())

    task["task_id"] = task_id

    # Mark task as queued in Redis
    r.set(f"task:{task_id}", json.dumps({
        "status": "queued",
        "result": None,
        "error": None
    }), ex=300)

    r.rpush("task_queue", json.dumps(task))
    return task_id


def dequeue_task():
    """Pop task from queue"""
    item = r.lpop("task_queue")
    return json.loads(item) if item else None



def wait_for_tasks(task_ids, timeout=15):
    """Poll Redis until all tasks complete or timeout. Returns partial if incomplete."""
    start = time.time()
    time.sleep(0.5)  # fast-path: let worker pick up tasks

    while True:
        results = []
        all_done = True

        for task_id in task_ids:
            raw = r.get(f"task:{task_id}")
            if not raw:
                all_done = False
                continue
            data = json.loads(raw)
            result_data = data.get("result")
            results.append({
                "task_id": task_id,
                "status": data["status"],
                "output": result_data.get("result") if isinstance(result_data, dict) else result_data,
                "error": data.get("error")
            })
            if data["status"] not in ["completed", "failed"]:
                all_done = False

        if all_done and len(results) == len(task_ids):
            return {"status": "completed", "results": results}

        if time.time() - start > timeout:
            return {"status": "partial", "results": results}

        time.sleep(0.5)

# --- Models ---

class Task(BaseModel):
    type: str       # "system", "memory", "workflow"
    action: str     # "run_command", "read_redis", "delegate_to_sauron"
    payload: dict


class PlanRequest(BaseModel):
    message: str


class QueryRequest(BaseModel):
    type: str                       # "redis", "system"
    key: Optional[str] = None       # for redis key lookup
    action: Optional[str] = None    # "list_keys", "queue_length", "status"
    pattern: Optional[str] = None   # for redis key pattern matching


# --- Endpoints ---

@app.post("/plan")
def handle_plan(plan_req: PlanRequest, request: Request):
    """Convert natural language request to structured task"""
    if not check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        task = plan_task(plan_req.message)
        return {"message": plan_req.message, "task": task}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)



@app.post("/execute")
def handle_execute(plan_req: PlanRequest, request: Request):
    """Plan + normalize + enqueue in one step"""
    if not check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        plan_output = plan_task(plan_req.message)
        normalized = normalize_task(plan_output)

        if isinstance(normalized, list):
            task_ids = [enqueue_task(t) for t in normalized]
            return {
                "status": "queued",
                "message": plan_req.message,
                "task_ids": task_ids,
                "executor": "earendil_worker"
            }

        task_id = enqueue_task(normalized)
        return {
            "status": "queued",
            "message": plan_req.message,
            "task_id": task_id,
            "executor": "earendil_worker"
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/execute/result")
def execute_result(req: dict, request: Request):
    """Aggregate results for multiple task_ids"""
    if not check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    task_ids = req.get("tasks", [])
    results = []

    for task_id in task_ids:
        raw = r.get(f"task:{task_id}")
        if not raw:
            results.append({"task_id": task_id, "status": "not_found"})
            continue
        data = json.loads(raw)
        results.append({
            "task_id": task_id,
            "status": data["status"],
            "result": data.get("result"),
            "error": data.get("error")
        })

    if all(x["status"] == "completed" for x in results):
        overall = "completed"
    elif any(x["status"] == "failed" for x in results):
        overall = "failed"
    else:
        overall = "running"

    return {"status": overall, "results": results}


@app.post("/execute/wait")
def execute_wait(req: dict, request: Request):
    """Plan + execute + wait for all results"""
    if not check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        message = req["message"]
        plan_output = plan_task(message)
        normalized = normalize_task(plan_output)

        if isinstance(normalized, list):
            task_ids = [enqueue_task(t) for t in normalized]
        else:
            task_ids = [enqueue_task(normalized)]

        result = wait_for_tasks(task_ids)
        result["message"] = message
        result["tasks"] = task_ids
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
@app.post("/task")
def handle_task(task: Task, request: Request):
    """Route structured tasks to appropriate executor"""
    if not check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        if task.type == "system" and task.action == "run_command":
            task_id = enqueue_task({
                "type": "system",
                "action": "run_command",
                "payload": task.payload
            })
            return {"status": "queued", "task_id": task_id, "executor": "earendil_worker"}

        elif task.type == "memory":
            if task.action == "set":
                key = task.payload.get("key")
                value = task.payload.get("value")
                r.set(key, value)
                return {"status": "success", "message": "stored"}
            elif task.action == "get":
                key = task.payload.get("key")
                value = r.get(key)
                return {"status": "success", "value": value}

        elif task.type == "async":
            enqueue_task(task.payload)
            return {"status": "queued", "queue": "task_queue"}

        return {"status": "error", "error": "unsupported task type/action"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/query")
def handle_query(query: QueryRequest, request: Request):
    """
    Read-only system state inspection.
    NO mutations. NO side effects. READ ONLY.

    Supported queries:
      {"type": "redis", "key": "task:<uuid>"}           - lookup specific key
      {"type": "redis", "action": "list_keys", "pattern": "task:*"}  - list matching keys
      {"type": "redis", "action": "queue_length"}       - current queue depth
      {"type": "redis", "action": "dbsize"}             - total keys in Redis
      {"type": "system", "action": "status"}            - system health summary
    """
    if not check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        # --- Redis key lookup ---
        if query.type == "redis" and query.key:
            value = r.get(query.key)
            if value:
                # Try to parse as JSON, fall back to raw string
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    pass
            return {
                "type": "redis_lookup",
                "key": query.key,
                "exists": value is not None,
                "value": value
            }

        # --- Redis list keys ---
        if query.type == "redis" and query.action == "list_keys":
            pattern = query.pattern or "task:*"
            # SCAN instead of KEYS for production safety
            keys = []
            cursor = 0
            while True:
                cursor, batch = r.scan(cursor=cursor, match=pattern, count=100)
                keys.extend(batch)
                if cursor == 0:
                    break
            # Cap at 100 keys to prevent huge responses
            keys = sorted(keys)[:100]
            return {
                "type": "redis_list",
                "pattern": pattern,
                "count": len(keys),
                "keys": keys
            }

        # --- Redis queue length ---
        if query.type == "redis" and query.action == "queue_length":
            length = r.llen("task_queue")
            return {
                "type": "queue_status",
                "queue": "task_queue",
                "length": length
            }

        # --- Redis dbsize ---
        if query.type == "redis" and query.action == "dbsize":
            size = r.dbsize()
            return {
                "type": "redis_info",
                "total_keys": size
            }

        # --- System status ---
        if query.type == "system" and query.action == "status":
            # Check Redis connectivity
            redis_ok = False
            try:
                r.ping()
                redis_ok = True
            except Exception:
                pass

            # Check worker service status
            worker_status = "unknown"
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", "earendil-worker"],
                    capture_output=True, text=True, timeout=5
                )
                worker_status = result.stdout.strip()
            except Exception:
                worker_status = "check_failed"

            # Check OpenClaw gateway status
            gateway_status = "unknown"
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", "openclaw-gateway"],
                    capture_output=True, text=True, timeout=5
                )
                gateway_status = result.stdout.strip()
            except Exception:
                gateway_status = "check_failed"

            # Queue depth
            queue_depth = r.llen("task_queue")

            # Total tracked tasks
            task_keys = []
            cursor = 0
            while True:
                cursor, batch = r.scan(cursor=cursor, match="task:*", count=100)
                task_keys.extend(batch)
                if cursor == 0:
                    break

            return {
                "type": "system_status",
                "api": "online",
                "redis": "connected" if redis_ok else "disconnected",
                "worker": worker_status,
                "openclaw_gateway": gateway_status,
                "queue_depth": queue_depth,
                "tracked_tasks": len(task_keys)
            }

        return JSONResponse(
            {"error": "unsupported query type/action", "hint": "supported: redis (key, list_keys, queue_length, dbsize), system (status)"},
            status_code=400
        )

    except Exception as e:
        return JSONResponse(
            {"error": str(e), "type": "query_error"},
            status_code=500
        )


@app.get("/result/{task_id}")
def get_result(task_id: str, request: Request):
    """Retrieve task status and result from Redis"""
    if not check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    task_state = r.get(f"task:{task_id}")

    if task_state:
        state = json.loads(task_state)
        return {
            "task_id": task_id,
            "status": state.get("status"),
            "result": state.get("result"),
            "error": state.get("error")
        }

    return {"task_id": task_id, "status": "not_found", "result": None, "error": None}


@app.get("/health")
def health_check():
    """Simple health endpoint - no auth required"""
    return {"status": "online", "agent": "earendil", "version": "0.2.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="100.112.3.116", port=5000)
