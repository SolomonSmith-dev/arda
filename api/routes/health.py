from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter()

API_VERSION = "0.3.0"


@router.get("/health")
def health() -> dict:
    return {"status": "online", "agent": "earendil", "version": API_VERSION}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    """Prometheus scrape endpoint. Returns ``""`` when prometheus_client
    isn't installed so the route stays callable on slim installs.
    """
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # noqa: F401

        return generate_latest().decode("utf-8")
    except ImportError:
        return "# prometheus_client not installed\n"
