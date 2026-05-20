"""Regex-based intent router used by the legacy ``/plan`` endpoint
(``api/routes/tasks.py``) and surfaced through the MCP server's
``arda_plan`` tool. The canonical orchestration path is the Sauron
LangGraph (see ``agents/sauron/graph.py``), which has its own
LLM-driven routing via native Anthropic tool_use -- this module is
the inspection-only "what would happen?" fallback.
"""

from __future__ import annotations

import re
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


_OPS_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(disk|df|du|storage|file|folder|director|ls|rm|cp|mv|chmod|chown)\b", re.IGNORECASE),
    re.compile(r"\b(ps|kill|restart|start|stop|pid|running|service|systemctl|pm2)\b", re.IGNORECASE),
    re.compile(r"\b(port|netstat|ss|ufw|firewall|iptables|listening)\b", re.IGNORECASE),
    re.compile(r"\b(git|commit|push|pull|branch|merge|clone|repo)\b", re.IGNORECASE),
    re.compile(r"\b(docker|container|image|compose|dockerfile)\b", re.IGNORECASE),
    re.compile(r"\b(memory|ram|cpu|load|uptime|top|htop)\b", re.IGNORECASE),
    re.compile(r"\b(ssh|install|apt|npm|pip|update|upgrade|journal)\b", re.IGNORECASE),
    re.compile(r"\b(run|execute|check|monitor|scan|backup|deploy|configure)\b.{0,50}\b(server|service|system|command|script)\b", re.IGNORECASE),
)

_KNOWLEDGE_KEYWORDS = (
    "what is",
    "what are",
    "explain",
    "summarize",
    "tell me about",
    "describe",
    "search",
    "look up",
    "according to",
    "documentation",
    "docs",
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
    """Regex-based intent router. Priority: film > knowledge > ops > earendil default."""
    msg = message.lower().strip()

    if any(k in msg for k in _FILM_KEYWORDS):
        return "tombombadil"
    if any(k in msg for k in _KNOWLEDGE_KEYWORDS):
        return "finrod"
    if any(p.search(message) for p in _OPS_PATTERNS):
        return "earendil"
    return "earendil"


def plan(message: str) -> Plan:
    """Single-step plan: classify and forward the raw message to one specialist."""
    specialist = classify(message)
    return Plan(
        intent=specialist,
        subtasks=[Subtask(specialist=specialist, payload={"message": message})],
    )
