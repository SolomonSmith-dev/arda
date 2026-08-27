"""D1 / V6: strip leaked speaker prefixes from assistant text."""

from __future__ import annotations

import pytest

from agents.tombombadil.agent import _history_messages, _strip_leaked_speaker_prefix
from agents.tombombadil.memory import Turn


def test_strip_literal_viewer_prefix():
    assert _strip_leaked_speaker_prefix("[viewer] hey there") == "hey there"


def test_strip_display_name_and_mention_style():
    assert _strip_leaked_speaker_prefix("[Solomon Smith] Hello") == "Hello"
    assert _strip_leaked_speaker_prefix("[@Solomon Smith] Hello Patrick!") == "Hello Patrick!"


def test_strip_preserves_mock_marker():
    assert _strip_leaked_speaker_prefix("[mock:claude-haiku] echo hello") == (
        "[mock:claude-haiku] echo hello"
    )
    assert _strip_leaked_speaker_prefix("[mock] reply") == "[mock] reply"


def test_strip_noop_when_clean():
    assert _strip_leaked_speaker_prefix("Just a normal reply.") == "Just a normal reply."
    assert _strip_leaked_speaker_prefix("") == ""


def test_history_messages_strips_stale_assistant_prefix():
    turns = [
        Turn(role="user", viewer="Brian", discord_id="1", content="hi", ts=1),
        Turn(
            role="assistant",
            viewer="Tom",
            discord_id="0",
            content="[Solomon Smith] old leak",
            ts=2,
        ),
    ]
    msgs = _history_messages(turns)
    assert msgs[0]["content"] == "[Brian] hi"
    assert msgs[1]["content"] == "old leak"


# ----- review findings: over-stripping and blank results ------------------

@pytest.mark.parametrize(
    "content",
    [
        "[Spoilers] The ending recontextualises everything.",
        "[1] See my note above.",
        "[ ] pick a film for Thursday",
        "[note\ncontext] body",
        "[Criterion] just announced a sale",
    ],
)
def test_does_not_strip_legitimate_bracketed_openings(content):
    """A film-club bot plausibly opens with "[Spoilers] ...". The old regex
    matched any bracketed token up to 64 chars, so it silently ate these.
    Bare brackets now only strip for names the model could be imitating.
    """
    assert _strip_leaked_speaker_prefix(content) == content


def test_mention_form_strips_any_name():
    """``[@Anything] `` is mention-shaped and never legitimate content, so it
    strips regardless of whether the name is a known speaker."""
    assert _strip_leaked_speaker_prefix("[@Spoilers] hi") == "hi"
    assert _strip_leaked_speaker_prefix("[@Someone Unknown] hi") == "hi"


def test_prefix_only_history_row_is_dropped_not_sent_empty():
    """A pre-V6 row that is nothing but a prefix strips to "". Appending an
    empty assistant block makes Anthropic 400, hard-failing every turn in
    the scope until the 7-day TTL expires, so the turn is dropped instead.
    """
    turns = [
        Turn(role="user", viewer="Brian", discord_id="1", content="hi", ts=1),
        Turn(role="assistant", viewer="Tom", discord_id="0", content="[viewer] ", ts=2),
    ]
    msgs = _history_messages(turns)
    assert all(m["content"].strip() for m in msgs), msgs
    assert [m["role"] for m in msgs] == ["user"]
