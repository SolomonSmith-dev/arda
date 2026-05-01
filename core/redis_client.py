from __future__ import annotations

from functools import lru_cache

import redis
import redis.asyncio as redis_async

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
