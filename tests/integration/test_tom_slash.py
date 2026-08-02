"""Spec 4.2: Tom Bombadil slash command *glue* (register_commands path).

Pure ``cmd_*`` behaviour lives in ``tests/tombombadil/test_commands.py``
and ``tests/tombombadil/test_club.py``. This module drives the
discord.py app-command callbacks registered by ``register_commands``
through ``FakeInteraction``, asserting:

- ephemeral True/False per command family
- ``tom_slash_commands_total`` metric increments
- interaction-derived wiring (viewer from FakeUser, channel_id,
  history_scope_key for /forget)
"""

from __future__ import annotations

import pytest

from agents.galadriel.store import list_jobs
from agents.tombombadil import memory, metrics
from agents.tombombadil.identity import resolve as resolve_viewer
from tests.integration.conftest import make_interaction


def _slash_count(name: str) -> float:
    return metrics.SLASH_COMMANDS.labels(name=name)._value.get()


def _callback(tree, name: str):
    cmd = tree.get_command(name)
    assert cmd is not None, f"missing slash /{name}"
    return cmd.callback


def _club_callback(tree, name: str):
    club = tree.get_command("club")
    assert club is not None, "missing /club group"
    cmd = club.get_command(name)
    assert cmd is not None, f"missing slash /club {name}"
    return cmd.callback


def _last_reply(interaction) -> tuple[str, bool]:
    assert interaction.response.sent, "expected response.send_message"
    return interaction.response.sent[-1]


# ---------------------------------------------------------------------------
# Public (non-ephemeral) commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_glue_public_and_metrics(identity_yaml, fake_redis, slash_tree, solomon, guild_channel):
    """Spec 4.2.1 glue: /rate is public, increments metric, saves note."""
    ix = make_interaction(solomon, guild_channel)
    before = _slash_count("rate")
    await _callback(slash_tree, "rate")(ix, film="Inception", rating=9.0)
    content, ephemeral = _last_reply(ix)
    assert ephemeral is False
    assert "OK" in content and "Inception" in content
    assert _slash_count("rate") == before + 1
    assert fake_redis.sismember("films", "Inception")
    assert fake_redis.sismember("watchers", "Solomon Smith")


@pytest.mark.asyncio
async def test_recommend_glue_public_and_metrics(
    identity_yaml, fake_redis, slash_tree, solomon, guild_channel
):
    """Spec 4.2.2 glue: /recommend is public and increments metric."""
    ix = make_interaction(solomon, guild_channel)
    before = _slash_count("recommend")
    await _callback(slash_tree, "recommend")(ix, for_name=None)
    content, ephemeral = _last_reply(ix)
    assert ephemeral is False
    assert content.startswith("**For Solomon Smith")
    assert _slash_count("recommend") == before + 1


@pytest.mark.asyncio
async def test_club_stats_glue_public_and_metrics(identity_yaml, fake_redis, slash_tree, solomon, guild_channel):
    """Spec 4.2.3 glue: /club stats is public and increments metric."""
    ix = make_interaction(solomon, guild_channel)
    before = _slash_count("club_stats")
    await _club_callback(slash_tree, "stats")(ix)
    content, ephemeral = _last_reply(ix)
    assert ephemeral is False
    assert "Top-rated" in content
    assert _slash_count("club_stats") == before + 1


@pytest.mark.asyncio
async def test_club_recommend_glue_public_and_metrics(
    identity_yaml, fake_redis, slash_tree, solomon, guild_channel
):
    """Spec 4.2.4 glue: /club recommend is public and increments metric."""
    ix = make_interaction(solomon, guild_channel)
    before = _slash_count("club_recommend")
    await _club_callback(slash_tree, "recommend")(ix, names="Brian")
    content, ephemeral = _last_reply(ix)
    assert ephemeral is False
    assert isinstance(content, str) and content
    assert _slash_count("club_recommend") == before + 1


@pytest.mark.asyncio
async def test_club_schedule_uses_interaction_channel_id(
    identity_yaml, fake_redis, slash_tree, solomon, guild_channel
):
    """Spec 4.2.5 glue: channel_id comes from the interaction, not a hardcoded arg."""
    ix = make_interaction(solomon, guild_channel)
    before = _slash_count("club_schedule")
    await _club_callback(slash_tree, "schedule")(
        ix, film="Inception", when="2099-01-01T19:00:00"
    )
    content, ephemeral = _last_reply(ix)
    assert ephemeral is False
    assert content.startswith("Scheduled watch party")
    assert _slash_count("club_schedule") == before + 1
    jobs = [j for j in list_jobs(fake_redis) if j.id.startswith("watch_party_")]
    assert jobs, "expected a watch_party cron job"
    # Delivery target must be the FakeInteraction channel id.
    assert jobs[0].delivery.to == str(guild_channel.id)


# ---------------------------------------------------------------------------
# Ephemeral (private) commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whoami_glue_ephemeral_resolves_regular(
    identity_yaml, fake_redis, slash_tree, brian, guild_channel
):
    """Spec 4.2.7 glue: /whoami is ephemeral; viewer comes from FakeUser + YAML."""
    ix = make_interaction(brian, guild_channel)
    before = _slash_count("whoami")
    await _callback(slash_tree, "whoami")(ix)
    content, ephemeral = _last_reply(ix)
    assert ephemeral is True
    assert "regular" in content.lower()
    assert "Brian" in content
    assert _slash_count("whoami") == before + 1


@pytest.mark.asyncio
async def test_forget_glue_ephemeral_uses_history_scope_key(
    identity_yaml, fake_redis, slash_tree, solomon, guild_channel
):
    """Spec 4.2.6 glue: /forget is ephemeral and scopes via history_scope_key."""
    ix = make_interaction(solomon, guild_channel)
    scope_key = memory.history_scope_key(ix)
    assert scope_key == f"tom:hist:ch:{guild_channel.id}"
    # Seed a turn under the interaction-derived key (not a hand-built string).
    viewer = resolve_viewer(str(solomon.id), str(solomon), redis=fake_redis)
    memory.append_turn(fake_redis, scope_key, viewer, "user", "hi")

    before = _slash_count("forget")
    await _callback(slash_tree, "forget")(ix, scope="short")
    content, ephemeral = _last_reply(ix)
    assert ephemeral is True
    assert "conversation history" in content.lower()
    assert memory.recent_turns(fake_redis, scope_key) == []
    assert _slash_count("forget") == before + 1


@pytest.mark.asyncio
async def test_setpref_glue_ephemeral_and_metrics(
    identity_yaml, fake_redis, slash_tree, solomon, guild_channel
):
    """D6 glue: /setpref is ephemeral and increments metric."""
    ix = make_interaction(solomon, guild_channel)
    before = _slash_count("setpref")
    await _callback(slash_tree, "setpref")(ix, key="suppress_films", value="1")
    content, ephemeral = _last_reply(ix)
    assert ephemeral is True
    assert "suppress_films" in content
    assert _slash_count("setpref") == before + 1
    assert memory.get_prefs(fake_redis, str(solomon.id)).get("suppress_films") == "1"


@pytest.mark.asyncio
async def test_sync_glue_defers_then_followup(
    identity_yaml, fake_redis, slash_tree, solomon, guild_channel, monkeypatch
):
    """D10 glue: /sync defers (ephemeral) then replies via followup."""
    monkeypatch.setattr(
        "agents.tombombadil.commands.cmd_sync",
        lambda *_a, **_k: "Synced 0 films.",
    )
    ix = make_interaction(solomon, guild_channel)
    before = _slash_count("sync")
    await _callback(slash_tree, "sync")(ix)
    assert ix.response.deferred is True
    assert ix.response.deferred_ephemeral is True
    assert not ix.response.sent
    assert ix.followup.sent
    content, ephemeral = ix.followup.sent[-1]
    assert ephemeral is True
    assert "Synced" in content
    assert _slash_count("sync") == before + 1
