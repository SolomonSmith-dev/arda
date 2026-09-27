from __future__ import annotations

from functools import lru_cache

import redis
import redis.asyncio as redis_async
from redis.backoff import NoBackoff
from redis.retry import Retry

from core.config import settings

TASK_QUEUE_KEY = "task_queue"
RESULT_TTL_SECONDS = 300


def task_result_key(task_id: str) -> str:
    return f"task:{task_id}"


@lru_cache(maxsize=1)
def get_redis_sync() -> redis.Redis:
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True,
    )


@lru_cache(maxsize=1)
def get_redis_async() -> redis_async.Redis:
    return redis_async.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True,
    )


def redis_reachable(timeout: float = 1.0) -> bool:
    """One no-retry ping, for startup checks.

    The shared clients keep redis-py's default retry with backoff, which is
    right for a transient blip in production but makes one ``ping`` against a
    dead server take ~7s. A probe should answer in ``timeout`` or say no.
    """
    probe = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
        retry=Retry(NoBackoff(), 0),
    )
    try:
        return bool(probe.ping())
    except redis.RedisError:
        return False
    finally:
        probe.close()
