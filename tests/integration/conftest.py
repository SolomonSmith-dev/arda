"""Shared fixtures for Tom Bombadil integration tests."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import fakeredis
import pytest

from agents._mock_llm import _MockResponse
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


@pytest.fixture(autouse=True)
def _isolate_letterboxd_export_dir(monkeypatch):
    """A dev environment with LETTERBOXD_EXPORT_DIR set would cause
    every FilmKnowledge() construction (e.g., the ``knowledge`` fixture)
    to parse CSVs from disk. Clear it for the duration of every test
    so the integration suite is hermetic.
    """
    monkeypatch.delenv("LETTERBOXD_EXPORT_DIR", raising=False)


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

        @staticmethod
        async def process_commands(_msg):
            # bot.on_message ends with `await bot.process_commands(message)`
            # for legacy text-command (`!cmd`) routing. Tom doesn't use any
            # text commands, so a no-op is the correct test double.
            return

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


# Module-level helper reused across test modules.
async def _send_mention(channel, user, text, *, bot_user):
    """Build a mention message addressed to Tom and drive on_message."""
    content = f"<@{bot_user.id}> {text}"
    msg = make_message(user, channel, content, mentions=[bot_user])
    await tom_bot.on_message(msg)
    return msg


@contextmanager
def capture_system_prompt():
    """Patch tom_agent._llm.invoke to capture the joined SystemMessage
    contents. Yields a dict that gets a ``sys`` key populated after the
    next ``get_response`` / ``on_message`` call.

    Use:
        with capture_system_prompt() as cap:
            await tom_agent.get_response(...)
        assert "..." in cap["sys"]
    """
    captured: dict = {}

    def _fake(messages):
        captured["sys"] = "\n".join(
            m.content for m in messages if m.__class__.__name__ == "SystemMessage"
        )
        return _MockResponse(content="ok")

    with patch.object(tom_agent._llm, "invoke", side_effect=_fake):
        yield captured


SAMPLE_LETTERBOXD_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:letterboxd="https://letterboxd.com">
  <channel>
    <item>
      <title>Stalker, 1979 - ★★★★★</title>
      <letterboxd:filmTitle>Stalker</letterboxd:filmTitle>
      <letterboxd:filmYear>1979</letterboxd:filmYear>
      <letterboxd:memberRating>5.0</letterboxd:memberRating>
      <letterboxd:watchedDate>2026-05-09</letterboxd:watchedDate>
    </item>
    <item>
      <title>Solaris, 1972 - ★★★★½</title>
      <letterboxd:filmTitle>Solaris</letterboxd:filmTitle>
      <letterboxd:filmYear>1972</letterboxd:filmYear>
      <letterboxd:memberRating>4.5</letterboxd:memberRating>
      <letterboxd:watchedDate>2026-05-10</letterboxd:watchedDate>
    </item>
  </channel>
</rss>"""
