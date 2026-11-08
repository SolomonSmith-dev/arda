from __future__ import annotations

import json

import fakeredis
import pytest

from agents.tombombadil import delivery


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


def test_publish_enqueues_json(r):
    delivery.publish("1234", "hello, club", redis=r)
    raw = r.lpop(delivery.QUEUE_KEY)
    assert raw is not None
    payload = json.loads(raw)
    assert payload["channel_id"] == "1234"
    assert payload["text"] == "hello, club"


def test_publish_multiple_preserves_order(r):
    delivery.publish("c", "first", redis=r)
    delivery.publish("c", "second", redis=r)
    first = json.loads(r.lpop(delivery.QUEUE_KEY))
    second = json.loads(r.lpop(delivery.QUEUE_KEY))
    assert first["text"] == "first"
    assert second["text"] == "second"


def test_blpop_wait_is_shorter_than_the_socket_read_timeout():
    """redis-py 8 defaults socket_timeout=5. A 5s BLPOP on an empty queue then
    times out on the socket read before Redis answers, and the subscriber logs
    ``delivery_blpop_failed`` once a minute instead of ever posting. The shared
    async client must carry a read timeout with headroom over the BLPOP wait.
    """
    from core.redis_client import get_redis_async

    get_redis_async.cache_clear()
    try:
        kwargs = get_redis_async().connection_pool.connection_kwargs
    finally:
        get_redis_async.cache_clear()
    socket_timeout = kwargs.get("socket_timeout")
    assert socket_timeout is not None, "pin it; library defaults change across majors"
    assert delivery.BLPOP_TIMEOUT_SECONDS < socket_timeout
