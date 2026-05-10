"""ARDA MCP server.

Exposes the unified ARDA API as native Claude tools over stdio.

For sub-pass 2 the API URL still points at the legacy Earendil host
(Mac Mini, port 5000). Phase 3 swaps this to the unified api/main.py
on whichever host hosts ARDA -- the MCP tool surface stays the same.
"""

from __future__ import annotations

import json
import time

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from core.config import settings

HEADERS = {
    "Content-Type": "application/json",
    "x-api-key": settings.arda_api_key,
}

client = httpx.Client(base_url=settings.earendil_host, headers=HEADERS, timeout=30.0)

mcp = FastMCP("arda_mcp")


class ExecuteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    command: str = Field(..., min_length=1, max_length=2000)
    poll: bool = Field(default=True)
    poll_timeout: int = Field(default=10, ge=1, le=60)


class QueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    type: str
    key: str | None = None
    action: str | None = None
    pattern: str | None = None


class PlanInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    message: str = Field(..., min_length=1, max_length=500)


@mcp.tool(
    name="arda_execute",
    annotations={
        "title": "Execute command via ARDA",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def arda_execute(params: ExecuteInput) -> str:
    try:
        resp = client.post(
            "/task",
            json={
                "type": "system",
                "action": "run_command",
                "payload": {"command": params.command},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        task_id = data.get("task_id")

        if not task_id:
            return json.dumps({"error": "No task_id returned", "response": data})

        if not params.poll:
            return json.dumps({"status": "queued", "task_id": task_id})

        deadline = time.time() + params.poll_timeout
        while time.time() < deadline:
            r = client.get(f"/result/{task_id}")
            r.raise_for_status()
            result = r.json()
            status = result.get("status")

            if status == "completed":
                return json.dumps(
                    {"status": "completed", "task_id": task_id, "result": result.get("result")}
                )
            if status == "failed":
                return json.dumps(
                    {"status": "failed", "task_id": task_id, "error": result.get("error")}
                )
            time.sleep(0.5)

        return json.dumps(
            {
                "status": "timeout",
                "task_id": task_id,
                "message": f"Result not ready after {params.poll_timeout}s.",
            }
        )

    except httpx.ConnectError:
        return json.dumps({"error": "Cannot reach ARDA API."})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    name="arda_query",
    annotations={
        "title": "Query ARDA system state",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def arda_query(params: QueryInput) -> str:
    try:
        payload: dict = {"type": params.type}
        if params.key:
            payload["key"] = params.key
        if params.action:
            payload["action"] = params.action
        if params.pattern:
            payload["pattern"] = params.pattern

        resp = client.post("/query", json=payload)
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)

    except httpx.ConnectError:
        return json.dumps({"error": "Cannot reach ARDA API."})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    name="arda_plan",
    annotations={
        "title": "Plan a task from natural language",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def arda_plan(params: PlanInput) -> str:
    try:
        resp = client.post("/plan", json={"message": params.message})
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)

    except httpx.ConnectError:
        return json.dumps({"error": "Cannot reach ARDA API."})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    name="arda_status",
    annotations={
        "title": "ARDA system health check",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def arda_status() -> str:
    try:
        health = client.get("/health")
        health.raise_for_status()

        resp = client.post("/query", json={"type": "system", "action": "status"})
        resp.raise_for_status()
        data = resp.json()
        data["api_version"] = health.json().get("version", "unknown")
        return json.dumps(data, indent=2)

    except httpx.ConnectError:
        return json.dumps({"api": "unreachable", "error": "Cannot reach ARDA API."})
    except Exception as e:
        return json.dumps({"error": str(e)})


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
