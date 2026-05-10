"""Rule-based extraction of structured signals from a Discord message.

Cheap, deterministic, runs on every inbound message *after* Tom has
already replied so it adds no latency to the response path. PR 1 ships
with regex rules; PR 6 may swap in an LLM-backed extractor when
``CLAUDE_API_KEY`` is set.

Three outputs:

* ``prefs``      -> map of pref-key to value, applied to
  ``tom:pref:{discord_id}`` (e.g. ``suppress_films=1``).
* ``notes``      -> structured film ratings to forward into
  :func:`agents.tombombadil.persistent_memory.save_note`.
* ``free_facts`` -> short text snippets to embed into Finrod so Tom can
  recall them next session (preferences, declared opinions, "remember
  that ..." asks).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.tombombadil.identity import Viewer


@dataclass
class NoteDraft:
    film: str
    rating: float
    viewer: str
    raw: str = ""


@dataclass
class ExtractedFacts:
    prefs: dict[str, str] = field(default_factory=dict)
    notes: list[NoteDraft] = field(default_factory=list)
    free_facts: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.prefs or self.notes or self.free_facts)


_SUPPRESS_FILMS_PATTERNS = (
    re.compile(r"\bstop\s+(?:mentioning|talking\s+about|bringing\s+up)\s+films?\b", re.IGNORECASE),
    re.compile(r"\bstop\s+(?:mentioning|talking\s+about|bringing\s+up)\s+movies?\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+(?:mention|talk\s+about|bring\s+up)\s+(?:films?|movies?|my\s+film\s+history|my\s+letterboxd)\b", re.IGNORECASE),
)

_UNSUPPRESS_FILMS_PATTERNS = (
    re.compile(r"\b(?:you\s+can|please|go\s+ahead|feel\s+free\s+to)\s+(?:mention|talk\s+about|bring\s+up)\s+(?:films?|movies?)\s+again\b", re.IGNORECASE),
    re.compile(r"\b(?:resume|restart|reset)\s+film\s+(?:mentions|talk|recs?)\b", re.IGNORECASE),
)

# "I rated Inception 9/10", "I gave Get Out an 8", "Ran was a 9 for me",
# "I'd rate Stalker a 10/10".
_RATING_PATTERNS = (
    re.compile(
        r"\bI\s+(?:rated|gave|scored)\s+(?:the\s+)?(?P<film>[\"'].+?[\"']|[A-Z][\w\s:&'\-!?,]+?)\s+(?:an?\s+)?(?P<rating>-?\d+(?:\.\d+)?)\s*(?:/\s*10)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bI(?:'d|\s+would)\s+(?:rate|give)\s+(?P<film>[\"'].+?[\"']|[A-Z][\w\s:&'\-!?,]+?)\s+(?:an?\s+)?(?P<rating>-?\d+(?:\.\d+)?)\s*(?:/\s*10)?\b",
        re.IGNORECASE,
    ),
)

_REMEMBER_PATTERNS = (
    re.compile(r"\bremember\s+that\s+(?P<fact>.+?)(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bfor\s+the\s+record[,:]?\s+(?P<fact>.+?)(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bnote\s+(?:to\s+self|that)[,:]?\s+(?P<fact>.+?)(?:[.!?]|$)", re.IGNORECASE),
)

_STRONG_OPINION_PATTERNS = (
    re.compile(r"\bI\s+(?:absolutely\s+|really\s+|fucking\s+)?(?:love|adore|hate|despise|can'?t\s+stand)\s+(?P<obj>.+?)(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bI'?m\s+(?:obsessed\s+with|sick\s+of)\s+(?P<obj>.+?)(?:[.!?]|$)", re.IGNORECASE),
)

_PREF_KEY_SUPPRESS_FILMS = "suppress_films"
_MAX_FACT_LEN = 280


def _trim(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _truncate(s: str, limit: int = _MAX_FACT_LEN) -> str:
    s = _trim(s)
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def extract(user_text: str, bot_reply: str, viewer: Viewer) -> ExtractedFacts:
    """Run all rules and return the union of their findings.

    ``bot_reply`` is currently unused but accepted for symmetry with a
    future LLM extractor that may want to detect cases where the bot
    promised to remember something.
    """
    facts = ExtractedFacts()
    if not user_text or not user_text.strip():
        return facts

    text = user_text.strip()

    if any(p.search(text) for p in _SUPPRESS_FILMS_PATTERNS):
        facts.prefs[_PREF_KEY_SUPPRESS_FILMS] = "1"
        facts.free_facts.append("user asked tom to stop mentioning films")
    elif any(p.search(text) for p in _UNSUPPRESS_FILMS_PATTERNS):
        facts.prefs[_PREF_KEY_SUPPRESS_FILMS] = "0"

    for pattern in _RATING_PATTERNS:
        for match in pattern.finditer(text):
            film = _trim(match.group("film")).strip("\"'")
            try:
                rating = float(match.group("rating"))
            except ValueError:
                continue
            rating = max(0.0, min(10.0, rating))
            if film and viewer.canonical_name:
                facts.notes.append(
                    NoteDraft(film=film, rating=rating, viewer=viewer.canonical_name, raw=match.group(0))
                )

    for pattern in _REMEMBER_PATTERNS:
        for match in pattern.finditer(text):
            fact = _truncate(match.group("fact"))
            if fact:
                facts.free_facts.append(fact)

    for pattern in _STRONG_OPINION_PATTERNS:
        for match in pattern.finditer(text):
            facts.free_facts.append(_truncate(match.group(0)))

    return facts
