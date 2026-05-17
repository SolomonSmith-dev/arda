from __future__ import annotations

from agents.conduct import CONDUCT_PROMPT


def test_conduct_prompt_loads_from_doc():
    assert isinstance(CONDUCT_PROMPT, str)
    assert len(CONDUCT_PROMPT) > 200


def test_conduct_prompt_contains_core_rules():
    text = CONDUCT_PROMPT.lower()
    # Core themes from the distilled openclaw AGENTS.md
    assert "when to speak" in text
    assert "stay quiet" in text
    assert "privacy" in text
    assert "secrets" in text
