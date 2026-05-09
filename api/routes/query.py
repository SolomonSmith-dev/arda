from __future__ import annotations

import json
import os
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.middleware.auth import require_api_key
from core.redis_client import TASK_QUEUE_KEY, get_redis_sync

router = APIRouter(dependencies=[Depends(require_api_key)])

WORKER_UNIT = "earendil-worker"
GATEWAY_UNIT = "openclaw-gateway"


class QueryBody(BaseModel):
    type: str
    key: str | None = None
    action: str | None = None
    pattern: str | None = None


def _running_in_docker() -> bool:
    return os.path.exists("/.dockerenv")


def _systemctl_status(unit: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "check_failed"


@router.post("/query")
def handle_query(body: QueryBody) -> dict:
    """Read-only Redis / system inspection. Mirrors legacy contract."""
    r = get_redis_sync()

    try:
        if body.type == "redis" and body.key:
            value = r.get(body.key)
            if value:
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    pass
            return {
                "type": "redis_lookup",
                "key": body.key,
                "exists": value is not None,
                "value": value,
            }

        if body.type == "redis" and body.action == "list_keys":
            pattern = body.pattern or "task:*"
            keys: list[str] = []
            cursor = 0
            while True:
                cursor, batch = r.scan(cursor=cursor, match=pattern, count=100)
                keys.extend(batch)
                if cursor == 0:
                    break
            keys = sorted(keys)[:100]
            return {
                "type": "redis_list",
                "pattern": pattern,
                "count": len(keys),
                "keys": keys,
            }

        if body.type == "redis" and body.action == "queue_length":
            return {
                "type": "queue_status",
                "queue": TASK_QUEUE_KEY,
                "length": r.llen(TASK_QUEUE_KEY),
            }

        if body.type == "redis" and body.action == "dbsize":
            return {"type": "redis_info", "total_keys": r.dbsize()}

        if body.type == "system" and body.action == "status":
            redis_ok = False
            try:
                r.ping()
                redis_ok = True
            except Exception:
                pass

            if _running_in_docker():
                worker_status = "containerized"
                gateway_status = "n/a"
            else:
                worker_status = _systemctl_status(WORKER_UNIT)
                gateway_status = _systemctl_status(GATEWAY_UNIT)

            queue_depth = r.llen(TASK_QUEUE_KEY) if redis_ok else 0
            tracked = 0
            if redis_ok:
                cursor = 0
                while True:
                    cursor, batch = r.scan(cursor=cursor, match="task:*", count=100)
                    tracked += len(batch)
                    if cursor == 0:
                        break

            return {
                "type": "system_status",
                "api": "online",
                "redis": "connected" if redis_ok else "disconnected",
                "worker": worker_status,
                "openclaw_gateway": gateway_status,
                "queue_depth": queue_depth,
                "tracked_tasks": tracked,
            }

        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported query type/action",
                "hint": "supported: redis (key, list_keys, queue_length, dbsize), system (status)",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e), "type": "query_error"})
