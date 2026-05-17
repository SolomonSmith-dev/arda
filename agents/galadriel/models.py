"""Pydantic models for cron jobs.

Stored as a single JSON blob per job at ``cron:job:<id>`` plus a sorted-set
``cron:queue`` keyed by ``next_run_at_ms``. See ``store.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ScheduleKind = Literal["cron", "at"]
PayloadKind = Literal["agentTurn", "systemEvent"]
DeliveryMode = Literal["announce", "telegram", "discord", "none"]


class JobSchedule(BaseModel):
    kind: ScheduleKind
    expr: str | None = None
    tz: str = "UTC"
    at_iso: str | None = None


class JobPayload(BaseModel):
    kind: PayloadKind
    message: str | None = None
    text: str | None = None
    timeout_seconds: int = 60


class JobDelivery(BaseModel):
    mode: DeliveryMode = "none"
    to: str | None = None


class Job(BaseModel):
    id: str
    name: str
    schedule: JobSchedule
    payload: JobPayload
    delivery: JobDelivery = Field(default_factory=JobDelivery)
    delete_after_run: bool = False
    enabled: bool = True

    created_at_ms: int
    updated_at_ms: int
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: str | None = None
    last_duration_ms: int | None = None
    last_error: str | None = None
    consecutive_errors: int = 0
