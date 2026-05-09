from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

API_VERSION = "0.3.0"


@router.get("/health")
def health() -> dict:
    return {"status": "online", "agent": "earendil", "version": API_VERSION}
