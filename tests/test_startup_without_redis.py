"""#90: the API must not stall for ~25s at startup when Redis is down.

redis-py 7 retries every command with exponential backoff, so one ``ping``
against a closed port takes ~7s and the Letterboxd cron seed that follows
takes ~20s more. The startup probe disables retries, and the seed is skipped
when the probe fails.
"""

from __future__ import annotations

import socket
import time

import fakeredis
from fastapi.testclient import TestClient

from api import main as api_main
from core import redis_client
from core.config import settings


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_redis_reachable_fails_fast_on_a_closed_port(monkeypatch):
    monkeypatch.setattr(settings, "redis_host", "127.0.0.1")
    monkeypatch.setattr(settings, "redis_port", _closed_port())
    start = time.monotonic()
    assert redis_client.redis_reachable() is False
    assert time.monotonic() - start < 2.0


def test_lifespan_skips_the_cron_seed_when_redis_is_down(monkeypatch):
    seeded: list[object] = []
    monkeypatch.setattr(api_main, "redis_reachable", lambda: False)
    monkeypatch.setattr(
        "agents.tombombadil.sync_job.ensure_letterboxd_sync_cron",
        lambda r, **_k: seeded.append(r),
    )
    start = time.monotonic()
    with TestClient(api_main.create_app()) as c:
        assert c.get("/health").status_code == 200
    assert seeded == []
    assert time.monotonic() - start < 5.0


def test_lifespan_seeds_the_cron_when_redis_is_up(monkeypatch):
    seeded: list[object] = []
    monkeypatch.setattr(api_main, "redis_reachable", lambda: True)
    # Every seed must hit a live store; the real client would retry for
    # seconds against the closed default port.
    monkeypatch.setattr(api_main, "get_redis_sync", lambda: fakeredis.FakeRedis(decode_responses=True))
    monkeypatch.setattr(
        "agents.tombombadil.sync_job.ensure_letterboxd_sync_cron",
        lambda r, **_k: seeded.append(r),
    )
    with TestClient(api_main.create_app()):
        pass
    assert len(seeded) == 1
