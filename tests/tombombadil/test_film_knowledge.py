"""``get_user_summary`` is the block Tom's system prompt calls the only
source of truth for a viewer's ratings, and PR #54's preamble tells the
model every rated film is in it. These tests hold that claim to account.
"""

from __future__ import annotations

import pytest

from agents.tombombadil.film_knowledge import FilmKnowledge

VIEWER = "Test Viewer"


def _film(title: str, rating: float, year: int | None = 2000) -> dict:
    return {
        "title": title,
        "year": year,
        "watchers": [{"name": VIEWER, "rating": rating, "themes": [], "take": ""}],
    }


@pytest.fixture
def knowledge():
    """A viewer with 120 rated films: well past the old 30-film cap, and
    roughly the order of magnitude of a real Letterboxd export.
    """
    fk = FilmKnowledge()
    fk.films = [_film(f"Film {i:03d}", (i % 10) + 1) for i in range(120)]
    fk.people = {VIEWER: {"avg_rating": 5.5}}
    return fk


def test_summary_includes_every_rated_film(knowledge):
    """The regression. This used to return the top 30 by rating, so a
    ~900-film library lost ~870 entries and Tom denied films the viewer
    had genuinely rated.
    """
    summary = knowledge.get_user_summary(VIEWER)
    assert summary is not None
    for i in range(120):
        assert f"Film {i:03d}" in summary, f"Film {i:03d} missing from the summary"
    assert "120 films rated" in summary


def test_summary_is_alphabetical_so_the_model_can_scan_it(knowledge):
    body = knowledge.get_user_summary(VIEWER).split("All rated films (alphabetical):")[1]
    titles = [ln.split(":")[0].lstrip("- ").split(" (")[0] for ln in body.strip().splitlines()]
    assert titles == sorted(titles, key=str.lower)


def test_summary_keeps_a_separate_highest_rated_line(knowledge):
    """The alphabetical dump is for lookup. 'What are my top films' needs
    the ratings-sorted line, which the preamble points the model at.
    """
    line = next(
        ln for ln in knowledge.get_user_summary(VIEWER).splitlines()
        if ln.startswith("Highest-rated:")
    )
    assert line.count("/10") == 10
    assert "(10/10)" in line


def test_summary_respects_an_explicit_cap(knowledge):
    summary = knowledge.get_user_summary(VIEWER, max_films=5)
    assert "5 films rated" in summary
    assert summary.count("/10") == 5 + 5  # 5 catalogue lines + 5 in Highest-rated


def test_summary_matches_the_viewer_case_insensitively(knowledge):
    assert knowledge.get_user_summary("test viewer") is not None


def test_summary_is_none_for_an_unknown_viewer(knowledge):
    assert knowledge.get_user_summary("Nobody At All") is None


def test_summary_is_none_when_the_viewer_rated_nothing():
    fk = FilmKnowledge()
    fk.films = [{"title": "Unwatched", "year": 1999, "watchers": []}]
    fk.people = {VIEWER: {"avg_rating": 0}}
    assert fk.get_user_summary(VIEWER) is None
