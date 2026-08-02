"""D1 / V6: strip leaked speaker prefixes from assistant text."""

from __future__ import annotations

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
