"""Cron job CRUD for the Galadriel scheduler.

Routes:
  - POST   /cron        create a job
  - GET    /cron        list all jobs
  - GET    /cron/{id}   read one job
  - DELETE /cron/{id}   delete a job
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agents.galadriel.models import (
    Job,
    JobDelivery,
    JobPayload,
    JobSchedule,
)
from agents.galadriel.scheduler import Schedule, next_run_ms
from agents.galadriel.store import delete_job, list_jobs, read_job, save_job
from api.middleware.auth import require_api_key
from core.redis_client import get_redis_sync

router = APIRouter(dependencies=[Depends(require_api_key)])


class CreateJobRequest(BaseModel):
    name: str
    schedule: JobSchedule
    payload: JobPayload
    delivery: JobDelivery = Field(default_factory=JobDelivery)
    delete_after_run: bool = False
    enabled: bool = True


def _now_ms() -> int:
    return int(time.time() * 1000)


def _compute_next_run(schedule: JobSchedule) -> int | None:
    return next_run_ms(
        Schedule(
            kind=schedule.kind,
            expr=schedule.expr,
            tz=schedule.tz,
            at_iso=schedule.at_iso,
        )
    )


@router.post("/cron", status_code=201)
def create_job(req: CreateJobRequest) -> dict:
    try:
        next_run = _compute_next_run(req.schedule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if req.schedule.kind == "at" and next_run is None:
        raise HTTPException(
            status_code=400,
            detail="schedule.at_iso must be in the future",
        )

    now = _now_ms()
    job = Job(
        id=str(uuid.uuid4()),
        name=req.name,
        schedule=req.schedule,
        payload=req.payload,
        delivery=req.delivery,
        delete_after_run=req.delete_after_run,
        enabled=req.enabled,
        created_at_ms=now,
        updated_at_ms=now,
        next_run_at_ms=next_run,
    )
    save_job(get_redis_sync(), job)
    return job.model_dump(mode="json")


@router.get("/cron")
def list_all_jobs() -> dict:
    jobs = list_jobs(get_redis_sync())
    return {"jobs": [j.model_dump(mode="json") for j in jobs]}


@router.get("/cron/{job_id}")
def get_job(job_id: str) -> dict:
    job = read_job(get_redis_sync(), job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.model_dump(mode="json")


@router.delete("/cron/{job_id}", status_code=204)
def remove_job(job_id: str) -> None:
    if not delete_job(get_redis_sync(), job_id):
        raise HTTPException(status_code=404, detail="job not found")
