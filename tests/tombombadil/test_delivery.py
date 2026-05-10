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
