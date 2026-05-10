"""Spec 4.1: Tom Bombadil conversational flows."""

from __future__ import annotations


def test_harness_imports_cleanly(identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel):
    """Spec smoke: shared fixtures wire up without exceptions."""
    assert solomon.name == "Solomon Smith"
    assert guild_channel.id == 42
    assert fake_bot_user.id == 1487666626919792740
    assert fake_redis.ping()
