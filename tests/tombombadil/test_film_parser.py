"""Ported from tombombadil/test_parser.py — converted from script-style assertions to pytest."""

from __future__ import annotations

import pytest

from agents.tombombadil.film_parser import FilmNoteParser, parse_film_note


@pytest.fixture
def parser() -> FilmNoteParser:
    return FilmNoteParser()


def test_valid_input(parser):
    r = parser.parse("Name: Solomon\nFilm: Ran\nRating: 9\nReaction: Masterpiece")
    assert r["valid"] is True
    assert r["data"]["film"] == "Ran"
    assert r["data"]["name"] == "Solomon"
    assert isinstance(r["data"]["rating"], float)
    assert r["data"]["rating"] == 9.0


def test_missing_name_is_warning_not_error(parser):
    r = parser.parse("Film: La Haine\nRating: 10")
    assert r["valid"] is True
    assert r["data"]["name"] is None
    assert r["data"]["film"] == "La Haine"
    assert len(r["warnings"]) > 0


def test_missing_rating_is_error(parser):
    r = parser.parse("Name: Gavin\nFilm: Ghost Dog")
    assert r["valid"] is False
    assert any("Rating" in e for e in r["errors"])


def test_markdown_strip(parser):
    r = parser.parse("**Name:** Solomon\n__Film:__ Ran\nRating: 9")
    assert r["valid"] is True
    assert r["data"]["film"] == "Ran"
    assert r["data"]["name"] == "Solomon"


def test_clamp_high_rating(parser):
    r = parser.parse("Name: Test\nFilm: Test\nRating: 15")
    assert r["valid"] is True
    assert r["data"]["rating"] == 10.0


def test_clamp_low_rating(parser):
    r = parser.parse("Name: Test\nFilm: Test\nRating: -5")
    assert r["valid"] is True
    assert r["data"]["rating"] == 0.0


def test_empty_input(parser):
    r = parser.parse("")
    assert r["valid"] is False
    assert len(r["errors"]) > 0


def test_oversized_input(parser):
    r = parser.parse("A" * 6000)
    assert r["valid"] is False
    assert len(r["errors"]) > 0


def test_float_rating(parser):
    r = parser.parse("Name: Test\nFilm: Test\nRating: 8.5")
    assert r["valid"] is True
    assert r["data"]["rating"] == 8.5


def test_missing_film_is_error(parser):
    r = parser.parse("Name: Solomon\nRating: 9")
    assert r["valid"] is False
    assert any("Film" in e for e in r["errors"])


def test_module_level_parse_function():
    r = parse_film_note("Name: A\nFilm: B\nRating: 7")
    assert r["valid"] is True
    assert r["data"]["film"] == "B"
