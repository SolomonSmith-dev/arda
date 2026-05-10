"""Shared fixtures for Tom Bombadil integration tests."""

from __future__ import annotations

from pathlib import Path

import fakeredis
import pytest

from agents.finrod.agent import Finrod
from agents.finrod.embeddings import MockEmbedder
from agents.finrod.store import InMemoryStore
from agents.tombombadil import agent as tom_agent
from agents.tombombadil import bot as tom_bot
from agents.tombombadil import identity, memory
from tests.integration._doubles import FakeChannel, FakeInteraction, FakeMessage, FakeUser

SOLOMON_DISCORD_ID = "298740907778375680"
BRIAN_DISCORD_ID = "200000000000000001"
WES_DISCORD_ID = "200000000000000002"
STRANGER_DISCORD_ID = "999999999999999999"
TOM_DISCORD_ID = 1487666626919792740


YAML_BODY = f"""\
owner:
  discord_id: "{SOLOMON_DISCORD_ID}"
  canonical_name: "Solomon Smith"
regulars:
  - discord_id: "{BRIAN_DISCORD_ID}"
    canonical_name: "Brian"
"""


@pytest.fixture
def identity_yaml(tmp_path: Path, monkeypatch):
    path = tmp_path / "identity.yaml"
    path.write_text(YAML_BODY)
    monkeypatch.setenv("TOMBOMBADIL_IDENTITY_FILE", str(path))
    identity.reload_config()
    yield path
    identity.reload_config()


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(tom_agent, "get_redis_sync", lambda: r)
    monkeypatch.setattr(tom_bot, "get_redis_sync", lambda: r)
    return r


@pytest.fixture
def finrod_in_memory(monkeypatch):
    instance = Finrod(store=InMemoryStore(), embedder=MockEmbedder())
    monkeypatch.setattr(memory, "_get_finrod", lambda: instance)
    return instance


@pytest.fixture(autouse=True)
def stub_long_term_memory(monkeypatch, request):
    """Default: noop long-term memory. Tests that need real recall
    request the `finrod_in_memory` fixture, which overrides _get_finrod.
    """
    # request.fixturenames includes transitively requested fixtures,
    # so this correctly skips patching even when finrod_in_memory is
    # pulled in indirectly by another fixture.
    if "finrod_in_memory" in request.fixturenames:
        return

    async def _no_facts(*_args, **_kwargs):
        return []

    async def _no_op(*_args, **_kwargs):
        return True

    monkeypatch.setattr(memory, "recall_facts", _no_facts)
    monkeypatch.setattr(memory, "remember_fact", _no_op)


@pytest.fixture
def fake_bot_user(monkeypatch):
    """Replace tom_bot.bot with a minimal shim exposing user and get_channel.

    The real bot is a discord.ext.commands.Bot with a private _connection;
    replacing the entire module-level binding with a shim is simpler than
    trying to mutate the real bot's internal state.
    """
    tom = FakeUser(id=TOM_DISCORD_ID, name="TomBombadil")

    class _BotShim:
        user = tom

        @staticmethod
        def get_channel(_cid):
            return None

    monkeypatch.setattr(tom_bot, "bot", _BotShim())
    return tom


def _user(discord_id: str | int, name: str) -> FakeUser:
    return FakeUser(id=int(discord_id), name=name, display_name=name)


@pytest.fixture
def solomon() -> FakeUser:
    return _user(SOLOMON_DISCORD_ID, "Solomon Smith")


@pytest.fixture
def brian() -> FakeUser:
    return _user(BRIAN_DISCORD_ID, "Brian")


@pytest.fixture
def wes() -> FakeUser:
    return _user(WES_DISCORD_ID, "Wes Prater")


@pytest.fixture
def stranger() -> FakeUser:
    return _user(STRANGER_DISCORD_ID, "RandomVisitor")


@pytest.fixture
def guild_channel() -> FakeChannel:
    return FakeChannel(id=42, type="text", name="test")


@pytest.fixture
def dm_channel(solomon) -> FakeChannel:
    # type string must end with "private" so memory.history_scope_key
    # routes this to the tom:hist:dm:* namespace.
    return FakeChannel(id=solomon.id, type="DMChannel.private", name="dm")


def make_message(
    author: FakeUser,
    channel: FakeChannel,
    content: str,
    mentions: list[FakeUser] | None = None,
    message_id: int | None = None,
) -> FakeMessage:
    return FakeMessage(
        id=message_id or (channel.id * 100 + author.id % 1000),
        content=content,
        author=author,
        channel=channel,
        mentions=list(mentions or []),
    )


def make_interaction(user: FakeUser, channel: FakeChannel) -> FakeInteraction:
    return FakeInteraction(user=user, channel_id=channel.id, channel=channel)
