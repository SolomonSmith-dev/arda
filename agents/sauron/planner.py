from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Specialist = Literal["earendil", "finrod", "tombombadil"]


@dataclass(frozen=True)
class Subtask:
    specialist: Specialist
    payload: dict


@dataclass(frozen=True)
class Plan:
    intent: str
    subtasks: list[Subtask]


_SHELL_KEYWORDS = (
    "uptime",
    "df ",
    "free ",
    "whoami",
    "pwd",
    "ls ",
    "echo ",
    "system status",
    "disk",
    "memory",
    "process",
    "kill ",
    "run ",
    "execute ",
    "shell",
    "command",
)

_KNOWLEDGE_KEYWORDS = (
    "what is",
    "what are",
    "explain",
    "summarize",
    "tell me about",
    "describe",
    "search",
    "find ",
    "lookup",
    "look up",
    "according to",
    "documentation",
    "docs",
    "memory",
    "remember",
    "recall",
)

_FILM_KEYWORDS = (
    "film:",
    "rating:",
    "movie",
    "film",
    "letterboxd",
    "tmdb",
    "ran ",
    "la haine",
    "ghost dog",
    "watched",
    "watch",
    "recommend",
    "kurosawa",
)


def classify(message: str) -> Specialist:
    """Keyword router. Sub-pass 2.5 swaps this for an LLM intent classifier."""
    msg = message.lower().strip()

    if any(k in msg for k in _FILM_KEYWORDS):
        return "tombombadil"
    if any(k in msg for k in _KNOWLEDGE_KEYWORDS):
        return "finrod"
    if any(k in msg for k in _SHELL_KEYWORDS):
        return "earendil"
    return "earendil"


def plan(message: str) -> Plan:
    """Single-step plan: classify and forward the raw message to one specialist.

    Multi-step decomposition (chain-of-thought across specialists) is a
    future enhancement -- for sub-pass 2 we forward the full message to
    one specialist and aggregate its single result.
    """
    specialist = classify(message)
    return Plan(
        intent=specialist,
        subtasks=[Subtask(specialist=specialist, payload={"message": message})],
    )
