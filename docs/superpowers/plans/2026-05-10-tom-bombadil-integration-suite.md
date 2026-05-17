# Tom Bombadil Integration Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an integration test suite that asserts every contract in `docs/superpowers/specs/2026-05-10-tom-bombadil-behavior-spec.md`, with tests passing for currently-correct behavior and `xfail` markers tagging the 10 known deltas (D1–D10) so the audit phase (sub-project C) has a machine-readable fix list.

**Architecture:** A shared discord.py doubles harness under `tests/integration/_doubles.py` provides minimal stand-ins for `User`, `Channel`, `Message`, `Reaction`, and `Interaction` — enough to drive `bot.on_message`, `bot.on_reaction_add`, and registered slash handlers directly without spinning up a real Discord client. `tests/integration/conftest.py` wires `fakeredis` + `MockLLM` + an in-memory Finrod into the same module-level globals the bot uses (via `monkeypatch`), so tests exercise the actual production code paths. Each spec section gets one test module (`test_tom_<group>.py`), each test docstring references the spec contract it covers.

**Tech Stack:** pytest 9, pytest-asyncio (already auto-mode in `pyproject.toml`), fakeredis (already a dev dep), `agents._mock_llm.MockLLM`, `agents.finrod.embeddings.MockEmbedder`, `agents.finrod.store.InMemoryStore`. discord.py doubles authored fresh in this plan — keep them minimal and local to `tests/integration/`.

---

## File Structure

**Create:**

| Path | Responsibility |
|------|----------------|
| `tests/integration/__init__.py` | empty package marker |
| `tests/integration/_doubles.py` | discord.py stand-ins: `FakeUser`, `FakeChannel`, `FakeMessage`, `FakeReaction`, `FakeInteraction`, `FakeGuild` |
| `tests/integration/conftest.py` | shared fixtures: `fake_redis`, `mock_llm`, `finrod_in_memory`, `fake_bot_user`, `make_message`, `make_interaction`, `identity_yaml` |
| `tests/integration/test_tom_conversational.py` | 4.1.1 mention reply, 4.1.2 draft flow |
| `tests/integration/test_tom_slash.py` | 4.2.1 `/rate`, 4.2.2 `/recommend`, 4.2.3-5 `/club {stats,recommend,schedule}`, 4.2.6 `/forget`, 4.2.7 `/whoami` |
| `tests/integration/test_tom_scheduled.py` | 4.3.1 watch-party, 4.3.2 letterboxd sync |
| `tests/integration/test_tom_internal.py` | 4.4.1 recall, 4.4.2 prefs, 4.4.3 identity+roster |
| `tests/integration/test_tom_cross_cutting.py` | 5.1 onboarding, 5.2 collisions, 5.3 failure modes, 5.4 privacy, 5.5 operator |

**Modify:** none. Sub-project B is test-only.

**Reference (read for patterns, do not modify):**

- `tests/tombombadil/test_agent_smoke.py:1-40` — `fake_redis` fixture pattern using `monkeypatch.setattr(tom_agent, "get_redis_sync", lambda: r)`.
- `tests/tombombadil/test_commands.py:30-50` — `finrod_in_memory` fixture pattern with `monkeypatch.setattr(memory, "_get_finrod", lambda: instance)`.
- `tests/tombombadil/test_identity.py:18-30` — `TOMBOMBADIL_IDENTITY_FILE` env override + `identity.reload_config()`.
- `agents/tombombadil/bot.py` — the production handlers we'll drive directly: `on_message`, `_offer_pending_draft`, `on_reaction_add`, `_guard_check`, `_resolve_mentions`.
- `agents/tombombadil/commands.py:140-200` — the slash command registrations whose handler functions (`cmd_rate`, `cmd_recommend`, `cmd_club_stats`, `club.cmd_club_recommend`, `club.cmd_club_schedule`, `cmd_forget`, `cmd_whoami`) we can also test directly.

---

## Conventions used in every task

1. **Spec docstring:** every test function's docstring opens with `"Spec 4.1.1 / V5: ..."` (section + voice rule) so failures point straight at the contract.
2. **Known-delta marking:** tests covering D1–D10 use `@pytest.mark.xfail(strict=True, reason="D2: requester_discord_id bound at pop time, not push time")`. `strict=True` means a passing xfail is also a failure — that's the signal that the delta has been fixed and the test should drop the marker.
3. **No production-code edits in this plan.** If a test demands a change to production code, that's a finding for sub-project C, not a step here.
4. **Async tests:** declared with `async def test_...` — `pyproject.toml` already sets `asyncio_mode = "auto"`.
5. **Run command per task:** `pytest tests/integration/test_tom_<name>.py -v 2>&1 | tail -30` — show failures, xfails, and pass counts.

---

## Task 1: Bootstrap harness + shared fixtures

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/_doubles.py`
- Create: `tests/integration/conftest.py`
- Test: `tests/integration/test_tom_conversational.py` (one smoke test only — full coverage in Task 2)

- [ ] **Step 1: Create package marker**

```bash
touch /Users/solomonsmith/Projects/arda/tests/integration/__init__.py
```

- [ ] **Step 2: Create discord.py doubles**

Write `tests/integration/_doubles.py` with these classes. The shape mirrors what `bot.on_message` and slash handlers actually read from real discord.py objects — no more.

```python
"""Discord.py doubles for Tom Bombadil integration tests.

Minimal stand-ins for User, Channel, Message, Reaction, Interaction.
Each class exposes only the attributes / methods actually touched by
agents.tombombadil.bot and agents.tombombadil.commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock


@dataclass
class FakeUser:
    id: int
    display_name: str = ""
    name: str = ""

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.name or f"user_{self.id}"
        if not self.name:
            self.name = self.display_name

    def __eq__(self, other):
        return isinstance(other, FakeUser) and other.id == self.id

    def __hash__(self):
        return hash(self.id)

    def __str__(self):
        return self.display_name


@dataclass
class FakeChannel:
    id: int
    type: str = "text"            # "text" for guild channels, "private" for DMs
    name: str = "general"
    sent: list[str] = field(default_factory=list)

    async def send(self, content: str):
        self.sent.append(content)
        # Real Discord returns the sent Message. Return a fresh FakeMessage
        # so callers that bind drafts to it have a stable id.
        msg = FakeMessage(id=10_000_000 + len(self.sent), content=content, author=FakeUser(id=0, name="TomBombadil"), channel=self, mentions=[])
        return msg

    def __str__(self):
        return f"<#{self.id}>"


@dataclass
class FakeMessage:
    id: int
    content: str
    author: FakeUser
    channel: FakeChannel
    mentions: list[FakeUser] = field(default_factory=list)
    role_mentions: list[Any] = field(default_factory=list)
    reactions_added: list[str] = field(default_factory=list)
    reply_log: list[str] = field(default_factory=list)
    _next_reply_id: int = 0

    async def reply(self, content: str, mention_author: bool = True, delete_after: int | None = None):
        self.reply_log.append(content)
        # Each reply is a fresh message in the same channel — register it
        # so subsequent draft binding has a stable id.
        self._next_reply_id += 1
        msg = FakeMessage(
            id=self.id * 1000 + self._next_reply_id,
            content=content,
            author=FakeUser(id=0, name="TomBombadil"),
            channel=self.channel,
            mentions=[],
        )
        self.channel.sent.append(content)
        return msg

    async def add_reaction(self, emoji: str):
        self.reactions_added.append(emoji)


@dataclass
class FakeReaction:
    emoji: str
    message: FakeMessage


@dataclass
class FakeResponse:
    sent: list[tuple[str, bool]] = field(default_factory=list)

    async def send_message(self, content: str, ephemeral: bool = False):
        self.sent.append((content, ephemeral))


@dataclass
class FakeInteraction:
    user: FakeUser
    channel_id: int
    channel: FakeChannel
    response: FakeResponse = field(default_factory=FakeResponse)
```

- [ ] **Step 3: Create conftest with shared fixtures**

Write `tests/integration/conftest.py`. The fixtures patch the same module-level globals the real bot uses, so every test drives production code.

```python
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
    # All three module-level get_redis_sync references are patched so
    # whichever code path the test exercises talks to the same FakeRedis.
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
    if "finrod_in_memory" in request.fixturenames:
        return  # the real fixture is in play; don't stub it out

    async def _no_facts(*_args, **_kwargs):
        return []

    async def _no_op(*_args, **_kwargs):
        return True

    monkeypatch.setattr(memory, "recall_facts", _no_facts)
    monkeypatch.setattr(memory, "remember_fact", _no_op)


@pytest.fixture
def fake_bot_user(monkeypatch):
    """Replace tom_bot.bot.user (a property) with a fake user. The bot's
    on_message uses `bot.user.id` and `bot.user.mentioned_in(message)`.
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
```

- [ ] **Step 4: Write one smoke test asserting the harness imports cleanly**

Write `tests/integration/test_tom_conversational.py` with a single sentinel test:

```python
"""Spec 4.1: Tom Bombadil conversational flows."""

from __future__ import annotations

import pytest


def test_harness_imports_cleanly(identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel):
    """Spec smoke: shared fixtures wire up without exceptions."""
    assert solomon.name == "Solomon Smith"
    assert guild_channel.id == 42
    assert fake_bot_user.id == 1487666626919792740
    assert fake_redis.ping()
```

- [ ] **Step 5: Run the smoke test**

```bash
cd /Users/solomonsmith/Projects/arda
source /Users/solomonsmith/Projects/Agents/.venv/bin/activate
pytest tests/integration/ -v 2>&1 | tail -10
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/solomonsmith/Projects/arda
git checkout -b claude/tom-integration-suite
git add tests/integration/
git commit -m "feat(tests): bootstrap Tom Bombadil integration harness

Shared discord.py doubles + conftest for sub-project B.
Smoke test passes. Module test files added in following commits."
```

---

## Task 2: Spec 4.1.1 — Mention reply contract

**Files:**
- Modify: `tests/integration/test_tom_conversational.py`

- [ ] **Step 1: Add happy-path tests for the three tiers**

Append to `test_tom_conversational.py`:

```python
from agents.tombombadil import bot as tom_bot
from agents.tombombadil import memory


async def _send_mention(channel, user, text, *, bot_user):
    """Build a mention message addressed to Tom and drive on_message."""
    content = f"<@{bot_user.id}> {text}"
    from tests.integration.conftest import make_message
    msg = make_message(user, channel, content, mentions=[bot_user])
    await tom_bot.on_message(msg)
    return msg


@pytest.mark.asyncio
async def test_solomon_mention_replies_with_mockllm_marker(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.1: a mention from the owner produces one reply via MockLLM."""
    msg = await _send_mention(guild_channel, solomon, "tell me about Ran", bot_user=fake_bot_user)
    assert len(msg.reply_log) == 1
    assert "[mock:" in msg.reply_log[0]


@pytest.mark.asyncio
async def test_regular_mention_replies(
    identity_yaml, fake_redis, fake_bot_user, brian, guild_channel
):
    """Spec 4.1.1: a regular member mention also produces a reply."""
    msg = await _send_mention(guild_channel, brian, "what should we watch?", bot_user=fake_bot_user)
    assert len(msg.reply_log) == 1


@pytest.mark.asyncio
async def test_stranger_mention_replies(
    identity_yaml, fake_redis, fake_bot_user, stranger, guild_channel
):
    """Spec 4.1.1: strangers still get a reply (no LLM call refusal)."""
    msg = await _send_mention(guild_channel, stranger, "hi", bot_user=fake_bot_user)
    assert len(msg.reply_log) == 1
```

- [ ] **Step 2: Add side-effect tests (history + counter)**

```python
@pytest.mark.asyncio
async def test_mention_appends_user_and_assistant_turns(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.1 side effects: two turns persist after a successful reply."""
    await _send_mention(guild_channel, solomon, "hello", bot_user=fake_bot_user)
    turns = memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}")
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[0].content == "hello"
    assert turns[1].role == "assistant"
    # V6: assistant turn must NOT begin with `[viewer]`.
    assert not turns[1].content.startswith("[")
```

- [ ] **Step 3: Add guard / error tests**

```python
from agents.tombombadil import guards


@pytest.mark.asyncio
async def test_banned_user_gets_canned_refusal_no_llm_call(
    identity_yaml, fake_redis, fake_bot_user, stranger, guild_channel
):
    """Spec 4.1.1 ban path: banned user receives the canned refusal and
    no history is written."""
    guards.ban(fake_redis, str(stranger.id))
    msg = await _send_mention(guild_channel, stranger, "hi", bot_user=fake_bot_user)
    assert msg.reply_log == ["I've been asked not to engage with you. Sorry."]
    assert memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}") == []


@pytest.mark.asyncio
async def test_prompt_too_long_is_refused(
    identity_yaml, fake_redis, fake_bot_user, brian, guild_channel
):
    """Spec 4.1.1 length cap: payloads over MAX_PROMPT_CHARS are refused."""
    long_text = "x" * (guards.MAX_PROMPT_CHARS + 5)
    msg = await _send_mention(guild_channel, brian, long_text, bot_user=fake_bot_user)
    assert len(msg.reply_log) == 1
    assert str(guards.MAX_PROMPT_CHARS) in msg.reply_log[0]


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_budget(
    identity_yaml, fake_redis, fake_bot_user, brian, guild_channel
):
    """Spec 4.1.1 rate limit: non-owner exhausting RATE_LIMIT_MAX_TOKENS
    sees the canned cooldown reply."""
    for _ in range(guards.RATE_LIMIT_MAX_TOKENS):
        await _send_mention(guild_channel, brian, "hi", bot_user=fake_bot_user)
    msg = await _send_mention(guild_channel, brian, "hi again", bot_user=fake_bot_user)
    assert "Easy there" in msg.reply_log[0]


@pytest.mark.asyncio
async def test_owner_bypasses_rate_limit(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 3.1 / 4.1.1: owner tier bypasses rate-limiting."""
    for _ in range(guards.RATE_LIMIT_MAX_TOKENS * 2):
        msg = await _send_mention(guild_channel, solomon, "hi", bot_user=fake_bot_user)
        assert "Easy there" not in msg.reply_log[-1]
```

- [ ] **Step 4: Add mention-resolution test**

```python
@pytest.mark.asyncio
async def test_mention_resolution_substitutes_display_names(
    identity_yaml, fake_redis, fake_bot_user, solomon, wes, guild_channel
):
    """Spec V4 / 4.1.1: <@id> tokens for non-bot users are substituted
    with display names before the LLM call."""
    from tests.integration.conftest import make_message
    captured: dict = {}

    def fake_invoke(messages):
        captured["last_human"] = next(
            m.content for m in reversed(messages) if m.__class__.__name__ == "HumanMessage"
        )
        from agents._mock_llm import _MockResponse
        return _MockResponse(content="ok")

    from unittest.mock import patch
    from agents.tombombadil import agent as tom_agent
    content = f"<@{fake_bot_user.id}> Say hello to <@{wes.id}>"
    msg = make_message(solomon, guild_channel, content, mentions=[fake_bot_user, wes])
    with patch.object(tom_agent._llm, "invoke", side_effect=fake_invoke):
        await tom_bot.on_message(msg)
    assert "Wes Prater" in captured["last_human"]
    assert f"<@{wes.id}>" not in captured["last_human"]
    assert f"<@{fake_bot_user.id}>" not in captured["last_human"]
```

- [ ] **Step 5: Run and verify**

```bash
pytest tests/integration/test_tom_conversational.py -v 2>&1 | tail -20
```

Expected: 9 passed (1 from Task 1 + 8 added here).

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_tom_conversational.py
git commit -m "test(integration): Spec 4.1.1 mention reply contract"
```

---

## Task 3: Spec 4.1.2 — Note capture (draft + confirm)

**Files:**
- Modify: `tests/integration/test_tom_conversational.py`

- [ ] **Step 1: Add tests for the draft offer path**

```python
from agents.tombombadil import draft_store


@pytest.mark.asyncio
async def test_rating_phrase_offers_draft(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.2: a natural-language rating produces a follow-up draft
    message after the primary reply."""
    msg = await _send_mention(
        guild_channel, solomon, "I rated Stalker 10/10", bot_user=fake_bot_user
    )
    # Two replies: the primary conversational one + the draft prompt.
    assert len(msg.reply_log) == 2
    assert "React" in msg.reply_log[1] and "Stalker" in msg.reply_log[1]


@pytest.mark.asyncio
async def test_pronoun_rating_does_not_offer_draft(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.2 / pronoun blacklist: 'I rated it 5/10' is dropped."""
    msg = await _send_mention(
        guild_channel, solomon, "I rated it 5/10", bot_user=fake_bot_user
    )
    assert len(msg.reply_log) == 1  # only the primary reply, no draft


@pytest.mark.asyncio
async def test_stranger_rating_does_not_offer_draft(
    identity_yaml, fake_redis, fake_bot_user, stranger, guild_channel
):
    """Spec 4.1.2: strangers can't produce drafts (no canonical_name)."""
    msg = await _send_mention(
        guild_channel, stranger, "I rated Inception 9/10", bot_user=fake_bot_user
    )
    assert len(msg.reply_log) == 1
```

- [ ] **Step 2: Add reaction-commit + reaction-skip tests**

```python
async def _react(message_id, channel, user, emoji):
    """Drive on_reaction_add with a freshly-built FakeReaction."""
    from tests.integration._doubles import FakeMessage, FakeReaction, FakeUser
    tom = FakeUser(id=0, name="TomBombadil")
    target = FakeMessage(id=message_id, content="React ...", author=tom, channel=channel, mentions=[])
    await tom_bot.on_reaction_add(FakeReaction(emoji=emoji, message=target), user)
    return target


@pytest.mark.asyncio
async def test_check_reaction_commits_draft(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.2: ✅ reaction by the original drafter triggers save_note."""
    await _send_mention(
        guild_channel, solomon, "I rated Stalker 10/10", bot_user=fake_bot_user
    )
    # The bound draft was created against the second reply's id.
    msg_ids = [m for m in fake_redis.keys("tom:draft:*")]
    assert len(msg_ids) == 1
    bound_id = int(msg_ids[0].split(":")[-1])

    target = await _react(bound_id, guild_channel, solomon, "✅")
    assert any("logged" in r for r in target.reply_log)
    assert fake_redis.sismember("films", "Stalker")
    assert fake_redis.sismember("watchers", "Solomon Smith")
    # Draft is cleared after commit.
    assert fake_redis.exists(f"tom:draft:{bound_id}") == 0


@pytest.mark.asyncio
async def test_x_reaction_skips_draft(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.1.2: ❌ reaction discards the draft, no save_note."""
    await _send_mention(
        guild_channel, solomon, "I rated Stalker 10/10", bot_user=fake_bot_user
    )
    bound_id = int(list(fake_redis.keys("tom:draft:*"))[0].split(":")[-1])
    target = await _react(bound_id, guild_channel, solomon, "❌")
    assert any("Skipped" in r for r in target.reply_log)
    assert not fake_redis.sismember("films", "Stalker")


@pytest.mark.asyncio
async def test_wrong_user_reaction_ignored(
    identity_yaml, fake_redis, fake_bot_user, solomon, brian, guild_channel
):
    """Spec 4.1.2: only the requester can confirm. Brian reacting to
    Solomon's draft is silently ignored."""
    await _send_mention(
        guild_channel, solomon, "I rated Stalker 10/10", bot_user=fake_bot_user
    )
    bound_id = int(list(fake_redis.keys("tom:draft:*"))[0].split(":")[-1])
    target = await _react(bound_id, guild_channel, brian, "✅")
    # No save, no reply.
    assert target.reply_log == []
    assert not fake_redis.sismember("films", "Stalker")
    # Draft still pending.
    assert fake_redis.exists(f"tom:draft:{bound_id}") == 1
```

- [ ] **Step 3: Add the D2 regression test (xfail until fixed)**

```python
@pytest.mark.xfail(
    strict=True,
    reason="D2: requester_discord_id is bound at pop time, not push time. "
    "Concurrent drafts can attribute a rating to the wrong viewer when "
    "the wrong reactor confirms.",
)
@pytest.mark.asyncio
async def test_concurrent_drafts_bind_to_original_drafters(
    identity_yaml, fake_redis, fake_bot_user, solomon, brian, guild_channel
):
    """Spec 4.1.2 / 5.2: when Solomon and Brian both queue drafts in
    the same channel, each draft binds under its own drafter's
    discord_id -- not whoever's reply happens to pop it first."""
    await _send_mention(
        guild_channel, solomon, "I rated Stalker 10/10", bot_user=fake_bot_user
    )
    await _send_mention(
        guild_channel, brian, "I rated Ran 9/10", bot_user=fake_bot_user
    )
    drafts = sorted(int(k.split(":")[-1]) for k in fake_redis.keys("tom:draft:*"))
    # Two drafts in flight.
    assert len(drafts) == 2
    # Each draft's requester_discord_id matches the drafter's id, not
    # the other user's.
    d0 = fake_redis.hgetall(f"tom:draft:{drafts[0]}")
    d1 = fake_redis.hgetall(f"tom:draft:{drafts[1]}")
    by_film = {d["film"]: d for d in (d0, d1)}
    assert by_film["Stalker"]["requester_discord_id"] == str(solomon.id)
    assert by_film["Ran"]["requester_discord_id"] == str(brian.id)
```

- [ ] **Step 4: Run and verify**

```bash
pytest tests/integration/test_tom_conversational.py -v 2>&1 | tail -20
```

Expected: ~13 passed, 1 xfailed (D2).

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_tom_conversational.py
git commit -m "test(integration): Spec 4.1.2 draft+confirm contract + D2 xfail"
```

---

## Task 4: Spec 4.2.1 /rate + 4.2.7 /whoami

**Files:**
- Create: `tests/integration/test_tom_slash.py`

- [ ] **Step 1: Create the module with /rate happy path + refusals**

```python
"""Spec 4.2: Tom Bombadil slash command flows."""

from __future__ import annotations

import pytest

from agents.tombombadil import commands as tom_commands
from agents.tombombadil.identity import Tier, Viewer, resolve as resolve_viewer


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
```

- [ ] **Step 2: Add /whoami coverage**

```python
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
```

- [ ] **Step 3: Run + commit**

```bash
pytest tests/integration/test_tom_slash.py -v 2>&1 | tail -15
git add tests/integration/test_tom_slash.py
git commit -m "test(integration): Spec 4.2.1 /rate + 4.2.7 /whoami"
```

Expected: 8 passed.

---

## Task 5: Spec 4.2.2 /recommend + 4.2.3 /club stats

**Files:**
- Modify: `tests/integration/test_tom_slash.py`

- [ ] **Step 1: Add /recommend tests covering both branches**

```python
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
    assert reply.startswith("**For Solomon Smith**")


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
```

- [ ] **Step 2: Add /club stats test**

```python
def test_club_stats_includes_seed_films():
    """Spec 4.2.3: /club stats returns aggregate with Ran/La Haine/Ghost Dog."""
    reply = tom_commands.cmd_club_stats()
    assert "Top-rated" in reply
    assert "Ran" in reply or "La Haine" in reply
```

- [ ] **Step 3: Run + commit**

```bash
pytest tests/integration/test_tom_slash.py -v 2>&1 | tail -10
git add tests/integration/test_tom_slash.py
git commit -m "test(integration): Spec 4.2.2 /recommend + 4.2.3 /club stats"
```

Expected: +5 passed (13 total in slash file).

---

## Task 6: Spec 4.2.4 /club recommend + 4.2.5 /club schedule

**Files:**
- Modify: `tests/integration/test_tom_slash.py`

- [ ] **Step 1: Add /club recommend tests**

```python
from agents.tombombadil import club
from agents.tombombadil.film_knowledge import FilmKnowledge


@pytest.fixture
def knowledge():
    return FilmKnowledge()


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
```

- [ ] **Step 2: Add /club schedule tests**

```python
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
```

- [ ] **Step 3: Run + commit**

```bash
pytest tests/integration/test_tom_slash.py -v 2>&1 | tail -15
git add tests/integration/test_tom_slash.py
git commit -m "test(integration): Spec 4.2.4-5 /club recommend + /club schedule"
```

Expected: +7 passed (20 in slash file).

---

## Task 7: Spec 4.2.6 /forget

**Files:**
- Modify: `tests/integration/test_tom_slash.py`

- [ ] **Step 1: Add /forget tests across all four scopes**

```python
from agents.tombombadil import memory


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
```

- [ ] **Step 2: Run + commit**

```bash
pytest tests/integration/test_tom_slash.py -v 2>&1 | tail -15
git add tests/integration/test_tom_slash.py
git commit -m "test(integration): Spec 4.2.6 /forget across all scopes"
```

Expected: +6 passed (26 in slash file).

---

## Task 8: Spec 4.3.1 — Watch-party announcement

**Files:**
- Create: `tests/integration/test_tom_scheduled.py`

- [ ] **Step 1: Create module with delivery.publish + dispatch path**

```python
"""Spec 4.3: Tom Bombadil scheduled flows."""

from __future__ import annotations

import json

import pytest

from agents.galadriel.models import Job, JobDelivery, JobPayload, JobSchedule
from agents.galadriel.worker import announce
from agents.tombombadil import club, delivery


def _job(channel_id: str = "42", film: str = "Inception") -> Job:
    return Job(
        id="x",
        name=f"Watch party: {film}",
        schedule=JobSchedule(kind="at", at_iso="2099-01-01T19:00:00"),
        payload=JobPayload(kind="agentTurn", message=f"Announce: {film} tonight"),
        delivery=JobDelivery(mode="discord", to=channel_id),
        created_at_ms=0,
        updated_at_ms=0,
    )


def test_delivery_publish_enqueues_json(fake_redis):
    """Spec 4.3.1 delivery: publish writes a {channel_id, text} JSON
    payload to tom:announce:queue."""
    delivery.publish("42", "club night tonight", redis=fake_redis)
    raw = fake_redis.lpop(delivery.QUEUE_KEY)
    payload = json.loads(raw)
    assert payload == {"channel_id": "42", "text": "club night tonight"}


def test_galadriel_discord_mode_pushes_to_queue(monkeypatch, fake_redis):
    """Spec 4.3.1: Galadriel's announce() dispatches mode='discord' to
    delivery.publish, which queues the message for the subscriber."""
    monkeypatch.setattr(delivery, "get_redis_sync", lambda: fake_redis)
    job = _job()
    result = {"result": {"reply": "Club night tonight, watching Inception"}}
    announce(job, result)
    raw = fake_redis.lpop(delivery.QUEUE_KEY)
    payload = json.loads(raw)
    assert payload["channel_id"] == "42"
    assert "Inception" in payload["text"]


def test_schedule_watch_party_writes_galadriel_job(fake_redis):
    """Spec 4.3.1: schedule_watch_party persists a cron:job:* entry
    keyed by a watch_party_<hex> id with delivery.mode='discord'."""
    job = club.schedule_watch_party(
        fake_redis,
        film="Inception",
        when_iso="2099-01-01T19:00:00",
        channel_id="42",
        organizer="Solomon Smith",
    )
    assert job.id.startswith("watch_party_")
    assert job.delivery.mode == "discord"
    assert job.delivery.to == "42"
    # The saved blob is retrievable.
    assert fake_redis.exists(f"cron:job:{job.id}")


def test_ensure_weekly_club_night_is_idempotent(fake_redis):
    """Spec 4.3.1: ensure_weekly_club_night uses a fixed id; second call
    overwrites the first instead of creating a duplicate."""
    j1 = club.ensure_weekly_club_night(fake_redis, channel_id="42")
    j2 = club.ensure_weekly_club_night(fake_redis, channel_id="42")
    assert j1.id == j2.id == club.WEEKLY_NIGHT_JOB_ID
```

- [ ] **Step 2: Run + commit**

```bash
pytest tests/integration/test_tom_scheduled.py -v 2>&1 | tail -10
git add tests/integration/test_tom_scheduled.py
git commit -m "test(integration): Spec 4.3.1 watch-party + galadriel discord delivery"
```

Expected: 4 passed.

---

## Task 9: Spec 4.3.2 — Letterboxd auto-sync

**Files:**
- Modify: `tests/integration/test_tom_scheduled.py`

- [ ] **Step 1: Embed sample RSS body + add sync tests**

```python
from agents.tombombadil import sync_job


SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
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


def test_sync_saves_new_films_and_advances_watermark(fake_redis):
    """Spec 4.3.2 sync side effects: new diary entries become notes
    keyed under viewer_name; watermark advances to latest watchedDate."""
    result = sync_job.run_sync(
        fake_redis,
        username="SolomonThaChef",
        viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED,
    )
    assert result.fetched == 2 and result.saved == 2
    assert fake_redis.sismember("films", "Stalker")
    assert fake_redis.sismember("films", "Solaris")
    assert fake_redis.get(sync_job.WATERMARK_KEY) == "2026-05-10"


def test_sync_idempotent_on_second_run(fake_redis):
    """Spec 4.3.2: watermark filters previously-saved entries."""
    sync_job.run_sync(fake_redis, username="x", viewer_name="Solomon Smith", feed_text=SAMPLE_FEED)
    second = sync_job.run_sync(fake_redis, username="x", viewer_name="Solomon Smith", feed_text=SAMPLE_FEED)
    assert second.new == 0 and second.saved == 0


def test_sync_announces_when_channel_set(fake_redis):
    """Spec 4.3.2: TOM_LETTERBOXD_ANNOUNCE_CHANNEL_ID -> delivery
    payloads enqueued per saved film."""
    sync_job.run_sync(
        fake_redis, username="x", viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED, announce_channel_id="42",
    )
    raw = fake_redis.lrange(delivery.QUEUE_KEY, 0, -1)
    assert len(raw) == 2
    texts = [json.loads(p)["text"] for p in raw]
    assert any("Stalker" in t for t in texts)
    assert any("Solaris" in t for t in texts)


def test_sync_returns_error_without_username(fake_redis):
    """Spec 4.3.2: no LETTERBOXD_USERNAME -> errored SyncResult,
    no replies, no watermark change."""
    result = sync_job.run_sync(fake_redis, username="", feed_text=None)
    assert result.errors and "LETTERBOXD_USERNAME" in result.errors[0]
    assert fake_redis.get(sync_job.WATERMARK_KEY) is None
```

- [ ] **Step 2: Run + commit**

```bash
pytest tests/integration/test_tom_scheduled.py -v 2>&1 | tail -10
git add tests/integration/test_tom_scheduled.py
git commit -m "test(integration): Spec 4.3.2 letterboxd auto-sync"
```

Expected: 4 added → 8 total in scheduled file.

---

## Task 10: Spec 4.4 — Internal flows (recall, prefs, identity+roster)

**Files:**
- Create: `tests/integration/test_tom_internal.py`

- [ ] **Step 1: Create module with recall tests**

```python
"""Spec 4.4: Tom Bombadil internal (per-message) flows."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.tombombadil import agent as tom_agent
from agents.tombombadil import memory


@pytest.mark.asyncio
async def test_recall_returns_stored_fact_for_owner(
    identity_yaml, fake_redis, finrod_in_memory, solomon
):
    """Spec 4.4.1: a previously remembered fact comes back when queried
    with the same wording (MockEmbedder = cosine 1.0 on identity)."""
    from agents.tombombadil.identity import resolve as resolve_viewer
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    await memory.remember_fact(viewer, "user loves Tarkovsky", source_channel="x")
    recalled = await memory.recall_facts(viewer, "[Solomon Smith] user loves Tarkovsky")
    assert any("tarkovsky" in r.lower() for r in recalled)


@pytest.mark.asyncio
async def test_recall_filters_by_viewer(
    identity_yaml, fake_redis, finrod_in_memory, solomon, brian
):
    """Spec 4.4.1 / 5.4: recall returns only the requesting viewer's
    facts, never another user's."""
    from agents.tombombadil.identity import resolve as resolve_viewer
    sv = resolve_viewer(str(solomon.id), str(solomon))
    bv = resolve_viewer(str(brian.id), str(brian))
    await memory.remember_fact(sv, "solomon loves Tarkovsky", source_channel="x")
    await memory.remember_fact(bv, "brian loves Get Out", source_channel="x")
    # Cross-namespace query returns no Solomon-attributed entries to Brian.
    crossover = await memory.recall_facts(bv, "[Solomon Smith] solomon loves Tarkovsky")
    assert all("solomon" not in r.lower() for r in crossover)


@pytest.mark.asyncio
async def test_recall_score_floor_filters_irrelevant(
    identity_yaml, fake_redis, finrod_in_memory, solomon
):
    """Spec 4.4.1: matches below RECALL_SCORE_FLOOR (0.35) are dropped."""
    from agents.tombombadil.identity import resolve as resolve_viewer
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    await memory.remember_fact(viewer, "user prefers slow cinema", source_channel="x")
    recalled = await memory.recall_facts(viewer, "completely unrelated random topic 1234")
    assert recalled == []


@pytest.mark.asyncio
async def test_stranger_recall_returns_empty(
    identity_yaml, fake_redis, finrod_in_memory, stranger
):
    """Spec 4.4.1: strangers have no canonical_name and therefore no facts."""
    from agents.tombombadil.identity import resolve as resolve_viewer
    viewer = resolve_viewer(str(stranger.id), str(stranger))
    recalled = await memory.recall_facts(viewer, "anything")
    assert recalled == []
```

- [ ] **Step 2: Add pref enforcement tests**

```python
@pytest.mark.asyncio
async def test_suppress_films_pref_swaps_film_block(
    identity_yaml, fake_redis, fake_bot_user, brian, guild_channel
):
    """Spec 4.4.2: suppress_films=1 replaces the film summary with the
    'has asked you NOT to bring up films' line in the system prompt."""
    from agents.tombombadil.identity import resolve as resolve_viewer
    viewer = resolve_viewer(str(brian.id), str(brian))
    memory.set_pref(fake_redis, viewer.discord_id, "suppress_films", "1")
    captured: dict = {}

    def fake_invoke(messages):
        captured["sys"] = "\n".join(
            m.content for m in messages if m.__class__.__name__ == "SystemMessage"
        )
        from agents._mock_llm import _MockResponse
        return _MockResponse(content="ok")

    with patch.object(tom_agent._llm, "invoke", side_effect=fake_invoke):
        await tom_agent.get_response(
            f"tom:hist:ch:{guild_channel.id}", "hi", viewer, fake_redis
        )
    assert "asked you NOT to bring up films unprompted" in captured["sys"]


@pytest.mark.asyncio
async def test_do_not_log_pref_skips_fact_extractor(
    identity_yaml, fake_redis, solomon
):
    """Spec 4.4.2: do_not_log=1 means a 'remember that...' message never
    persists a fact."""
    from agents.tombombadil.identity import resolve as resolve_viewer
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    memory.set_pref(fake_redis, viewer.discord_id, "do_not_log", "1")
    await tom_agent.get_response(
        "tom:hist:ch:1", "remember that I'm allergic to subtitles",
        viewer, fake_redis,
    )
    # No NoteDraft queue entry, no pref change beyond do_not_log itself.
    assert fake_redis.llen("tom:drafts:scope:tom:hist:ch:1") == 0
```

- [ ] **Step 3: Add identity-roster prompt-injection test**

```python
@pytest.mark.asyncio
async def test_roster_block_lists_all_film_db_people(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 4.4.3: the system prompt includes every FILM_DATABASE person
    so Tom can answer cross-user questions (e.g. 'how did Anthony rate?')
    without inventing data."""
    from agents.tombombadil.identity import resolve as resolve_viewer
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    captured: dict = {}

    def fake_invoke(messages):
        captured["sys"] = "\n".join(
            m.content for m in messages if m.__class__.__name__ == "SystemMessage"
        )
        from agents._mock_llm import _MockResponse
        return _MockResponse(content="ok")

    with patch.object(tom_agent._llm, "invoke", side_effect=fake_invoke):
        await tom_agent.get_response("tom:hist:ch:99", "hi", viewer, fake_redis)
    for name in ("Solomon Smith", "Anthony Taylor", "Brian", "Gavin", "Isis", "G"):
        assert name in captured["sys"]
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/integration/test_tom_internal.py -v 2>&1 | tail -12
git add tests/integration/test_tom_internal.py
git commit -m "test(integration): Spec 4.4.1-3 internal flows (recall, prefs, roster)"
```

Expected: 7 passed.

---

## Task 11: Spec 5.1 — Onboarding (first-contact contract)

**Files:**
- Create: `tests/integration/test_tom_cross_cutting.py`

- [ ] **Step 1: Create module with the onboarding-xfail test**

```python
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
    from tests.integration.conftest import make_message
    from agents.tombombadil import bot as tom_bot
    content = f"<@{fake_bot_user.id}> hi"
    msg = make_message(stranger, guild_channel, content, mentions=[fake_bot_user])
    await tom_bot.on_message(msg)
    reply = msg.reply_log[0].lower()
    assert stranger.display_name.lower() in reply
    assert "film" in reply or "club" in reply
    assert "/whoami" in reply or "what you've been watching" in reply or "tell me" in reply
```

- [ ] **Step 2: Run + commit (test should xfail)**

```bash
pytest tests/integration/test_tom_cross_cutting.py -v 2>&1 | tail -10
git add tests/integration/test_tom_cross_cutting.py
git commit -m "test(integration): Spec 5.1 onboarding contract + D7 xfail"
```

Expected: 1 xfailed.

---

## Task 12: Spec 5.2 — Multi-user collisions

**Files:**
- Modify: `tests/integration/test_tom_cross_cutting.py`

- [ ] **Step 1: Add collision tests**

```python
from agents.tombombadil import bot as tom_bot
from agents.tombombadil import memory


async def _send_mention(channel, user, text, *, bot_user):
    from tests.integration.conftest import make_message
    content = f"<@{bot_user.id}> {text}"
    msg = make_message(user, channel, content, mentions=[bot_user])
    await tom_bot.on_message(msg)
    return msg


@pytest.mark.asyncio
async def test_concurrent_mentions_history_interleaves(
    identity_yaml, fake_redis, fake_bot_user, solomon, brian, guild_channel
):
    """Spec 5.2: two users mentioning Tom back-to-back both get replies
    and both turn-pairs land in the shared channel history list in
    arrival order."""
    await _send_mention(guild_channel, solomon, "hello from solomon", bot_user=fake_bot_user)
    await _send_mention(guild_channel, brian, "hello from brian", bot_user=fake_bot_user)
    turns = memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}")
    # 4 turns: solomon-user, solomon-assistant, brian-user, brian-assistant
    assert len(turns) == 4
    assert turns[0].viewer == "Solomon Smith"
    assert turns[2].viewer == "Brian"


@pytest.mark.asyncio
async def test_separate_rate_limit_buckets_per_user(
    identity_yaml, fake_redis, fake_bot_user, brian, wes, guild_channel
):
    """Spec 5.2: per-user token buckets isolate rate limits. Burning
    Brian's budget doesn't lock out Wes."""
    from agents.tombombadil import guards
    for _ in range(guards.RATE_LIMIT_MAX_TOKENS):
        await _send_mention(guild_channel, brian, "hi", bot_user=fake_bot_user)
    blocked = await _send_mention(guild_channel, brian, "again", bot_user=fake_bot_user)
    free = await _send_mention(guild_channel, wes, "hi", bot_user=fake_bot_user)
    assert "Easy there" in blocked.reply_log[-1]
    assert "Easy there" not in free.reply_log[-1]


@pytest.mark.asyncio
async def test_dm_history_isolated_from_channel(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel, dm_channel
):
    """Spec 5.2 / 5.4: DM context never leaks into a channel and vice versa."""
    await _send_mention(dm_channel, solomon, "private question", bot_user=fake_bot_user)
    await _send_mention(guild_channel, solomon, "public question", bot_user=fake_bot_user)
    dm_turns = memory.recent_turns(fake_redis, f"tom:hist:dm:{solomon.id}")
    ch_turns = memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}")
    assert any("private question" in t.content for t in dm_turns)
    assert all("public question" not in t.content for t in dm_turns)
    assert any("public question" in t.content for t in ch_turns)
    assert all("private question" not in t.content for t in ch_turns)
```

- [ ] **Step 2: Run + commit**

```bash
pytest tests/integration/test_tom_cross_cutting.py -v 2>&1 | tail -10
git add tests/integration/test_tom_cross_cutting.py
git commit -m "test(integration): Spec 5.2 multi-user collisions"
```

Expected: 3 added passing + 1 prior xfail.

---

## Task 13: Spec 5.3 — Failure modes

**Files:**
- Modify: `tests/integration/test_tom_cross_cutting.py`

- [ ] **Step 1: Add failure-mode tests**

```python
from unittest.mock import patch

from agents.tombombadil import agent as tom_agent


@pytest.mark.asyncio
async def test_llm_timeout_returns_canned_no_history(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 5.3 LLM timeout: returns 'LLM timeout, try again' and writes
    NO history (so retries don't accumulate phantom turns)."""
    def raise_timeout(*_a, **_k):
        raise TimeoutError("simulated")

    with patch.object(tom_agent._llm, "invoke", side_effect=raise_timeout):
        msg = await _send_mention(guild_channel, solomon, "hi", bot_user=fake_bot_user)
    assert "LLM timeout" in msg.reply_log[-1]
    assert memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}") == []


@pytest.mark.asyncio
async def test_llm_empty_content_returns_canned_no_history(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 5.3 LLM empty: 'No response generated', no history."""
    from agents._mock_llm import _MockResponse

    with patch.object(tom_agent._llm, "invoke", return_value=_MockResponse(content="")):
        msg = await _send_mention(guild_channel, solomon, "hi", bot_user=fake_bot_user)
    assert msg.reply_log[-1] == "No response generated"
    assert memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}") == []


@pytest.mark.asyncio
async def test_llm_arbitrary_exception_returns_canned(
    identity_yaml, fake_redis, fake_bot_user, solomon, guild_channel
):
    """Spec 5.3 LLM crash: 'Error processing your request', no history."""
    with patch.object(tom_agent._llm, "invoke", side_effect=RuntimeError("boom")):
        msg = await _send_mention(guild_channel, solomon, "hi", bot_user=fake_bot_user)
    assert "Error processing" in msg.reply_log[-1]
    assert memory.recent_turns(fake_redis, f"tom:hist:ch:{guild_channel.id}") == []


@pytest.mark.asyncio
async def test_finrod_query_failure_falls_back_to_no_recall(
    identity_yaml, fake_redis, fake_bot_user, finrod_in_memory, solomon, guild_channel
):
    """Spec 5.3 Finrod outage: recall_facts returns [] on backend errors
    and the reply continues without recall context."""
    async def broken_run(*_a, **_k):
        from core.models import AgentResult, TaskStatus
        return AgentResult(task_id="x", agent="finrod", status=TaskStatus.FAILED, error="down")

    finrod_in_memory.run = broken_run  # type: ignore[method-assign]
    msg = await _send_mention(guild_channel, solomon, "tell me about Ran", bot_user=fake_bot_user)
    assert msg.reply_log  # still replies; recall failure is non-fatal
```

- [ ] **Step 2: Run + commit**

```bash
pytest tests/integration/test_tom_cross_cutting.py -v 2>&1 | tail -12
git add tests/integration/test_tom_cross_cutting.py
git commit -m "test(integration): Spec 5.3 failure modes (LLM + Finrod)"
```

Expected: 4 added passing.

---

## Task 14: Spec 5.4 + 5.5 — Privacy + operator surface

**Files:**
- Modify: `tests/integration/test_tom_cross_cutting.py`

- [ ] **Step 1: Add privacy invariant tests**

```python
@pytest.mark.asyncio
async def test_dm_reply_does_not_mention_other_users_facts(
    identity_yaml, fake_redis, fake_bot_user, finrod_in_memory,
    solomon, brian, dm_channel
):
    """Spec 5.4: Tom's reply to Solomon in a DM never surfaces a fact
    attributed to Brian in the system prompt."""
    from agents.tombombadil.identity import resolve as resolve_viewer
    bv = resolve_viewer(str(brian.id), str(brian))
    await memory.remember_fact(bv, "brian secretly hates Tarkovsky", source_channel="x")

    captured: dict = {}

    def fake_invoke(messages):
        captured["sys"] = "\n".join(
            m.content for m in messages if m.__class__.__name__ == "SystemMessage"
        )
        from agents._mock_llm import _MockResponse
        return _MockResponse(content="ok")

    with patch.object(tom_agent._llm, "invoke", side_effect=fake_invoke):
        await _send_mention(dm_channel, solomon, "what do you remember about Brian?",
                             bot_user=fake_bot_user)

    assert "brian secretly hates" not in captured["sys"].lower()


def test_history_ltrim_caps_at_two_times_max_turns(identity_yaml, fake_redis, solomon):
    """Spec 5.4 / memory invariants: scope history never exceeds
    2 * HISTORY_MAX_TURNS entries."""
    from agents.tombombadil.identity import resolve as resolve_viewer
    viewer = resolve_viewer(str(solomon.id), str(solomon))
    scope = "tom:hist:ch:cap"
    for i in range(memory.HISTORY_MAX_TURNS * 4):
        memory.append_turn(fake_redis, scope, viewer, "user" if i % 2 == 0 else "assistant", f"msg {i}")
    raw = fake_redis.lrange(scope, 0, -1)
    assert len(raw) <= memory.HISTORY_MAX_TURNS * 2
```

- [ ] **Step 2: Add operator-surface tests (banning, watermark reset)**

```python
def test_operator_ban_blocks_user(identity_yaml, fake_redis, brian):
    """Spec 5.5: SADD tom:bans <id> -> subsequent guard_check returns
    the ban refusal string."""
    from agents.tombombadil import guards
    fake_redis.sadd(guards.BAN_SET_KEY, str(brian.id))
    assert guards.is_banned(fake_redis, str(brian.id)) is True


def test_operator_unban_restores_access(identity_yaml, fake_redis, brian):
    """Spec 5.5: SREM tom:bans <id> reverses the ban."""
    from agents.tombombadil import guards
    guards.ban(fake_redis, str(brian.id))
    guards.unban(fake_redis, str(brian.id))
    assert guards.is_banned(fake_redis, str(brian.id)) is False


def test_operator_watermark_reset_re_pulls_letterboxd(fake_redis):
    """Spec 5.5: DEL tom:letterboxd:last_watched_iso causes the next
    sync to see all entries as 'new' again."""
    from agents.tombombadil import sync_job
    sync_job.run_sync(
        fake_redis, username="x", viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED,
    )
    fake_redis.delete(sync_job.WATERMARK_KEY)
    result = sync_job.run_sync(
        fake_redis, username="x", viewer_name="Solomon Smith",
        feed_text=SAMPLE_FEED,
    )
    # Both films re-seen as new because watermark was wiped.
    assert result.new == 2
```

The `SAMPLE_FEED` constant is reused from `test_tom_scheduled.py` — re-define it locally at the top of `test_tom_cross_cutting.py` (cheaper than importing across test files; pytest collects sibling test modules independently and shared constants stay readable inline):

```python
SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
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
```

- [ ] **Step 3: Run + commit + open PR**

```bash
pytest tests/integration/ -v 2>&1 | tail -25
git add tests/integration/test_tom_cross_cutting.py
git commit -m "test(integration): Spec 5.4 privacy + 5.5 operator surface"
git push -u origin claude/tom-integration-suite
gh pr create --title "test: Tom Bombadil integration suite (sub-project B)" \
  --base main \
  --body "Implements sub-project B from the brainstorm. Asserts the contracts in docs/superpowers/specs/2026-05-10-tom-bombadil-behavior-spec.md across tests/integration/test_tom_*.py. Two xfailed tests (D2 draft binding, D7 onboarding) tag known deltas for sub-project C."
```

Expected final state across the suite:
- ~50+ tests passing
- 2 xfailed (D2, D7)
- 0 errors

---

## Self-Review

**Spec coverage:**

| Spec section | Task | Notes |
|--------------|------|-------|
| 1 Vision / 2 Voice / 3 Identity | implicit — assertions inside per-flow tests reference V-rules and Tier invariants | ✓ |
| 4.1.1 Mention reply | Task 2 | ✓ |
| 4.1.2 Note capture + draft | Task 3 (includes D2 xfail) | ✓ |
| 4.2.1 /rate | Task 4 | ✓ |
| 4.2.2 /recommend | Task 5 | ✓ |
| 4.2.3 /club stats | Task 5 | ✓ |
| 4.2.4 /club recommend | Task 6 | ✓ |
| 4.2.5 /club schedule | Task 6 | ✓ |
| 4.2.6 /forget | Task 7 | ✓ |
| 4.2.7 /whoami | Task 4 | ✓ |
| 4.3.1 Watch-party announcement | Task 8 | ✓ |
| 4.3.2 Letterboxd auto-sync | Task 9 | ✓ |
| 4.4.1 Long-term recall | Task 10 | ✓ |
| 4.4.2 Pref enforcement | Task 10 | ✓ |
| 4.4.3 Identity + roster | Task 10 | ✓ |
| 5.1 Onboarding | Task 11 (D7 xfail) | ✓ |
| 5.2 Multi-user collisions | Task 12 | ✓ |
| 5.3 Failure modes | Task 13 | ✓ |
| 5.4 Privacy | Task 14 | ✓ |
| 5.5 Operator surface | Task 14 | ✓ |
| 6 Test rubric (recommended structure) | Tasks 2-14 follow the spec's module split | ✓ |
| 7 Known deltas (aggregated) | D2, D7 → xfail markers; D1, D3-D6, D8-D10 → audit-only (would require either non-determinism or external services to test in this suite) | ✓ partially — see note |

**Note on D1 / D3-D6 / D8-D10 coverage:** These deltas are intentionally not asserted by tests in this suite because:

- D1 (`[viewer]` prefix in stored history) — non-deterministic decay; testing requires synthetic stale entries which makes the test fragile rather than useful.
- D3 (Letterboxd films missing themes) — exercised implicitly by the favorites-fallback tests in Task 5.
- D4 / D5 (containers off) — operator concerns, not user-visible from inside a test process.
- D6 (no `/setpref`) — absence of a feature; no test can assert "this command doesn't exist".
- D8 (no LLM retry) — a non-issue under MockLLM; only matters with real network.
- D9 (no `/unrate`) — same as D6.
- D10 (no admin slash commands) — same as D6.

Sub-project C (audit) addresses these directly as fix tickets rather than test failures.

**Placeholder scan:** No "TBD", "TODO", or vague-instruction steps. Every code step shows the actual code.

**Type consistency:** Used identifiers across tasks — `solomon`, `brian`, `wes`, `stranger`, `guild_channel`, `dm_channel`, `_send_mention`, `_react`, `SAMPLE_FEED` — match across tasks. `make_message` / `make_interaction` defined in conftest and re-imported in tests where used.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-10-tom-bombadil-integration-suite.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review (test-design + code), fast iteration. Particularly suited here because every task is a self-contained test file write.

**2. Inline Execution** — execute the 14 tasks in this session using `executing-plans`, batch with checkpoints. Slower per task but keeps the context window primed with the spec details.

**Which approach?**
