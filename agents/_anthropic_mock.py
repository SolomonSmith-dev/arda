"""Deterministic mock for `anthropic.AsyncAnthropic` used by the Sauron
LangGraph orchestrator in tests and dev mode (USE_MOCK_LLM=true).

Mirrors the keyword classes in `agents/sauron/planner.py` so existing
e2e assertions (intent -> earendil/finrod/tombombadil) continue to hold
when routing flows through the StateGraph + tool_use loop instead of
the legacy keyword planner.

Only implements the subset of the AsyncAnthropic surface the graph
uses: `client.messages.create(model, system, tools, messages, max_tokens)`
returning an object with `content: list[block]` and `stop_reason: str`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

# Keyword classes mirror agents/sauron/planner.py. Order matters: film
# is checked first so "watched" / "kurosawa" win over generic verbs.
_FILM_KEYWORDS = (
    "film:", "rating:", "movie", "film", "letterboxd", "tmdb",
    "ran ", "la haine", "ghost dog", "watched", "watch",
    "recommend", "kurosawa",
)
_KNOWLEDGE_KEYWORDS = (
    "what is", "what are", "explain", "summarize", "tell me about",
    "describe", "search", "find ", "lookup", "look up",
    "according to", "documentation", "docs",
    "memory", "remember", "recall",
)
_SHELL_KEYWORDS = (
    "uptime", "df ", "free ", "whoami", "pwd", "ls ", "echo ",
    "system status", "disk", "memory", "process", "kill ",
    "run ", "execute ", "shell", "command",
)


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class MockMessage:
    content: list[Any]
    stop_reason: str
    id: str = "msg_mock"
    role: str = "assistant"
    model: str = "mock"


def _classify(text: str) -> tuple[str, dict]:
    """Mirror planner.classify -> (tool_name, tool_input)."""
    msg = text.lower().strip()
    if any(k in msg for k in _FILM_KEYWORDS):
        return "tombombadil_chat", {"message": text}
    if any(k in msg for k in _KNOWLEDGE_KEYWORDS):
        return "finrod_query", {"question": text}
    if any(k in msg for k in _SHELL_KEYWORDS):
        return "earendil_execute", {"message": text}
    return "earendil_execute", {"message": text}


def _latest_user(messages: list[dict]) -> dict | None:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m
    return None


def _has_tool_result(message: dict) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def _extract_user_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                return b.get("text", "")
    return ""


class _MockMessages:
    def __init__(self, parent: MockAnthropicClient):
        self._parent = parent

    async def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        stop_sequences: list[str] | None = None,
        timeout: float | None = None,
    ) -> MockMessage:
        """Signature is a strict subset of the real ``AsyncMessages.create``.

        This used to be ``create(self, *, messages, **kwargs)``, which named
        only ``messages`` and swallowed model, system, tools and max_tokens
        wholesale -- so it could not have caught a misspelled parameter, let
        alone one the SDK removed. That is exactly how ``temperature=0.2``
        reached production on the Tom path and broke every reply (the SDK
        dropped it in 1.x). Add a parameter here only after confirming the
        installed SDK accepts it.
        """
        self._parent.calls.append(
            {
                "model": model,
                "system": system,
                "messages": messages,
                "max_tokens": max_tokens,
                "tools": tools,
            }
        )

        latest = _latest_user(messages)

        # Second turn: we just returned a tool_result -> close the turn.
        if latest is not None and _has_tool_result(latest):
            return MockMessage(
                content=[TextBlock(text="[mock] tool_result observed; returning final answer.")],
                stop_reason="end_turn",
            )

        # First turn (or any subsequent plain user message): emit a tool_use.
        text = _extract_user_text(latest) if latest else ""
        tool_name, tool_input = _classify(text)
        return MockMessage(
            content=[ToolUseBlock(id=f"tu_{uuid4().hex[:8]}", name=tool_name, input=tool_input)],
            stop_reason="tool_use",
        )


@dataclass
class MockAnthropicClient:
    model: str = "mock"
    calls: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.messages = _MockMessages(self)


# --- Chat-mode mock (Tom Bombadil; no tool_use) ----------------------

class _MockChatMessages:
    """Tom Bombadil's only LLM mode: pure chat completion. Always
    returns a deterministic text block so tests can assert on the
    captured `system` and `messages` arguments without ever exercising
    a tool_use code path."""

    def __init__(self, parent: MockAnthropicChatClient):
        self._parent = parent

    async def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        stop_sequences: list[str] | None = None,
        timeout: float | None = None,
    ) -> MockMessage:
        """Deliberately does NOT accept ``**kwargs``.

        It used to, and that is how ``temperature=0.2`` shipped to production
        and broke every Tom reply: the anthropic SDK dropped ``temperature``
        from ``Messages.create`` in 1.x, but the mock swallowed it silently,
        so 400+ tests passed against a call the real client rejects with a
        TypeError. A mock that accepts more than the real client cannot fail
        on the mismatch it exists to model. Keep this signature a subset of
        the real one, and add parameters here only after confirming the
        installed SDK has them.
        """
        self._parent.calls.append(
            {
                "model": model,
                "system": system,
                "messages": messages,
                "max_tokens": max_tokens,
                "stop_sequences": stop_sequences,
                "timeout": timeout,
            }
        )
        latest = _latest_user(messages)
        echo = _extract_user_text(latest)[:120] if latest else ""
        return MockMessage(
            content=[TextBlock(text=f"[mock:{model}] {echo}")],
            stop_reason="end_turn",
            model=model,
        )


@dataclass
class MockAnthropicChatClient:
    """Mirrors the slice of `anthropic.AsyncAnthropic` Tom Bombadil
    uses: `await client.messages.create(model, system, messages,
    max_tokens)` returning a `MockMessage` whose `.content[0].text`
    echoes the latest user input with a `[mock:<model>]` marker."""

    model: str = "mock"
    calls: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.messages = _MockChatMessages(self)
