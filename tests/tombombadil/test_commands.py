from __future__ import annotations

import fakeredis
import pytest
from llama_index.core.llms import MockLLM

from agents._llama_index_mock import HashEmbedding
from agents.finrod.agent import Finrod
from agents.tombombadil import commands as tom_commands
from agents.tombombadil import memory
from agents.tombombadil.identity import Tier, Viewer

SOLOMON = Viewer(
    discord_id="111",
    discord_name="Solomon",
    canonical_name="Solomon Smith",
    tier=Tier.SOLOMON,
)
BRIAN = Viewer(
    discord_id="222",
    discord_name="Brian",
    canonical_name="Brian",
    tier=Tier.REGULAR,
)
STRANGER = Viewer(
    discord_id="999",
    discord_name="randomuser",
    canonical_name=None,
    tier=Tier.STRANGER,
)


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def finrod_in_memory(monkeypatch):
    instance = Finrod(llm=MockLLM(max_tokens=64), embed_model=HashEmbedding())
    monkeypatch.setattr(memory, "_get_finrod", lambda: instance)
    return instance


# ----- /rate ---------------------------------------------------------

def test_cmd_rate_saves_note(r):
    reply = tom_commands.cmd_rate(r, SOLOMON, "Inception", 9)
    assert "OK" in reply
    assert "Inception" in reply
    assert r.sismember("films", "Inception")
    assert r.sismember("watchers", "Solomon Smith")


def test_cmd_rate_rejects_stranger(r):
    reply = tom_commands.cmd_rate(r, STRANGER, "Inception", 9)
    assert "canonical name" in reply
    assert not r.sismember("films", "Inception")


def test_cmd_rate_clamps_invalid_rating(r):
    reply = tom_commands.cmd_rate(r, SOLOMON, "Inception", 15)
    assert "between 0 and 10" in reply
    assert not r.sismember("films", "Inception")


def test_cmd_rate_empty_film(r):
    reply = tom_commands.cmd_rate(r, SOLOMON, "   ", 9)
    assert "Film is required" in reply


# ----- /recommend ----------------------------------------------------

def test_cmd_recommend_defaults_to_invoker():
    reply = tom_commands.cmd_recommend(SOLOMON)
    # Solomon has data in FILM_DATABASE seed; should not be the "don't know" fallback.
    assert isinstance(reply, str)
    assert reply  # non-empty


def test_cmd_recommend_for_someone_else():
    reply = tom_commands.cmd_recommend(SOLOMON, for_name="Brian")
    assert isinstance(reply, str)


def test_cmd_recommend_for_unknown_returns_message():
    reply = tom_commands.cmd_recommend(SOLOMON, for_name="NobodyAtAll")
    assert "don't know" in reply.lower()


def test_cmd_recommend_stranger_with_no_target():
    reply = tom_commands.cmd_recommend(STRANGER)
    assert "name" in reply.lower()


# ----- /club stats ---------------------------------------------------

def test_cmd_club_stats_includes_top_rated():
    reply = tom_commands.cmd_club_stats()
    assert "Top-rated" in reply
    assert "Ran" in reply or "La Haine" in reply


# ----- /forget -------------------------------------------------------

@pytest.mark.asyncio
async def test_cmd_forget_short_clears_channel_history(r):
    memory.append_turn(r, "tom:hist:ch:1", SOLOMON, "user", "hello")
    reply = await tom_commands.cmd_forget(r, SOLOMON, "tom:hist:ch:1", "short")
    assert "conversation history" in reply
    assert memory.recent_turns(r, "tom:hist:ch:1") == []


@pytest.mark.asyncio
async def test_cmd_forget_prefs_clears_prefs(r):
    memory.set_pref(r, SOLOMON.discord_id, "suppress_films", "1")
    reply = await tom_commands.cmd_forget(r, SOLOMON, None, "prefs")
    assert "preferences" in reply
    assert memory.get_prefs(r, SOLOMON.discord_id) == {}


@pytest.mark.asyncio
async def test_cmd_forget_long_clears_finrod_facts(finrod_in_memory, r):
    await memory.remember_fact(SOLOMON, "user loves Tarkovsky", source_channel="x")
    assert finrod_in_memory.node_count() > 0
    reply = await tom_commands.cmd_forget(r, SOLOMON, None, "long")
    assert "fact" in reply.lower()
    assert finrod_in_memory.node_count() == 0


@pytest.mark.asyncio
async def test_cmd_forget_all_clears_everything(finrod_in_memory, r):
    memory.append_turn(r, "tom:hist:ch:1", SOLOMON, "user", "hi")
    memory.set_pref(r, SOLOMON.discord_id, "suppress_films", "1")
    await memory.remember_fact(SOLOMON, "user loves Tarkovsky", source_channel="x")

    reply = await tom_commands.cmd_forget(r, SOLOMON, "tom:hist:ch:1", "all")
    assert memory.recent_turns(r, "tom:hist:ch:1") == []
    assert memory.get_prefs(r, SOLOMON.discord_id) == {}
    assert finrod_in_memory.node_count() == 0
    assert "Cleared" in reply


@pytest.mark.asyncio
async def test_cmd_forget_rejects_invalid_scope(r):
    reply = await tom_commands.cmd_forget(r, SOLOMON, "tom:hist:ch:1", "everything")
    assert "Scope must be" in reply


@pytest.mark.asyncio
async def test_forget_long_does_not_touch_other_viewer(finrod_in_memory, r):
    await memory.remember_fact(SOLOMON, "Solomon loves Tarkovsky", source_channel="x")
    await memory.remember_fact(BRIAN, "Brian loves Get Out", source_channel="x")
    assert finrod_in_memory.node_count() == 2

    await tom_commands.cmd_forget(r, SOLOMON, None, "long")
    assert finrod_in_memory.node_count() == 1  # Brian's fact survives


# ----- /whoami -------------------------------------------------------

def test_cmd_whoami_owner():
    reply = tom_commands.cmd_whoami(SOLOMON)
    assert "solomon" in reply.lower()
    assert "yes" in reply.lower()  # owner: yes


def test_cmd_whoami_stranger():
    reply = tom_commands.cmd_whoami(STRANGER)
    assert "stranger" in reply.lower()
    assert "not yet mapped" in reply.lower()


# ----- /setpref (D6) -------------------------------------------------

def test_cmd_setpref_sets_valid_key(r):
    reply = tom_commands.cmd_setpref(r, BRIAN, "suppress_films", "1")
    assert "Set" in reply
    assert memory.get_prefs(r, BRIAN.discord_id)["suppress_films"] == "1"


def test_cmd_setpref_rejects_unknown_key(r):
    reply = tom_commands.cmd_setpref(r, BRIAN, "not_a_real_pref", "1")
    assert "Unknown pref" in reply
    assert memory.get_prefs(r, BRIAN.discord_id) == {}


def test_cmd_setpref_clear_unsets(r):
    memory.set_pref(r, BRIAN.discord_id, "suppress_films", "1")
    reply = tom_commands.cmd_setpref(r, BRIAN, "suppress_films", "clear")
    assert "Cleared" in reply
    assert "suppress_films" not in memory.get_prefs(r, BRIAN.discord_id)


# ----- /unrate (D9) --------------------------------------------------

def test_cmd_unrate_removes_note(r):
    tom_commands.cmd_rate(r, SOLOMON, "Inception", 9)
    assert r.sismember("films", "Inception")
    reply = tom_commands.cmd_unrate(r, SOLOMON, "Inception")
    assert "Removed" in reply
    assert r.zcard("notes:all") == 0
    assert not r.sismember("films", "Inception")
    # Re-rate same week should succeed after unique-key clear.
    reply2 = tom_commands.cmd_rate(r, SOLOMON, "Inception", 7)
    assert "OK" in reply2


def test_cmd_unrate_missing_note(r):
    reply = tom_commands.cmd_unrate(r, SOLOMON, "Nonexistent Film")
    assert "No note found" in reply


def test_cmd_unrate_rejects_stranger(r):
    reply = tom_commands.cmd_unrate(r, STRANGER, "Inception")
    assert "canonical name" in reply


# ----- admin (D10) ---------------------------------------------------

def test_cmd_ban_owner_only(r):
    refused = tom_commands.cmd_ban(r, BRIAN, "999")
    assert "Owner only" in refused
    assert not r.sismember("tom:bans", "999")

    ok = tom_commands.cmd_ban(r, SOLOMON, "999")
    assert "Banned" in ok
    assert r.sismember("tom:bans", "999")

    un = tom_commands.cmd_unban(r, SOLOMON, "999")
    assert "Unbanned" in un
    assert not r.sismember("tom:bans", "999")


def test_cmd_setrole_override(r):
    refused = tom_commands.cmd_setrole(r, BRIAN, "555", "regular", "Wes")
    assert "Owner only" in refused

    ok = tom_commands.cmd_setrole(r, SOLOMON, "555", "regular", "Wes Prater")
    assert "regular" in ok
    from agents.tombombadil.identity import resolve

    viewer = resolve("555", "someone", redis=r)
    assert viewer.tier is Tier.REGULAR
    assert viewer.canonical_name == "Wes Prater"
