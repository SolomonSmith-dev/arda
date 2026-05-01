from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentTask(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    agent: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class AgentResult(BaseModel):
    task_id: str
    agent: str
    status: TaskStatus
    result: Any | None = None
    error: str | None = None
    duration_ms: int | None = None


class HealthStatus(BaseModel):
    agent: str
    status: str
    model: str
    provider: str
    latency_ms: int | None = None
