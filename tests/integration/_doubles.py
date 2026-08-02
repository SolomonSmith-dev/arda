"""Discord.py doubles for Tom Bombadil integration tests.

Minimal stand-ins for User, Channel, Message, Reaction, Interaction.
Each class exposes only the attributes / methods actually touched by
agents.tombombadil.bot and agents.tombombadil.commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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

    def mentioned_in(self, message: FakeMessage) -> bool:
        return self in message.mentions

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
        msg = FakeMessage(
            id=10_000_000 + len(self.sent),
            content=content,
            author=FakeUser(id=0, name="TomBombadil"),
            channel=self,
            mentions=[],
        )
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
class FakeFollowup:
    """Stand-in for ``interaction.followup`` (used by ``/sync`` after defer)."""

    sent: list[tuple[str, bool]] = field(default_factory=list)

    async def send(self, content: str, ephemeral: bool = False):
        self.sent.append((content, ephemeral))


@dataclass
class FakeResponse:
    sent: list[tuple[str, bool]] = field(default_factory=list)
    deferred: bool = False
    deferred_ephemeral: bool = False

    async def send_message(self, content: str, ephemeral: bool = False):
        self.sent.append((content, ephemeral))

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False):
        self.deferred = True
        self.deferred_ephemeral = ephemeral


@dataclass
class FakeInteraction:
    user: FakeUser
    channel_id: int
    channel: FakeChannel
    response: FakeResponse = field(default_factory=FakeResponse)
    followup: FakeFollowup = field(default_factory=FakeFollowup)
