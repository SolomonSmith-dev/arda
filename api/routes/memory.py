from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.middleware.auth import require_api_key
from core.models import AgentTask

router = APIRouter(dependencies=[Depends(require_api_key)])


class IngestRequest(BaseModel):
    doc_id: str
    text: str
    metadata: dict | None = None


class QueryRequest(BaseModel):
    message: str
    top_k: int = 5


@router.post("/memory/ingest")
async def memory_ingest(req: IngestRequest, request: Request) -> dict:
    finrod = getattr(request.app.state, "finrod", None)
    if finrod is None:
        raise HTTPException(status_code=503, detail="finrod not registered")

    task = AgentTask(
        agent="finrod",
        type="memory_ingest",
        payload={
            "action": "ingest",
            "doc_id": req.doc_id,
            "text": req.text,
            "metadata": req.metadata,
        },
    )
    result = await finrod.run(task)
    return result.model_dump(mode="json")


@router.post("/memory/query")
async def memory_query(req: QueryRequest, request: Request) -> dict:
    finrod = getattr(request.app.state, "finrod", None)
    if finrod is None:
        raise HTTPException(status_code=503, detail="finrod not registered")

    task = AgentTask(
        agent="finrod",
        type="memory_query",
        payload={"action": "query", "message": req.message, "top_k": req.top_k},
    )
    result = await finrod.run(task)
    return result.model_dump(mode="json")
