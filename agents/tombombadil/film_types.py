"""Shared shapes for the film database.

These live in their own module rather than in
:mod:`agents.tombombadil.film_knowledge` because
:mod:`agents.tombombadil.letterboxd_loader` produces a ``FilmDatabase``
and ``film_knowledge`` already imports *from* the loader. Putting the
types in either one would make the import circular.

``total=False`` throughout: these describe records assembled from a
hand-maintained literal, a Letterboxd CSV export, and the merge of the
two, so no single key is guaranteed on every record. Readers use
``.get()`` accordingly.
"""

from __future__ import annotations

from typing import TypedDict


class Watcher(TypedDict, total=False):
    """One person's take on one film, as stored in ``Film["watchers"]``."""

    name: str
    rating: float | None
    themes: list[str]
    take: str


class Film(TypedDict, total=False):
    """A film plus everyone who has watched it."""

    title: str
    directors: str
    year: int | None
    watchers: list[Watcher]
    group_consensus: str
    themes: list[str]


class Person(TypedDict, total=False):
    """A viewer profile, keyed by canonical name in ``FilmDatabase["people"]``.

    Built either from the hand-maintained literal or from
    :meth:`agents.tombombadil.letterboxd_loader.LetterboxdExport.watcher_record`.
    """

    avg_rating: float
    preferred_themes: list[str]
    style: str
    films_watched: list[str]


class FilmDatabase(TypedDict):
    """The whole catalogue: films, and the people who rate them."""

    films: list[Film]
    people: dict[str, Person]
