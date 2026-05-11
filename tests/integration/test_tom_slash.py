"""Spec 4.2: Tom Bombadil slash command flows."""

from __future__ import annotations

import pytest

from agents.tombombadil import club, memory
from agents.tombombadil import commands as tom_commands
from agents.tombombadil.film_knowledge import FilmKnowledge
from agents.tombombadil.identity import resolve as resolve_viewer


def test_rate_saves_for_owner(identity_yaml, fake_redis, solomon):
    """Spec 4.2.1: /rate by the owner logs a note."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    reply = tom_commands.cmd_rate(fake_redis, viewer, "Inception", 9)
    assert "OK" in reply and "Inception" in reply
    assert fake_redis.sismember("films", "Inception")
    assert fake_redis.sismember("watchers", "Solomon Smith")


def test_rate_saves_for_regular(identity_yaml, fake_redis, brian):
    """Spec 4.2.1: /rate by a regular logs under their canonical name."""
    viewer = resolve_viewer(str(brian.id), str(brian))
    reply = tom_commands.cmd_rate(fake_redis, viewer, "Stalker", 8.5)
    assert "OK" in reply
    assert fake_redis.sismember("watchers", "Brian")


def test_rate_refuses_stranger(identity_yaml, fake_redis, stranger):
    """Spec 4.2.1: strangers get a configuration message, no save."""
    viewer = resolve_viewer(str(stranger.id), str(stranger))
    reply = tom_commands.cmd_rate(fake_redis, viewer, "Inception", 9)
    assert "canonical name" in reply
    assert not fake_redis.sismember("films", "Inception")


def test_rate_rejects_out_of_range(identity_yaml, fake_redis, solomon):
    """Spec 4.2.1 / preconditions: rating must be 0-10."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    reply = tom_commands.cmd_rate(fake_redis, viewer, "Inception", 15)
    assert "between 0 and 10" in reply
    assert not fake_redis.sismember("films", "Inception")


def test_rate_rejects_empty_film(identity_yaml, fake_redis, solomon):
    """Spec 4.2.1: empty film is refused."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    reply = tom_commands.cmd_rate(fake_redis, viewer, "   ", 9)
    assert "Film is required" in reply


def test_whoami_owner(identity_yaml, solomon):
    """Spec 4.2.7: /whoami shows tier=solomon for the owner."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    reply = tom_commands.cmd_whoami(viewer)
    assert "solomon" in reply.lower()
    assert "yes" in reply.lower()


def test_whoami_regular(identity_yaml, brian):
    """Spec 4.2.7: /whoami shows tier=regular for a club member."""
    viewer = resolve_viewer(str(brian.id), str(brian))
    reply = tom_commands.cmd_whoami(viewer)
    assert "regular" in reply.lower()
    assert "Brian" in reply


def test_whoami_stranger(identity_yaml, stranger):
    """Spec 4.2.7: strangers see tier=stranger and 'not yet mapped'."""
    viewer = resolve_viewer(str(stranger.id), str(stranger))
    reply = tom_commands.cmd_whoami(viewer)
    assert "stranger" in reply.lower()
    assert "not yet mapped" in reply.lower()


def test_recommend_for_solomon_returns_favorites_fallback(
    identity_yaml, fake_redis, solomon
):
    """Spec 4.2.2: Solomon has watched the entire seed catalog, so the
    theme-overlap pick is None; the fallback surfaces favorites instead
    of returning 'I don't have enough data'."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    reply = tom_commands.cmd_recommend(viewer)
    assert "don't have enough data" not in reply.lower()
    # Either the rec format or the favorites fallback header.
    assert reply.startswith("**For Solomon Smith")


def test_recommend_for_known_other_returns_string(identity_yaml, fake_redis, solomon):
    """Spec 4.2.2: /recommend for_name:<known> succeeds (rec or fallback)."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    reply = tom_commands.cmd_recommend(viewer, for_name="Brian")
    assert reply and "I don't know" not in reply


def test_recommend_for_unknown_admits_ignorance(identity_yaml, fake_redis, solomon):
    """Spec 4.2.2 / V5: unknown name returns a refusal, not a hallucinated rec."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    reply = tom_commands.cmd_recommend(viewer, for_name="NobodyAtAll")
    assert "don't know" in reply.lower()


def test_recommend_for_stranger_self_explains(identity_yaml, fake_redis, stranger):
    """Spec 4.2.2: a stranger asking /recommend with no for_name gets
    a helpful note about identity mapping."""
    viewer = resolve_viewer(str(stranger.id), str(stranger))
    reply = tom_commands.cmd_recommend(viewer)
    assert "name" in reply.lower()


def test_club_stats_includes_seed_films():
    """Spec 4.2.3: /club stats returns aggregate with Ran/La Haine/Ghost Dog."""
    reply = tom_commands.cmd_club_stats()
    assert "Top-rated" in reply
    assert "Ran" in reply or "La Haine" in reply


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def knowledge():
    """FilmKnowledge is treated read-only by all callers in this file.
    Module-scoping avoids reparsing FILM_DATABASE for every test."""
    return FilmKnowledge()


# ---------------------------------------------------------------------------
# Spec 4.2.4: /club recommend
# ---------------------------------------------------------------------------


def test_club_recommend_empty_names(knowledge):
    """Spec 4.2.4: empty list returns a 'pass one or more' message."""
    reply = club.cmd_club_recommend(knowledge, "")
    assert "one or more" in reply.lower()


def test_club_recommend_all_unknown(knowledge):
    """Spec 4.2.4: all-unknown names returns 'I don't know any of...'."""
    reply = club.cmd_club_recommend(knowledge, "NobodyA, NobodyB")
    assert "don't know any" in reply.lower()


def test_club_recommend_mixed_known_and_unknown(knowledge):
    """Spec 4.2.4: at least one known name produces a recommendation
    string (or the 'everyone's watched' fallback). Never crashes."""
    reply = club.cmd_club_recommend(knowledge, "Brian, NobodyA")
    assert isinstance(reply, str) and reply
    assert "don't know any" not in reply.lower()


# ---------------------------------------------------------------------------
# Spec 4.2.5: /club schedule
# ---------------------------------------------------------------------------


def test_club_schedule_saves_galadriel_job(
    identity_yaml, fake_redis, knowledge
):
    """Spec 4.2.5: /club schedule registers a Galadriel job in Redis."""
    reply = club.cmd_club_schedule(
        fake_redis, knowledge,
        film="Inception",
        when_iso="2099-01-01T19:00:00",
        channel_id="42",
        organizer="Solomon Smith",
    )
    assert reply.startswith("Scheduled watch party")
    # The Galadriel store writes cron:job:<id> keys.
    keys = [k for k in fake_redis.keys("cron:job:*")]
    assert any("watch_party_" in k for k in keys)


def test_club_schedule_rejects_bad_iso(identity_yaml, fake_redis, knowledge):
    """Spec 4.2.5: malformed ISO returns a typed error."""
    reply = club.cmd_club_schedule(
        fake_redis, knowledge,
        film="Inception", when_iso="next thursday",
        channel_id="42", organizer="Solomon Smith",
    )
    assert "ISO 8601" in reply


def test_club_schedule_rejects_empty_film(identity_yaml, fake_redis, knowledge):
    """Spec 4.2.5: empty film is refused before saving the job."""
    reply = club.cmd_club_schedule(
        fake_redis, knowledge,
        film="   ", when_iso="2099-01-01T19:00:00",
        channel_id="42", organizer="Solomon Smith",
    )
    assert "Film is required" in reply


def test_club_schedule_warns_for_uncatalogued_film(
    identity_yaml, fake_redis, knowledge
):
    """Spec 4.2.5: scheduling a film not in FILM_DATABASE adds a heads-up."""
    reply = club.cmd_club_schedule(
        fake_redis, knowledge,
        film="The Holy Mountain",
        when_iso="2099-01-01T19:00:00",
        channel_id="42", organizer="Solomon Smith",
    )
    assert "Scheduled" in reply
    assert "isn't in the catalog" in reply


# ---------------------------------------------------------------------------
# Spec 4.2.6: /forget across all scopes
# ---------------------------------------------------------------------------


def test_forget_short_clears_channel_history(identity_yaml, fake_redis, solomon):
    """Spec 4.2.6 short: clears current channel's history list."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    memory.append_turn(fake_redis, "tom:hist:ch:42", viewer, "user", "hi")
    reply = tom_commands.cmd_forget(fake_redis, viewer, "tom:hist:ch:42", "short")
    assert "conversation history" in reply.lower()
    assert memory.recent_turns(fake_redis, "tom:hist:ch:42") == []


def test_forget_prefs_clears_pref_hash(identity_yaml, fake_redis, solomon):
    """Spec 4.2.6 prefs: deletes tom:pref:<id> HASH."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    memory.set_pref(fake_redis, viewer.discord_id, "suppress_films", "1")
    reply = tom_commands.cmd_forget(fake_redis, viewer, None, "prefs")
    assert "preferences" in reply.lower()
    assert memory.get_prefs(fake_redis, viewer.discord_id) == {}


@pytest.mark.asyncio
async def test_forget_long_clears_viewer_finrod_facts(
    identity_yaml, fake_redis, finrod_in_memory, solomon
):
    """Spec 4.2.6 long: drops viewer's tom_fact rows from Finrod's store."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    await memory.remember_fact(viewer, "user loves Tarkovsky", source_channel="x")
    assert finrod_in_memory.store.count() > 0
    reply = tom_commands.cmd_forget(fake_redis, viewer, None, "long")
    assert "fact" in reply.lower()
    assert finrod_in_memory.store.count() == 0


@pytest.mark.asyncio
async def test_forget_all_clears_everything(
    identity_yaml, fake_redis, finrod_in_memory, solomon
):
    """Spec 4.2.6 all: short + prefs + long together."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    memory.append_turn(fake_redis, "tom:hist:ch:42", viewer, "user", "hi")
    memory.set_pref(fake_redis, viewer.discord_id, "suppress_films", "1")
    await memory.remember_fact(viewer, "fact", source_channel="x")
    reply = tom_commands.cmd_forget(fake_redis, viewer, "tom:hist:ch:42", "all")
    assert "Cleared" in reply
    assert memory.recent_turns(fake_redis, "tom:hist:ch:42") == []
    assert memory.get_prefs(fake_redis, viewer.discord_id) == {}
    assert finrod_in_memory.store.count() == 0


def test_forget_rejects_unknown_scope(identity_yaml, fake_redis, solomon):
    """Spec 4.2.6: unknown scope returns a typed error."""
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    reply = tom_commands.cmd_forget(fake_redis, viewer, "tom:hist:ch:42", "everything")
    assert "Scope must be" in reply


@pytest.mark.asyncio
async def test_forget_long_does_not_touch_other_viewer(
    identity_yaml, fake_redis, finrod_in_memory, solomon, brian
):
    """Spec 4.2.6 long / 5.4 privacy: Solomon's /forget never wipes Brian's facts."""
    solomon_v = resolve_viewer(str(solomon.id), str(solomon))
    brian_v = resolve_viewer(str(brian.id), str(brian))
    await memory.remember_fact(solomon_v, "solomon fact", source_channel="x")
    await memory.remember_fact(brian_v, "brian fact", source_channel="x")
    tom_commands.cmd_forget(fake_redis, solomon_v, None, "long")
    assert finrod_in_memory.store.count() == 1  # Brian's fact survives
