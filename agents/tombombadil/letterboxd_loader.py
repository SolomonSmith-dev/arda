"""Letterboxd export loader.

Reads a Letterboxd CSV export and returns it in a shape compatible with
:mod:`agents.tombombadil.film_knowledge` `FILM_DATABASE`. Designed for the
"download an export from letterboxd.com/settings/export" zip — point this
at the unzipped directory.

Files we read (any missing are skipped silently):

- ``profile.csv``  — pulls ``Name`` and ``Favorite Films``.
- ``ratings.csv``  — Date, Name, Year, Letterboxd URI, Rating (0.5-5.0).
- ``reviews.csv``  — adds ``Review``/``Tags`` to the rated entries.
- ``watched.csv``  — films watched without a rating; merged in last.

Letterboxd ratings are on a 0.5-5.0 scale; we double them so they match
the 0-10 scale used in ``FILM_DATABASE``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from core.logging import get_logger

log = get_logger("agents.tombombadil.letterboxd")


@dataclass
class LetterboxdEntry:
    title: str
    year: int | None = None
    rating: float | None = None
    review: str = ""
    tags: list[str] = field(default_factory=list)
    watched_date: str = ""
    uri: str = ""


@dataclass
class LetterboxdExport:
    name: str
    favorites: list[str]
    entries: dict[str, LetterboxdEntry]

    def watcher_record(self, themes_for: callable | None = None) -> dict:
        """Return a dict shaped like ``FILM_DATABASE['people'][name]``."""
        ratings = [e.rating for e in self.entries.values() if e.rating is not None]
        avg = round(sum(ratings) / len(ratings), 2) if ratings else 0.0
        watched = [e.title for e in self.entries.values()]
        return {
            "avg_rating": avg,
            "preferred_themes": [],
            "style": "Imported from Letterboxd",
            "films_watched": watched,
        }


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _parse_year(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw or not raw.isdigit():
        return None
    return int(raw)


def _parse_rating(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return round(v * 2, 1)


def _parse_tags(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def _key(title: str, year: int | None) -> str:
    return f"{title.lower().strip()}|{year or ''}"


def load_letterboxd_export(
    export_dir: Path, viewer_name: str | None = None
) -> LetterboxdExport:
    """Read a Letterboxd export directory and return a :class:`LetterboxdExport`.

    Missing CSVs are tolerated. If ``viewer_name`` is provided it overrides
    whatever ``profile.csv`` says — useful when your Letterboxd handle
    doesn't match the canonical name used in the seed FILM_DATABASE.
    """
    export_dir = Path(export_dir)

    name = "Letterboxd User"
    favorites: list[str] = []
    profile_rows = _read_csv(export_dir / "profile.csv")
    if profile_rows:
        row = profile_rows[0]
        derived = (
            (row.get("Name") or "").strip()
            or " ".join(
                filter(None, [(row.get("Given Name") or "").strip(),
                              (row.get("Family Name") or "").strip()])
            ).strip()
            or (row.get("Username") or "").strip()
        )
        name = derived or name
        fav_raw = row.get("Favorite Films") or ""
        favorites = [f.strip() for f in fav_raw.split(",") if f.strip()]

    if viewer_name:
        name = viewer_name

    entries: dict[str, LetterboxdEntry] = {}

    for row in _read_csv(export_dir / "watched.csv"):
        title = (row.get("Name") or "").strip()
        if not title:
            continue
        year = _parse_year(row.get("Year") or "")
        entries[_key(title, year)] = LetterboxdEntry(
            title=title,
            year=year,
            uri=(row.get("Letterboxd URI") or "").strip(),
            watched_date=(row.get("Date") or "").strip(),
        )

    for row in _read_csv(export_dir / "ratings.csv"):
        title = (row.get("Name") or "").strip()
        if not title:
            continue
        year = _parse_year(row.get("Year") or "")
        key = _key(title, year)
        entry = entries.get(key) or LetterboxdEntry(title=title, year=year)
        entry.rating = _parse_rating(row.get("Rating") or "")
        entry.uri = entry.uri or (row.get("Letterboxd URI") or "").strip()
        entry.watched_date = entry.watched_date or (row.get("Date") or "").strip()
        entries[key] = entry

    for row in _read_csv(export_dir / "reviews.csv"):
        title = (row.get("Name") or "").strip()
        if not title:
            continue
        year = _parse_year(row.get("Year") or "")
        key = _key(title, year)
        entry = entries.get(key) or LetterboxdEntry(title=title, year=year)
        if entry.rating is None:
            entry.rating = _parse_rating(row.get("Rating") or "")
        entry.review = (row.get("Review") or "").strip()
        entry.tags = _parse_tags(row.get("Tags") or "")
        entry.watched_date = entry.watched_date or (row.get("Watched Date") or row.get("Date") or "").strip()
        entry.uri = entry.uri or (row.get("Letterboxd URI") or "").strip()
        entries[key] = entry

    log.info(
        "letterboxd_export_loaded",
        name=name,
        favorites=len(favorites),
        entries=len(entries),
        rated=sum(1 for e in entries.values() if e.rating is not None),
        reviewed=sum(1 for e in entries.values() if e.review),
    )
    return LetterboxdExport(name=name, favorites=favorites, entries=entries)


def merge_into_film_database(
    base: dict, export: LetterboxdExport
) -> dict:
    """Return a new film database dict with ``export`` merged into ``base``.

    For each entry: if the film already exists in ``base['films']`` (matched
    by case-insensitive title + year), append the user as a watcher; else
    create a new film entry. Always (re)writes ``base['people'][name]``.
    """
    films = [dict(f) for f in base.get("films", [])]
    people = dict(base.get("people", {}))

    by_key: dict[str, dict] = {}
    for f in films:
        f["watchers"] = list(f.get("watchers", []))
        by_key[_key(f.get("title", ""), f.get("year"))] = f

    for entry in export.entries.values():
        key = _key(entry.title, entry.year)
        film = by_key.get(key)
        if film is None:
            film = {
                "title": entry.title,
                "directors": "",
                "year": entry.year,
                "watchers": [],
                "group_consensus": "",
                "themes": list(entry.tags),
            }
            films.append(film)
            by_key[key] = film

        already = next(
            (w for w in film["watchers"] if w.get("name") == export.name), None
        )
        watcher_entry = {
            "name": export.name,
            "rating": entry.rating,
            "themes": entry.tags,
            "take": entry.review or "",
        }
        if already is None:
            film["watchers"].append(watcher_entry)
        else:
            already.update({k: v for k, v in watcher_entry.items() if v not in (None, "", [])})

    people[export.name] = export.watcher_record()

    return {"films": films, "people": people}
