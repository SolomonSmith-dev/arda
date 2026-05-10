from __future__ import annotations

from pathlib import Path

import pytest

from agents.tombombadil import identity
from agents.tombombadil.identity import Tier, resolve

YAML_BODY = """\
owner:
  discord_id: "111111111111111111"
  canonical_name: "Solomon Smith"
regulars:
  - discord_id: "222222222222222222"
    canonical_name: "Brian"
  - discord_id: "333333333333333333"
    canonical_name: "Gavin"
"""


@pytest.fixture
def identity_yaml(tmp_path: Path, monkeypatch):
    path = tmp_path / "identity.yaml"
    path.write_text(YAML_BODY)
    monkeypatch.setenv("TOMBOMBADIL_IDENTITY_FILE", str(path))
    identity.reload_config()
    yield path
    identity.reload_config()


@pytest.fixture
def no_yaml(tmp_path: Path, monkeypatch):
    # Point to a path that doesn't exist so the fallback path runs.
    monkeypatch.setenv("TOMBOMBADIL_IDENTITY_FILE", str(tmp_path / "missing.yaml"))
    identity.reload_config()
    yield
    identity.reload_config()


def test_owner_resolves_to_solomon_tier(identity_yaml):
    v = resolve("111111111111111111", "Solomon [GMNI]")
    assert v.tier is Tier.SOLOMON
    assert v.canonical_name == "Solomon Smith"
    assert v.is_owner is True


def test_known_regular_by_id_resolves_to_regular_tier(identity_yaml):
    v = resolve("222222222222222222", "Brian Leeds")
    assert v.tier is Tier.REGULAR
    assert v.canonical_name == "Brian"
    assert v.is_owner is False


def test_unknown_id_and_unknown_name_is_stranger(identity_yaml):
    v = resolve("999999999999999999", "RandomDiscordUser")
    assert v.tier is Tier.STRANGER
    assert v.canonical_name is None
    assert v.is_owner is False


def test_name_heuristic_fallback_without_yaml(no_yaml):
    # No YAML, but display name matches a seeded FILM_DATABASE entry.
    v = resolve("777777777777777777", "Brian")
    assert v.tier is Tier.REGULAR
    assert v.canonical_name == "Brian"


def test_name_heuristic_owner_match_without_yaml(no_yaml):
    # No YAML, but display name first-word matches "Solomon Smith".
    v = resolve("777777777777777777", "Solomon [GMNI]")
    assert v.tier is Tier.SOLOMON
    assert v.canonical_name == "Solomon Smith"


def test_strip_discriminator(no_yaml):
    v = resolve("888888888888888888", "Brian#1234")
    assert v.canonical_name == "Brian"
    assert v.tier is Tier.REGULAR


def test_all_known_includes_yaml_and_film_db(identity_yaml):
    names = identity.all_known()
    assert "Solomon Smith" in names
    assert "Brian" in names
    assert "Gavin" in names
    # Film DB also seeds Anthony Taylor, Isis, G.
    assert any(n == "Anthony Taylor" for n in names)


def test_malformed_yaml_falls_back_silently(tmp_path: Path, monkeypatch):
    path = tmp_path / "broken.yaml"
    path.write_text("owner: {oops: unclosed")
    monkeypatch.setenv("TOMBOMBADIL_IDENTITY_FILE", str(path))
    identity.reload_config()
    try:
        v = resolve("111111111111111111", "Anyone")
        assert v.tier is Tier.STRANGER
    finally:
        identity.reload_config()
