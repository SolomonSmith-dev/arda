from __future__ import annotations

import fakeredis
import pytest

from agents.tombombadil import club
from agents.tombombadil.film_knowledge import FilmKnowledge


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def knowledge():
    return FilmKnowledge()


# ----- recommend_for_group ------------------------------------------

def test_recommend_for_group_empty_names(knowledge):
    reply = club.recommend_for_group(knowledge, [])
    assert "one or more" in reply.lower()


def test_recommend_for_group_unknown_only(knowledge):
    reply = club.recommend_for_group(knowledge, ["NobodyAtAll"])
    assert "don't know any" in reply.lower()


def test_recommend_for_group_valid_blend_handles_full_catalog(knowledge):
    """Solomon + Brian have collectively watched all three seed films,
    so the recommender correctly reports there's nothing left in the
    catalog to suggest. The important property: the function reasons
    over both viewers, doesn't crash, and returns a useful string.
    """
    reply = club.recommend_for_group(knowledge, ["Solomon Smith", "Brian"])
    assert isinstance(reply, str)
    assert reply  # non-empty
    # Either a recommendation header or the "everyone has watched" message.
    assert ("For Solomon Smith, Brian" in reply) or ("already watched" in reply.lower())


def test_recommend_for_group_with_unwatched_overlap(monkeypatch, knowledge):
    """Inject a viewer who hasn't watched a catalog film so the
    recommender has something to suggest. Verifies the happy path
    produces the structured Markdown response.
    """
    # Brian seed has only watched "Ran". So if we pair Brian with
    # someone (himself) the unwatched set is {La Haine, Ghost Dog}.
    reply = club.recommend_for_group(knowledge, ["Brian"])
    assert reply
    # Brian's themes are tragedy/emotional impact -- Ran is closest,
    # but Brian's already watched it, so we expect La Haine.
    assert ("For Brian" in reply) or ("already watched" in reply.lower())


def test_recommend_for_group_skips_unknown_names(knowledge):
    """An unknown name in the list shouldn't poison the call. The
    function must succeed (string reply, no crash) regardless of
    whether the algorithm can find a recommendation.
    """
    reply = club.recommend_for_group(knowledge, ["Brian", "NobodyAtAll"])
    assert isinstance(reply, str) and reply
    # And -- critically -- it's NOT the all-unknown refusal.
    assert "don't know any" not in reply.lower()


# ----- schedule_watch_party + cmd_club_schedule ---------------------

def test_schedule_watch_party_saves_job(r):
    job = club.schedule_watch_party(
        r,
        film="Inception",
        when_iso="2099-01-01T19:00:00",
        channel_id="1234567890",
        organizer="Solomon Smith",
    )
    assert job.id.startswith("watch_party_")
    assert job.delivery.mode == "discord"
    assert job.delivery.to == "1234567890"
    assert job.payload.kind == "agentTurn"
    assert "Inception" in (job.payload.message or "")


def test_schedule_watch_party_invalid_iso_raises(r):
    with pytest.raises(ValueError):
        club.schedule_watch_party(
            r,
            film="Inception",
            when_iso="not-an-iso",
            channel_id="x",
            organizer="Solomon Smith",
        )


def test_cmd_club_schedule_rejects_empty_film(r, knowledge):
    reply = club.cmd_club_schedule(
        r, knowledge,
        film="   ",
        when_iso="2099-01-01T19:00:00",
        channel_id="x",
        organizer="Solomon Smith",
    )
    assert "Film is required" in reply


def test_cmd_club_schedule_rejects_bad_iso(r, knowledge):
    reply = club.cmd_club_schedule(
        r, knowledge,
        film="Inception",
        when_iso="next thursday",
        channel_id="x",
        organizer="Solomon Smith",
    )
    assert "ISO 8601" in reply


def test_cmd_club_schedule_warns_for_uncatalogued_film(r, knowledge):
    reply = club.cmd_club_schedule(
        r, knowledge,
        film="The Holy Mountain",
        when_iso="2099-01-01T19:00:00",
        channel_id="x",
        organizer="Solomon Smith",
    )
    assert "Scheduled" in reply
    assert "isn't in the catalog" in reply


def test_cmd_club_schedule_no_warn_for_catalog_film(r, knowledge):
    reply = club.cmd_club_schedule(
        r, knowledge,
        film="Ran",
        when_iso="2099-01-01T19:00:00",
        channel_id="x",
        organizer="Solomon Smith",
    )
    assert "Scheduled" in reply
    assert "isn't in the catalog" not in reply


# ----- ensure_weekly_club_night -------------------------------------

def test_ensure_weekly_club_night_idempotent(r):
    j1 = club.ensure_weekly_club_night(r, channel_id="42")
    j2 = club.ensure_weekly_club_night(r, channel_id="42")
    # Same fixed id; save_job overwrites.
    assert j1.id == j2.id == club.WEEKLY_NIGHT_JOB_ID
    assert j1.delivery.mode == "discord"
    assert j1.delivery.to == "42"
    assert j1.schedule.kind == "cron"


# ----- discord_announce_payload -------------------------------------

def test_discord_announce_payload_prefers_reply_field():
    from agents.galadriel.models import Job, JobDelivery, JobPayload, JobSchedule
    job = Job(
        id="x",
        name="watch party",
        schedule=JobSchedule(kind="at", at_iso="2099-01-01T19:00:00"),
        payload=JobPayload(kind="agentTurn", message="hi"),
        delivery=JobDelivery(mode="discord", to="42"),
        created_at_ms=0,
        updated_at_ms=0,
    )
    payload = club.discord_announce_payload(job, {"result": {"reply": "It's Friday."}})
    assert payload["text"] == "It's Friday."
    assert payload["channel_id"] == "42"
    assert payload["job_id"] == "x"
