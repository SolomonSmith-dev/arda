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

Theme enrichment (D3): films get themes from Letterboxd tags first, then
from a lightweight keyword scan of title + review. The viewer's
``preferred_themes`` is derived from those film themes (weighted by
rating) so ``suggest_for_person`` can rank against imported history.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from core.logging import get_logger

log = get_logger("agents.tombombadil.letterboxd")

# Keyword → theme. Order matters only for readability; matches are
# unioned. Keep this list small and deterministic — no LLM / TMDB.
_THEME_KEYWORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsci-?fi\b|\bscience fiction\b", re.I), "sci-fi"),
    (re.compile(r"\bheist\b", re.I), "heist"),
    (re.compile(r"\bnoir\b", re.I), "noir"),
    (re.compile(r"\bhorror\b", re.I), "horror"),
    (re.compile(r"\bwar\b", re.I), "war"),
    (re.compile(r"\bviolence\b|\bviolent\b", re.I), "violence"),
    (re.compile(r"\brevenge\b|\bvengeance\b", re.I), "vengeance"),
    (re.compile(r"\bfate\b|\bdestiny\b", re.I), "fate"),
    (re.compile(r"\bidentity\b", re.I), "identity"),
    (re.compile(r"\bpower\b", re.I), "power"),
    (re.compile(r"\bgreed\b", re.I), "greed"),
    (re.compile(r"\bbetrayal\b", re.I), "betrayal"),
    (re.compile(r"\bloneliness\b|\bsolit(?:ude|ary)\b", re.I), "loneliness"),
    (re.compile(r"\bhonor\b|\bhonour\b|\bcode\b", re.I), "honor"),
    (re.compile(r"\bracis[mt]\b|\bracial\b", re.I), "racism"),
    (re.compile(r"\bpolice\b|\bbrutality\b", re.I), "state power"),
    (re.compile(r"\bbrotherhood\b", re.I), "brotherhood"),
    (re.compile(r"\btragedy\b|\btragic\b", re.I), "tragedy"),
    (re.compile(r"\bdream\b|\bmind\b|\bmemory\b", re.I), "identity"),
    (re.compile(r"\bsurvival\b", re.I), "survival"),
    (re.compile(r"\bfamily\b", re.I), "family"),
)

_MAX_PREFERRED_THEMES = 8


@dataclass
class LetterboxdEntry:
    title: str
    year: int | None = None
    rating: float | None = None
    review: str = ""
    tags: list[str] = field(default_factory=list)
    watched_date: str = ""
    uri: str = ""


def infer_themes(title: str, review: str = "", tags: list[str] | None = None) -> list[str]:
    """Return a de-duplicated theme list for a film.

    Priority: explicit Letterboxd tags, then keyword hits in
    ``title + review``. Always returns at least one theme so imported
    films participate in ``suggest_for_person`` ranking (D3).
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(theme: str) -> None:
        t = theme.strip().lower()
        if not t or t in seen:
            return
        seen.add(t)
        out.append(t)

    for tag in tags or []:
        _add(tag)

    blob = f"{title} {review}".strip()
    if blob:
        for pattern, theme in _THEME_KEYWORDS:
            if pattern.search(blob):
                _add(theme)

    if not out:
        _add("cinema")
    return out


@dataclass
class LetterboxdExport:
    name: str
    favorites: list[str]
    entries: dict[str, LetterboxdEntry]

    def watcher_record(self, film_themes_by_title: dict[str, list[str]] | None = None) -> dict:
        """Return a dict shaped like ``FILM_DATABASE['people'][name]``.

        ``preferred_themes`` is derived from the viewer's imported films'
        themes (rating-weighted) so recommendations work without manual
        Letterboxd tagging.
        """
        ratings = [e.rating for e in self.entries.values() if e.rating is not None]
        avg = round(sum(ratings) / len(ratings), 2) if ratings else 0.0
        watched = [e.title for e in self.entries.values()]
        preferred = _derive_preferred_themes(self, film_themes_by_title or {})
        return {
            "avg_rating": avg,
            "preferred_themes": preferred,
            "style": "Imported from Letterboxd",
            "films_watched": watched,
        }


def _derive_preferred_themes(
    export: LetterboxdExport,
    film_themes_by_title: dict[str, list[str]],
) -> list[str]:
    """Aggregate themes from high-rated / favorite films."""
    counts: Counter[str] = Counter()
    fav_set = {f.lower() for f in export.favorites}

    for entry in export.entries.values():
        themes = list(entry.tags)
        themes.extend(film_themes_by_title.get(entry.title.lower(), []))
        themes = infer_themes(entry.title, entry.review, themes)

        weight = 1
        if entry.rating is not None:
            if entry.rating >= 9:
                weight = 3
            elif entry.rating >= 7:
                weight = 2
        if entry.title.lower() in fav_set:
            weight += 1

        for theme in themes:
            if theme == "cinema" and len(themes) > 1:
                continue
            counts[theme] += weight

    if not counts:
        return []
    return [t for t, _ in counts.most_common(_MAX_PREFERRED_THEMES)]


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

    New / theme-less films are enriched via :func:`infer_themes` so they
    participate in recommendation ranking (D3).
    """
    films = [dict(f) for f in base.get("films", [])]
    people = dict(base.get("people", {}))

    by_key: dict[str, dict] = {}
    for f in films:
        f["watchers"] = list(f.get("watchers", []))
        f["themes"] = list(f.get("themes", []))
        by_key[_key(f.get("title", ""), f.get("year"))] = f

    for entry in export.entries.values():
        key = _key(entry.title, entry.year)
        film = by_key.get(key)
        inferred = infer_themes(entry.title, entry.review, entry.tags)
        if film is None:
            film = {
                "title": entry.title,
                "directors": "",
                "year": entry.year,
                "watchers": [],
                "group_consensus": "",
                "themes": inferred,
            }
            films.append(film)
            by_key[key] = film
        elif not film.get("themes"):
            film["themes"] = inferred
        else:
            # Union inferred themes into existing seed themes without
            # overwriting curated seed tags.
            existing = {t.lower() for t in film["themes"]}
            for theme in inferred:
                if theme not in existing and theme != "cinema":
                    film["themes"].append(theme)
                    existing.add(theme)

        already = next(
            (w for w in film["watchers"] if w.get("name") == export.name), None
        )
        watcher_entry = {
            "name": export.name,
            "rating": entry.rating,
            "themes": inferred,
            "take": entry.review or "",
        }
        if already is None:
            film["watchers"].append(watcher_entry)
        else:
            already.update({k: v for k, v in watcher_entry.items() if v not in (None, "", [])})

    film_themes_by_title = {
        f["title"].lower(): list(f.get("themes") or []) for f in films
    }
    people[export.name] = export.watcher_record(film_themes_by_title)

    return {"films": films, "people": people}
