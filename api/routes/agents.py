from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from api.middleware.auth import require_api_key
from core.logging import get_logger
from core.models import AgentTask

log = get_logger("api.routes.agents")

router = APIRouter(dependencies=[Depends(require_api_key)])

AGENT_NAMES = ("sauron", "earendil", "finrod", "tombombadil")


def _get_agent(request: Request, name: str):
    if name not in AGENT_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown agent: {name}")
    agent = getattr(request.app.state, name, None)
    if agent is None:
        raise HTTPException(status_code=503, detail=f"agent '{name}' not registered")
    return agent


@router.post("/agents/{name}/run")
async def run_agent(name: str, body: dict, request: Request) -> dict:
    agent = _get_agent(request, name)
    body.setdefault("agent", name)
    body.setdefault("type", "direct")
    body.setdefault("payload", body.get("payload", {}))
    task = AgentTask(**body)
    result = await agent.run(task)
    return result.model_dump(mode="json")


@router.get("/agents/health")
async def agents_health(request: Request) -> dict:
    statuses = []
    for name in AGENT_NAMES:
        agent = getattr(request.app.state, name, None)
        if agent is None:
            statuses.append({"agent": name, "status": "offline", "model": "unknown", "provider": "unknown"})
            continue
        try:
            h = await agent.health()
            statuses.append(h.model_dump(mode="json"))
        except Exception as e:
            log.warning("agent_health_failed", agent=name, exception=str(e))
            statuses.append(
                {"agent": name, "status": "degraded", "model": "unknown", "provider": "unknown"}
            )
    return {"agents": statuses}
