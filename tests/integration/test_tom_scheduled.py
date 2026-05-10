"""Spec 4.3: Tom Bombadil scheduled flows."""

from __future__ import annotations

import json

from agents.galadriel.models import Job, JobDelivery, JobPayload, JobSchedule
from agents.galadriel.worker import announce
from agents.tombombadil import club, delivery


def _job(channel_id: str = "42", film: str = "Inception") -> Job:
    return Job(
        id="x",
        name=f"Watch party: {film}",
        schedule=JobSchedule(kind="at", at_iso="2099-01-01T19:00:00"),
        payload=JobPayload(kind="agentTurn", message=f"Announce: {film} tonight"),
        delivery=JobDelivery(mode="discord", to=channel_id),
        created_at_ms=0,
        updated_at_ms=0,
    )


def test_delivery_publish_enqueues_json(fake_redis):
    """Spec 4.3.1 delivery: publish writes a {channel_id, text} JSON
    payload to tom:announce:queue."""
    delivery.publish("42", "club night tonight", redis=fake_redis)
    raw = fake_redis.lpop(delivery.QUEUE_KEY)
    payload = json.loads(raw)
    assert payload == {"channel_id": "42", "text": "club night tonight"}


def test_galadriel_discord_mode_pushes_to_queue(monkeypatch, fake_redis):
    """Spec 4.3.1: Galadriel's announce() dispatches mode='discord' to
    delivery.publish, which queues the message for the subscriber."""
    monkeypatch.setattr(delivery, "get_redis_sync", lambda: fake_redis)
    job = _job()
    result = {"result": {"reply": "Club night tonight, watching Inception"}}
    announce(job, result)
    raw = fake_redis.lpop(delivery.QUEUE_KEY)
    payload = json.loads(raw)
    assert payload["channel_id"] == "42"
    assert "Inception" in payload["text"]


def test_schedule_watch_party_writes_galadriel_job(fake_redis):
    """Spec 4.3.1: schedule_watch_party persists a cron:job:* entry
    keyed by a watch_party_<hex> id with delivery.mode='discord'."""
    job = club.schedule_watch_party(
        fake_redis,
        film="Inception",
        when_iso="2099-01-01T19:00:00",
        channel_id="42",
        organizer="Solomon Smith",
    )
    assert job.id.startswith("watch_party_")
    assert job.delivery.mode == "discord"
    assert job.delivery.to == "42"
    # The saved blob is retrievable.
    assert fake_redis.exists(f"cron:job:{job.id}")


def test_ensure_weekly_club_night_is_idempotent(fake_redis):
    """Spec 4.3.1: ensure_weekly_club_night uses a fixed id; second call
    overwrites the first instead of creating a duplicate."""
    j1 = club.ensure_weekly_club_night(fake_redis, channel_id="42")
    j2 = club.ensure_weekly_club_night(fake_redis, channel_id="42")
    assert j1.id == j2.id == club.WEEKLY_NIGHT_JOB_ID
