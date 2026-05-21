from __future__ import annotations

from pathlib import Path

import pytest

from agents.tombombadil.film_knowledge import FILM_DATABASE, FilmKnowledge
from agents.tombombadil.letterboxd_loader import (
    load_letterboxd_export,
    merge_into_film_database,
)


@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    (tmp_path / "profile.csv").write_text(
        "Username,Name,Email,Bio,Location,Website,Date Joined,Favorite Films\n"
        "solomonsmith,Solomon Smith,x@x,bio,NY,,2020-01-01,"
        "\"Ran, La Haine, Persona, Stalker\"\n",
        encoding="utf-8",
    )
    (tmp_path / "ratings.csv").write_text(
        "Date,Name,Year,Letterboxd URI,Rating\n"
        "2024-01-15,Inception,2010,https://lb/inception,4.5\n"
        "2024-02-01,Ran,1985,https://lb/ran,4.5\n"
        "2024-03-10,Stalker,1979,https://lb/stalker,5.0\n",
        encoding="utf-8",
    )
    (tmp_path / "reviews.csv").write_text(
        "Date,Name,Year,Letterboxd URI,Rating,Rewatch,Review,Tags,Watched Date\n"
        "2024-01-15,Inception,2010,https://lb/inception,4.5,No,"
        "\"Heist of the mind\",\"sci-fi, heist\",2024-01-14\n",
        encoding="utf-8",
    )
    (tmp_path / "watched.csv").write_text(
        "Date,Name,Year,Letterboxd URI\n"
        "2023-12-01,Persona,1966,https://lb/persona\n",
        encoding="utf-8",
    )
    return tmp_path


def test_load_export_reads_profile(export_dir):
    export = load_letterboxd_export(export_dir)
    assert export.name == "Solomon Smith"
    assert "Ran" in export.favorites
    assert len(export.favorites) == 4


def test_load_export_doubles_rating_to_ten_point_scale(export_dir):
    export = load_letterboxd_export(export_dir)
    inception = export.entries["inception|2010"]
    assert inception.rating == 9.0
    stalker = export.entries["stalker|1979"]
    assert stalker.rating == 10.0


def test_load_export_attaches_review_and_tags(export_dir):
    export = load_letterboxd_export(export_dir)
    inception = export.entries["inception|2010"]
    assert inception.review == "Heist of the mind"
    assert "sci-fi" in inception.tags


def test_load_export_includes_watched_only_films(export_dir):
    export = load_letterboxd_export(export_dir)
    persona = export.entries["persona|1966"]
    assert persona.rating is None
    assert persona.year == 1966


def test_load_export_tolerates_missing_files(tmp_path):
    export = load_letterboxd_export(tmp_path)
    assert export.entries == {}
    assert export.name == "Letterboxd User"


def test_merge_appends_watcher_to_existing_film(export_dir):
    export = load_letterboxd_export(export_dir)
    merged = merge_into_film_database(FILM_DATABASE, export)

    ran = next(f for f in merged["films"] if f["title"] == "Ran")
    names = [w["name"] for w in ran["watchers"]]
    assert "Solomon Smith" in names
    # Original 5 watchers still there (Solomon was in seed too — should update, not duplicate)
    assert len([n for n in names if n == "Solomon Smith"]) == 1


def test_merge_adds_new_film_when_missing(export_dir):
    export = load_letterboxd_export(export_dir)
    merged = merge_into_film_database(FILM_DATABASE, export)
    titles = [f["title"] for f in merged["films"]]
    assert "Inception" in titles
    assert "Stalker" in titles


def test_merge_updates_people_avg_rating(export_dir):
    export = load_letterboxd_export(export_dir)
    merged = merge_into_film_database(FILM_DATABASE, export)
    profile = merged["people"]["Solomon Smith"]
    # 9.0, 9.0, 10.0 -> avg 9.33
    assert profile["avg_rating"] == pytest.approx(9.33, abs=0.05)
    assert "Inception" in profile["films_watched"]


def test_merge_does_not_mutate_input(export_dir):
    export = load_letterboxd_export(export_dir)
    original_film_count = len(FILM_DATABASE["films"])
    merge_into_film_database(FILM_DATABASE, export)
    assert len(FILM_DATABASE["films"]) == original_film_count


def test_film_knowledge_constructor_loads_letterboxd_dir(export_dir):
    fk = FilmKnowledge(letterboxd_dir=export_dir)
    titles = [f["title"] for f in fk.films]
    assert "Inception" in titles


def test_film_knowledge_skips_when_dir_missing(tmp_path):
    fk = FilmKnowledge(letterboxd_dir=tmp_path / "does-not-exist")
    # Falls back to seed FILM_DATABASE without raising
    assert any(f["title"] == "Ran" for f in fk.films)


def test_film_knowledge_reads_env_var(export_dir, monkeypatch):
    monkeypatch.setenv("LETTERBOXD_EXPORT_DIR", str(export_dir))
    fk = FilmKnowledge()
    titles = [f["title"] for f in fk.films]
    assert "Inception" in titles


def test_load_export_handles_modern_profile_schema(tmp_path):
    """Letterboxd's current profile.csv uses Given Name + Family Name,
    not Name. Make sure we still resolve a sensible viewer name."""
    (tmp_path / "profile.csv").write_text(
        "Date Joined,Username,Given Name,Family Name,Email Address,"
        "Location,Website,Bio,Pronoun,Favorite Films\n"
        "2022-07-27,SolomonThaChef,Solomon,,,California,,,He / his,\n",
        encoding="utf-8",
    )
    export = load_letterboxd_export(tmp_path)
    # Given Name with empty Family Name -> just "Solomon"
    assert export.name == "Solomon"


def test_load_export_viewer_name_override_wins(tmp_path):
    """Explicit override beats whatever profile.csv says — used when
    the Letterboxd handle doesn't match the canonical seed identity."""
    (tmp_path / "profile.csv").write_text(
        "Date Joined,Username,Given Name,Family Name,Email Address,"
        "Location,Website,Bio,Pronoun,Favorite Films\n"
        "2022-07-27,SolomonThaChef,Solomon,,,California,,,He / his,\n",
        encoding="utf-8",
    )
    export = load_letterboxd_export(tmp_path, viewer_name="Solomon Smith")
    assert export.name == "Solomon Smith"


def test_film_knowledge_constructor_viewer_name_overrides_loader(tmp_path):
    """Constructor arg is the in-code default — env still wins over it."""
    (tmp_path / "profile.csv").write_text(
        "Date Joined,Username,Given Name,Family Name,Email Address,"
        "Location,Website,Bio,Pronoun,Favorite Films\n"
        "2022-07-27,SolomonThaChef,Solomon,,,California,,,He / his,\n",
        encoding="utf-8",
    )
    (tmp_path / "ratings.csv").write_text(
        "Date,Name,Year,Letterboxd URI,Rating\n"
        "2024-01-15,Inception,2010,https://lb/inception,4.5\n",
        encoding="utf-8",
    )
    fk = FilmKnowledge(
        letterboxd_dir=tmp_path, letterboxd_viewer_name="Solomon Smith"
    )
    inception = next(f for f in fk.films if f["title"] == "Inception")
    assert "Solomon Smith" in [w["name"] for w in inception["watchers"]]


def test_film_knowledge_env_var_overrides_constructor_arg(tmp_path, monkeypatch):
    (tmp_path / "profile.csv").write_text(
        "Date Joined,Username,Given Name,Family Name,Email Address,"
        "Location,Website,Bio,Pronoun,Favorite Films\n"
        "2022-07-27,SolomonThaChef,Solomon,,,California,,,He / his,\n",
        encoding="utf-8",
    )
    (tmp_path / "ratings.csv").write_text(
        "Date,Name,Year,Letterboxd URI,Rating\n"
        "2024-01-15,Inception,2010,https://lb/inception,4.5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LETTERBOXD_VIEWER_NAME", "Env Wins")
    fk = FilmKnowledge(
        letterboxd_dir=tmp_path, letterboxd_viewer_name="Constructor Loses"
    )
    inception = next(f for f in fk.films if f["title"] == "Inception")
    names = [w["name"] for w in inception["watchers"]]
    assert "Env Wins" in names
    assert "Constructor Loses" not in names


def test_get_user_summary_lists_all_rated_films_alphabetically(tmp_path):
    (tmp_path / "profile.csv").write_text(
        "Date Joined,Username,Given Name,Family Name,Email Address,"
        "Location,Website,Bio,Pronoun,Favorite Films\n"
        "2022-07-27,solo,Solomon,Smith,,,,,,\n",
        encoding="utf-8",
    )
    (tmp_path / "ratings.csv").write_text(
        "Date,Name,Year,Letterboxd URI,Rating\n"
        "2024-01-15,Inception,2010,uri,4.5\n"
        "2024-02-01,Stalker,1979,uri,5.0\n"
        "2024-03-10,Aftersun,2022,uri,4.0\n",
        encoding="utf-8",
    )
    # Use a name not in seed FILM_DATABASE so the count matches the export.
    fk = FilmKnowledge(letterboxd_dir=tmp_path, letterboxd_viewer_name="Test User")
    summary = fk.get_user_summary("Test User")
    assert summary is not None
    assert "Aftersun" in summary
    assert "Inception" in summary
    assert "Stalker" in summary
    # Alphabetical order in the all-films section (not the "Highest-rated" line)
    alpha = summary.split("All rated films (alphabetical):", 1)[1]
    assert alpha.index("Aftersun") < alpha.index("Inception") < alpha.index("Stalker")
    # "3 films rated" rather than the old "top-30" truncation
    assert "3 films rated" in summary


def test_film_knowledge_honors_viewer_name_env(tmp_path, monkeypatch):
    (tmp_path / "profile.csv").write_text(
        "Date Joined,Username,Given Name,Family Name,Email Address,"
        "Location,Website,Bio,Pronoun,Favorite Films\n"
        "2022-07-27,SolomonThaChef,Solomon,,,California,,,He / his,\n",
        encoding="utf-8",
    )
    (tmp_path / "ratings.csv").write_text(
        "Date,Name,Year,Letterboxd URI,Rating\n"
        "2024-01-15,Inception,2010,https://lb/inception,4.5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LETTERBOXD_VIEWER_NAME", "Solomon Smith")
    fk = FilmKnowledge(letterboxd_dir=tmp_path)
    inception = next(f for f in fk.films if f["title"] == "Inception")
    names = [w["name"] for w in inception["watchers"]]
    assert "Solomon Smith" in names
