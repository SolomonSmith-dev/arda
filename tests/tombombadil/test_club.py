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
    # This outcome is determinate, so assert it exactly. The old form was
    # hedged with `or "For Solomon Smith, Brian" in reply`, which would have
    # passed on either branch and told us nothing about which one ran.
    assert "already watched the catalog's overlap" in reply


def test_recommend_for_group_single_viewer_exhausts_the_seed_catalogue(knowledge):
    """A single seed viewer also lands on the "nothing left" branch.

    This test used to be called ``..._with_unwatched_overlap`` and claimed
    "we expect La Haine", but it never reached the recommendation branch --
    the `or ("already watched" in reply.lower())` hedge absorbed the real
    outcome, so the name and docstring described behaviour it never
    exercised. The recommendation branch is covered by
    ``test_recommend_for_group_renders_themes_without_crashing``, which
    builds a catalogue that actually has something unwatched to suggest.
    """
    reply = club.recommend_for_group(knowledge, ["Brian"])
    assert "already watched the catalog's overlap" in reply


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


def test_cmd_club_schedule_rejects_missing_channel(r, knowledge):
    """discord.Interaction.channel_id is Optional. Scheduling with None used
    to stringify to "None", report success, and then never deliver."""
    reply = club.cmd_club_schedule(
        r,
        knowledge,
        film="Inception",
        when_iso="2099-01-01T19:00:00",
        channel_id=None,
        organizer="Solomon Smith",
    )
    assert "can't schedule" in reply
    assert "job" not in reply
    assert not [k for k in r.keys("cron:job:watch_party_*")]


def test_recommend_for_group_renders_themes_without_crashing():
    """Regression: the themes line did ``sorted(set(...)[:4])``, slicing a
    set. Any call that actually reached a recommendation raised
    ``TypeError: 'set' object is not subscriptable``.

    The existing happy-path test accepts an "already watched" fallback, so
    it never reached this line. This one forces the recommend branch.
    """
    fk = club.FilmKnowledge()
    fk.films = [
        {
            "title": "Unwatched Epic",
            "year": 1985,
            "themes": ["power", "fate", "violence", "greed", "loyalty"],
            "watchers": [],
        }
    ]
    fk.people = {
        "Alice": {
            "avg_rating": 8.0,
            "preferred_themes": ["power", "fate", "violence"],
            "films_watched": ["Something Else"],
        }
    }

    reply = club.recommend_for_group(fk, ["Alice"])

    assert "Unwatched Epic" in reply
    # The film's own themes, deduped, alphabetised, first four -- which is
    # what the misplaced slice was meant to produce.
    assert "fate, greed, loyalty, power" in reply
    assert "violence" not in reply  # 5th alphabetically, correctly dropped
