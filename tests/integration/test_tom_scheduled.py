"""Spec 4.3: Tom Bombadil scheduled flows."""

from __future__ import annotations

import json

from agents.galadriel.models import Job, JobDelivery, JobPayload, JobSchedule
from agents.galadriel.worker import announce
from agents.tombombadil import club, delivery, sync_job


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


# ---------------------------------------------------------------------------
# Spec 4.3.2: Letterboxd auto-sync
# ---------------------------------------------------------------------------

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:letterboxd="https://letterboxd.com">
  <channel>
    <item>
      <title>Stalker, 1979 - ★★★★★</title>
      <letterboxd:filmTitle>Stalker</letterboxd:filmTitle>
      <letterboxd:filmYear>1979</letterboxd:filmYear>
      <letterboxd:memberRating>5.0</letterboxd:memberRating>
      <letterboxd:watchedDate>2026-05-09</letterboxd:watchedDate>
    </item>
    <item>
      <title>Solaris, 1972 - ★★★★½</title>
      <letterboxd:filmTitle>Solaris</letterboxd:filmTitle>
      <letterboxd:filmYear>1972</letterboxd:filmYear>
      <letterboxd:memberRating>4.5</letterboxd:memberRating>
      <letterboxd:watchedDate>2026-05-10</letterboxd:watchedDate>
    </item>
  </channel>
</rss>"""


def test_sync_saves_new_films_and_advances_watermark(fake_redis):
    """Spec 4.3.2 sync side effects: new diary entries become notes
    keyed under viewer_name; watermark advances to latest watchedDate."""
    result = sync_job.run_sync(
        fake_redis,
        username="SolomonThaChef",
        viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED,
    )
    assert result.fetched == 2 and result.saved == 2
    assert fake_redis.sismember("films", "Stalker")
    assert fake_redis.sismember("films", "Solaris")
    assert fake_redis.get(sync_job.WATERMARK_KEY) == "2026-05-10"


def test_sync_idempotent_on_second_run(fake_redis):
    """Spec 4.3.2: watermark filters previously-saved entries."""
    sync_job.run_sync(fake_redis, username="x", viewer_name="Solomon Smith", feed_text=SAMPLE_FEED)
    second = sync_job.run_sync(fake_redis, username="x", viewer_name="Solomon Smith", feed_text=SAMPLE_FEED)
    assert second.new == 0 and second.saved == 0


def test_sync_announces_when_channel_set(fake_redis):
    """Spec 4.3.2: TOM_LETTERBOXD_ANNOUNCE_CHANNEL_ID -> delivery
    payloads enqueued per saved film."""
    sync_job.run_sync(
        fake_redis, username="x", viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED, announce_channel_id="42",
    )
    raw = fake_redis.lrange(delivery.QUEUE_KEY, 0, -1)
    assert len(raw) == 2
    texts = [json.loads(p)["text"] for p in raw]
    assert any("Stalker" in t for t in texts)
    assert any("Solaris" in t for t in texts)


def test_sync_returns_error_without_username(fake_redis):
    """Spec 4.3.2: no LETTERBOXD_USERNAME -> errored SyncResult,
    no replies, no watermark change."""
    result = sync_job.run_sync(fake_redis, username="", feed_text=None)
    assert result.errors and "LETTERBOXD_USERNAME" in result.errors[0]
    assert fake_redis.get(sync_job.WATERMARK_KEY) is None
