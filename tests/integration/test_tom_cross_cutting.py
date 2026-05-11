"""Spec 5: Tom Bombadil cross-cutting concerns."""

from __future__ import annotations

import pytest


@pytest.mark.xfail(
    strict=True,
    reason="D7: stranger onboarding paragraph not specialised. First-contact "
    "currently uses the generic film-aware reply; spec 5.1 wants greeting + "
    "concrete next action.",
)
@pytest.mark.asyncio
async def test_stranger_first_contact_includes_greeting_and_suggestion(
    identity_yaml, fake_redis, fake_bot_user, stranger, guild_channel
):
    """Spec 5.1: a stranger's very first message produces a reply that:
    - greets by display name
    - names what Tom does
    - suggests one concrete next action (mentions /whoami or 'tell me what you've watched')
    """
    from agents.tombombadil import bot as tom_bot
    from tests.integration.conftest import make_message
    content = f"<@{fake_bot_user.id}> hi"
    msg = make_message(stranger, guild_channel, content, mentions=[fake_bot_user])
    await tom_bot.on_message(msg)
    reply = msg.reply_log[0].lower()
    assert stranger.display_name.lower() in reply
    assert "film" in reply or "club" in reply
    assert "/whoami" in reply or "what you've been watching" in reply or "tell me" in reply
