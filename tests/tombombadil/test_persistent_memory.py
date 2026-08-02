"""Tests for persistent_memory delete_note (D9)."""

from __future__ import annotations

import fakeredis
import pytest

from agents.tombombadil.persistent_memory import delete_note, save_note


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


def test_delete_note_cascades_indexes(r):
    ok, _ = save_note(r, "Inception", "Solomon Smith", 9)
    assert ok
    note_ids = r.zrange("notes:all", 0, -1)
    assert len(note_ids) == 1

    deleted, msg = delete_note(r, "Inception", "Solomon Smith")
    assert deleted, msg
    assert r.zcard("notes:all") == 0
    assert r.hgetall("note:" + note_ids[0]) == {}
    assert not r.sismember("films", "Inception")
    assert not r.sismember("watchers", "Solomon Smith")


def test_delete_note_case_insensitive_title(r):
    save_note(r, "Inception", "Solomon Smith", 8)
    ok, _ = delete_note(r, "inception", "Solomon Smith")
    assert ok
    assert r.zcard("notes:all") == 0


def test_delete_note_missing(r):
    ok, msg = delete_note(r, "Missing", "Solomon Smith")
    assert not ok
    assert "No note found" in msg
