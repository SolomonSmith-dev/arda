from __future__ import annotations

import fakeredis
import pytest

from agents.tombombadil import draft_store
from agents.tombombadil.fact_extractor import NoteDraft


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


def _draft(film: str = "Inception", rating: float = 8.0, viewer: str = "Solomon Smith") -> NoteDraft:
    return NoteDraft(film=film, rating=rating, viewer=viewer, raw="I rated " + film + " " + str(rating))


def test_push_then_pop_roundtrip(r):
    draft_store.push_pending(r, "tom:hist:ch:1", _draft())
    popped = draft_store.pop_pending(r, "tom:hist:ch:1")
    assert popped is not None
    assert popped.film == "Inception"
    assert popped.rating == 8.0
    assert popped.viewer == "Solomon Smith"


def test_pop_on_empty_returns_none(r):
    assert draft_store.pop_pending(r, "tom:hist:ch:nothing") is None


def test_push_preserves_order(r):
    draft_store.push_pending(r, "x", _draft("A", 1.0))
    draft_store.push_pending(r, "x", _draft("B", 2.0))
    a = draft_store.pop_pending(r, "x")
    b = draft_store.pop_pending(r, "x")
    assert a is not None and b is not None
    assert a.film == "A"
    assert b.film == "B"


def test_push_sets_ttl(r):
    draft_store.push_pending(r, "x", _draft())
    ttl = r.ttl("tom:drafts:scope:x")
    assert 0 < ttl <= draft_store.DRAFT_TTL_SECONDS


def test_bind_to_message_and_get_draft(r):
    draft = _draft()
    draft_store.bind_to_message(
        r, message_id=42, draft=draft, requester_discord_id="111", scope="tom:hist:ch:1"
    )
    got = draft_store.get_draft(r, 42)
    assert got is not None
    assert got["film"] == "Inception"
    assert got["rating"] == "8.0"
    assert got["viewer"] == "Solomon Smith"
    assert got["requester_discord_id"] == "111"
    assert got["scope"] == "tom:hist:ch:1"


def test_get_draft_for_unknown_id_returns_none(r):
    assert draft_store.get_draft(r, 99999) is None


def test_delete_draft_removes_entry(r):
    draft_store.bind_to_message(r, 42, _draft(), requester_discord_id="111", scope="x")
    draft_store.delete_draft(r, 42)
    assert draft_store.get_draft(r, 42) is None


def test_bind_sets_ttl(r):
    draft_store.bind_to_message(r, 42, _draft(), requester_discord_id="111", scope="x")
    ttl = r.ttl("tom:draft:42")
    assert 0 < ttl <= draft_store.DRAFT_TTL_SECONDS
