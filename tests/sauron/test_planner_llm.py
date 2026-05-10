"""LLM-classifier path tests for Sauron.

Validates that the public ``classify()`` and ``plan()`` keep working
when:

- ``CLAUDE_API_KEY`` is empty (regex fallback).
- The Claude client returns a well-formed token.
- The Claude client returns garbage (fallback to regex).
- The Claude client raises (fallback to regex).
"""

from __future__ import annotations

import pytest

from agents.sauron import planner


class _StubResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubLLM:
    def __init__(self, *, content: str | None = None, raise_exc: Exception | None = None) -> None:
        self._content = content
        self._raise = raise_exc

    def invoke(self, _messages):
        if self._raise is not None:
            raise self._raise
        return _StubResponse(self._content or "")


@pytest.fixture(autouse=True)
def reset_llm_cache():
    planner._llm.cache_clear()
    yield
    planner._llm.cache_clear()


def test_classify_falls_back_to_regex_without_claude_key(monkeypatch):
    monkeypatch.setattr(planner.settings, "claude_api_key", "")
    # Regex classifier matches "film" keyword.
    assert planner.classify("recommend a film for tonight") == "tombombadil"
    assert planner.classify("ls /tmp") == "earendil"


def test_classify_uses_llm_response_when_key_present(monkeypatch):
    monkeypatch.setattr(planner.settings, "claude_api_key", "sk-test")
    monkeypatch.setattr(planner, "_llm", lambda: _StubLLM(content="tombombadil"))
    assert planner.classify("anything goes here") == "tombombadil"


def test_classify_falls_back_when_llm_returns_garbage(monkeypatch):
    monkeypatch.setattr(planner.settings, "claude_api_key", "sk-test")
    monkeypatch.setattr(planner, "_llm", lambda: _StubLLM(content="???"))
    # Garbage -> regex; "ls /tmp" matches ops -> earendil.
    assert planner.classify("ls /tmp") == "earendil"


def test_classify_falls_back_when_llm_raises(monkeypatch):
    monkeypatch.setattr(planner.settings, "claude_api_key", "sk-test")
    monkeypatch.setattr(planner, "_llm", lambda: _StubLLM(raise_exc=RuntimeError("upstream 500")))
    # Falls through to regex; "movie" matches FILM_KEYWORDS.
    assert planner.classify("what's a good movie to watch tonight?") == "tombombadil"


def test_classify_normalises_trailing_punctuation(monkeypatch):
    monkeypatch.setattr(planner.settings, "claude_api_key", "sk-test")
    monkeypatch.setattr(planner, "_llm", lambda: _StubLLM(content="tombombadil."))
    assert planner.classify("hi") == "tombombadil"


def test_plan_routes_through_classify(monkeypatch):
    monkeypatch.setattr(planner.settings, "claude_api_key", "")
    p = planner.plan("disk usage on /var?")
    assert p.intent == "earendil"
    assert len(p.subtasks) == 1
    assert p.subtasks[0].specialist == "earendil"
    assert p.subtasks[0].payload == {"message": "disk usage on /var?"}


def test_classify_regex_preserved_as_public_helper():
    assert planner.classify_regex("recommend a kurosawa film") == "tombombadil"
