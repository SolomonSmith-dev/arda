from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from core.config import settings
from core.logging import get_logger

log = get_logger("agents.sauron.planner")

Specialist = Literal["earendil", "finrod", "tombombadil"]


@dataclass(frozen=True)
class Subtask:
    specialist: Specialist
    payload: dict


@dataclass(frozen=True)
class Plan:
    intent: str
    subtasks: list[Subtask]


# Ported from morgoth/src/classifier.js (legacy routing layer).
# A Gemini-based intent classifier replaces this in a future phase.
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


def classify_regex(message: str) -> Specialist:
    """Regex-based intent router. Priority: film > knowledge > ops > earendil default.

    Renamed from ``classify`` in PR 6 -- still the production default
    when ``CLAUDE_API_KEY`` is empty or the LLM classifier errors.
    """
    msg = message.lower().strip()

    if any(k in msg for k in _FILM_KEYWORDS):
        return "tombombadil"
    if any(k in msg for k in _KNOWLEDGE_KEYWORDS):
        return "finrod"
    if any(p.search(message) for p in _OPS_PATTERNS):
        return "earendil"
    return "earendil"


_CLASSIFIER_PROMPT = (
    "You are the router for a multi-agent system. Read the user's message "
    "and return ONE WORD naming the specialist that should handle it:\n"
    "- earendil: shell commands, system ops, server diagnostics, deploys, "
    "git/docker/process management, anything that wants a CLI executed.\n"
    "- finrod: knowledge / retrieval / 'what is X', summarising docs, "
    "remembering or recalling stored facts.\n"
    "- tombombadil: anything about films, movies, ratings, watch-party, "
    "club recommendations, Letterboxd, casual chat.\n"
    "If unsure, prefer earendil. Reply with ONLY one of: earendil, finrod, "
    "tombombadil. No punctuation, no explanation."
)


@lru_cache(maxsize=1)
def _llm():
    """Construct the Claude classifier once. Cached so we don't pay
    the SDK / HTTP-client init cost per message. Returns ``None`` when
    ``CLAUDE_API_KEY`` is empty so callers know to use regex fallback.
    """
    if not settings.claude_api_key:
        return None
    try:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model="claude-haiku-4-5-20251001",
            api_key=settings.claude_api_key,
            temperature=0.0,
            max_tokens=8,
        )
    except Exception as exc:
        log.warning("classifier_llm_init_failed", exc=str(exc))
        return None


def _normalise(reply: str) -> Specialist | None:
    token = (reply or "").strip().lower().split()[:1]
    if not token:
        return None
    cand = token[0].strip(".,;:'\"")
    if cand in ("earendil", "finrod", "tombombadil"):
        return cand  # type: ignore[return-value]
    return None


def classify_llm(message: str) -> Specialist:
    """Claude-backed intent router. Falls back to the regex classifier
    on empty key, init failure, network error, or unparseable output.
    """
    llm = _llm()
    if llm is None:
        return classify_regex(message)
    try:
        response = llm.invoke(
            [
                {"role": "system", "content": _CLASSIFIER_PROMPT},
                {"role": "user", "content": message},
            ]
        )
        content = getattr(response, "content", str(response))
        intent = _normalise(content)
        if intent is None:
            log.warning("classifier_unparseable", raw=str(content)[:120])
            return classify_regex(message)
        return intent
    except Exception as exc:
        log.warning("classifier_call_failed", exc=str(exc))
        return classify_regex(message)


def classify(message: str) -> Specialist:
    """Public router. Uses Claude when ``CLAUDE_API_KEY`` is set,
    otherwise the regex fallback. Both paths return the same shape.
    """
    return classify_llm(message)


def plan(message: str) -> Plan:
    """Single-step plan: classify and forward the raw message to one specialist."""
    specialist = classify(message)
    return Plan(
        intent=specialist,
        subtasks=[Subtask(specialist=specialist, payload={"message": message})],
    )
