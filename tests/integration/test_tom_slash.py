"""Spec 4.2: Tom Bombadil slash command flows."""

from __future__ import annotations

from agents.tombombadil import commands as tom_commands
from agents.tombombadil.identity import Tier, Viewer
from agents.tombombadil.identity import resolve as resolve_viewer


def _viewer(discord_id: int, name: str, tier: Tier) -> Viewer:
    return Viewer(
        discord_id=str(discord_id),
        discord_name=name,
        canonical_name=name if tier is not Tier.STRANGER else None,
        tier=tier,
    )


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
