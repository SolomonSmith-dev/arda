"""Letterboxd diary RSS feed parser.

Letterboxd publishes a per-user diary feed at
``https://letterboxd.com/{username}/rss/``. Each ``<item>`` is one
diary entry; rating + film metadata live in custom ``letterboxd:*``
namespace tags, which feedparser exposes as snake_case attributes
on the parsed entry (e.g. ``entry.letterboxd_filmtitle``).

The parser is intentionally tolerant -- Letterboxd's schema has
shifted before, and we'd rather skip a malformed entry than crash the
sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DiaryEntry:
    title: str
    year: int | None
    rating: float | None
    watched_iso: str
    rewatch: bool
    link: str

    @property
    def watched_dt(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.watched_iso.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None


def _to_int(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _parse_entry(entry: Any) -> DiaryEntry | None:
    """Convert one feedparser entry into a ``DiaryEntry``.

    Returns ``None`` if the entry is missing the bare-minimum film
    title (which would make it useless downstream).
    """
    title = getattr(entry, "letterboxd_filmtitle", None) or getattr(entry, "title", None)
    if not title:
        return None
    title = str(title).strip()
    if not title:
        return None

    year = _to_int(getattr(entry, "letterboxd_filmyear", None))
    # Letterboxd rates 0.5-5.0; we double to 0-10 to match FILM_DATABASE.
    raw_rating = _to_float(getattr(entry, "letterboxd_memberrating", None))
    rating = raw_rating * 2 if raw_rating is not None else None

    watched_iso = (
        getattr(entry, "letterboxd_watcheddate", None)
        or getattr(entry, "letterboxd_published", None)
        or getattr(entry, "published", "")
    )
    rewatch_raw = getattr(entry, "letterboxd_rewatch", "no")
    rewatch = str(rewatch_raw).strip().lower() in ("yes", "true", "1")
    link = getattr(entry, "link", "") or ""

    return DiaryEntry(
        title=title,
        year=year,
        rating=rating,
        watched_iso=str(watched_iso) if watched_iso else "",
        rewatch=rewatch,
        link=link,
    )


def parse_feed_text(text: str) -> list[DiaryEntry]:
    """Parse a Letterboxd diary RSS body and return the entries we
    were able to interpret. ``feedparser`` is imported lazily so the
    rest of the package stays importable without the dependency.
    """
    import feedparser

    parsed = feedparser.parse(text)
    out: list[DiaryEntry] = []
    for entry in parsed.entries or []:
        item = _parse_entry(entry)
        if item is not None:
            out.append(item)
    return out
