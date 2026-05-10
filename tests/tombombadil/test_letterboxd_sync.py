from __future__ import annotations

import json

import fakeredis
import pytest

from agents.tombombadil import delivery, sync_job
from agents.tombombadil.letterboxd_rss import parse_feed_text

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:letterboxd="https://letterboxd.com">
  <channel>
    <title>Solomon's Letterboxd</title>
    <link>https://letterboxd.com/SolomonThaChef/</link>
    <description>Films watched</description>

    <item>
      <title>Stalker, 1979 - ★★★★★</title>
      <link>https://letterboxd.com/SolomonThaChef/film/stalker/</link>
      <pubDate>Fri, 09 May 2026 22:00:00 +0000</pubDate>
      <letterboxd:filmTitle>Stalker</letterboxd:filmTitle>
      <letterboxd:filmYear>1979</letterboxd:filmYear>
      <letterboxd:memberRating>5.0</letterboxd:memberRating>
      <letterboxd:watchedDate>2026-05-09</letterboxd:watchedDate>
      <letterboxd:rewatch>No</letterboxd:rewatch>
    </item>

    <item>
      <title>Solaris, 1972 - ★★★★½</title>
      <link>https://letterboxd.com/SolomonThaChef/film/solaris/</link>
      <pubDate>Sat, 10 May 2026 03:30:00 +0000</pubDate>
      <letterboxd:filmTitle>Solaris</letterboxd:filmTitle>
      <letterboxd:filmYear>1972</letterboxd:filmYear>
      <letterboxd:memberRating>4.5</letterboxd:memberRating>
      <letterboxd:watchedDate>2026-05-10</letterboxd:watchedDate>
      <letterboxd:rewatch>No</letterboxd:rewatch>
    </item>

    <item>
      <title>Notebook entry without rating</title>
      <link>https://letterboxd.com/SolomonThaChef/film/something/</link>
      <pubDate>Sat, 10 May 2026 04:00:00 +0000</pubDate>
      <letterboxd:filmTitle>Something</letterboxd:filmTitle>
      <letterboxd:filmYear>2024</letterboxd:filmYear>
      <letterboxd:watchedDate>2026-05-10</letterboxd:watchedDate>
      <letterboxd:rewatch>No</letterboxd:rewatch>
    </item>
  </channel>
</rss>"""


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


# ----- parser ---------------------------------------------------------

def test_parse_feed_returns_entries():
    entries = parse_feed_text(SAMPLE_FEED)
    assert len(entries) == 3
    titles = [e.title for e in entries]
    assert "Stalker" in titles
    assert "Solaris" in titles


def test_parse_doubles_letterboxd_rating_to_ten_scale():
    entries = parse_feed_text(SAMPLE_FEED)
    by_title = {e.title: e for e in entries}
    assert by_title["Stalker"].rating == 10.0
    assert by_title["Solaris"].rating == 9.0
    # The "Something" entry has no rating in the RSS.
    assert by_title["Something"].rating is None


def test_parse_tolerates_missing_year():
    bare = """<?xml version="1.0"?><rss version="2.0" xmlns:letterboxd="https://letterboxd.com"><channel>
        <item><title>Film X</title><letterboxd:filmTitle>X</letterboxd:filmTitle>
        <letterboxd:watchedDate>2026-01-01</letterboxd:watchedDate></item>
    </channel></rss>"""
    entries = parse_feed_text(bare)
    assert len(entries) == 1
    assert entries[0].title == "X"
    assert entries[0].year is None
    assert entries[0].rating is None


# ----- run_sync -------------------------------------------------------

def test_run_sync_saves_new_entries_and_advances_watermark(r):
    result = sync_job.run_sync(
        r,
        username="SolomonThaChef",
        viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED,
    )
    assert result.fetched == 3
    assert result.new == 3  # all unseen; "Something" is in new but skipped at save (no rating)
    assert result.saved == 2  # Stalker + Solaris
    assert r.sismember("films", "Stalker")
    assert r.sismember("films", "Solaris")
    assert r.sismember("watchers", "Solomon Smith")
    assert r.get(sync_job.WATERMARK_KEY) == "2026-05-10"


def test_run_sync_skips_entries_at_or_before_watermark(r):
    r.set(sync_job.WATERMARK_KEY, "2026-05-09")
    result = sync_job.run_sync(
        r,
        username="SolomonThaChef",
        viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED,
    )
    # Stalker watched on 2026-05-09 is <= watermark -> skipped.
    assert result.fetched == 3
    assert result.new == 2  # Solaris + Something
    assert result.saved == 1  # Solaris saved; Something has no rating
    assert not r.sismember("films", "Stalker")


def test_run_sync_announces_when_channel_configured(r):
    sync_job.run_sync(
        r,
        username="SolomonThaChef",
        viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED,
        announce_channel_id="1234567890",
    )
    raw = r.lrange(delivery.QUEUE_KEY, 0, -1)
    assert len(raw) == 2  # Stalker + Solaris (Something skipped, no rating)
    parsed = [json.loads(p) for p in raw]
    texts = [p["text"] for p in parsed]
    assert any("Stalker" in t for t in texts)
    assert any("Solaris" in t for t in texts)
    assert all(p["channel_id"] == "1234567890" for p in parsed)


def test_run_sync_no_announce_when_channel_missing(r):
    sync_job.run_sync(
        r,
        username="SolomonThaChef",
        viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED,
    )
    assert r.llen(delivery.QUEUE_KEY) == 0


def test_run_sync_no_username_no_feed_returns_error(r):
    result = sync_job.run_sync(r, username="", feed_text=None)
    assert result.errors
    assert "LETTERBOXD_USERNAME" in result.errors[0]


def test_run_sync_idempotent_on_second_run(r):
    sync_job.run_sync(r, username="SolomonThaChef", viewer_name="Solomon Smith", feed_text=SAMPLE_FEED)
    # Second run with same feed -> watermark filters everything.
    second = sync_job.run_sync(r, username="SolomonThaChef", viewer_name="Solomon Smith", feed_text=SAMPLE_FEED)
    assert second.new == 0
    assert second.saved == 0


# ----- cron job -------------------------------------------------------

def test_ensure_letterboxd_sync_cron_idempotent(r):
    j1 = sync_job.ensure_letterboxd_sync_cron(r)
    j2 = sync_job.ensure_letterboxd_sync_cron(r)
    assert j1.id == j2.id == sync_job.LETTERBOXD_SYNC_JOB_ID
    assert j1.schedule.kind == "cron"
    assert j1.payload.kind == "systemEvent"
    assert j1.payload.text == "letterboxd_sync"


def test_ensure_letterboxd_sync_cron_custom_expr(r):
    job = sync_job.ensure_letterboxd_sync_cron(r, cron_expr="30 5 * * *", tz="UTC")
    assert job.schedule.expr == "30 5 * * *"
    assert job.schedule.tz == "UTC"
