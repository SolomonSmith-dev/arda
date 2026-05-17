from __future__ import annotations

from agents.tombombadil.fact_extractor import extract
from agents.tombombadil.identity import Tier, Viewer

SOLOMON = Viewer(
    discord_id="111",
    discord_name="Solomon",
    canonical_name="Solomon Smith",
    tier=Tier.SOLOMON,
)
STRANGER = Viewer(
    discord_id="999",
    discord_name="randomuser",
    canonical_name=None,
    tier=Tier.STRANGER,
)


def test_stop_mentioning_films_sets_suppress_pref():
    facts = extract("hey, please stop mentioning films for a bit", "ok", SOLOMON)
    assert facts.prefs.get("suppress_films") == "1"
    assert any("stop mentioning films" in f.lower() for f in facts.free_facts)


def test_dont_talk_about_movies_also_triggers():
    facts = extract("don't talk about movies anymore", "fine", SOLOMON)
    assert facts.prefs.get("suppress_films") == "1"


def test_unsuppress_resets_pref():
    facts = extract("you can mention films again", "ok!", SOLOMON)
    assert facts.prefs.get("suppress_films") == "0"


def test_rated_phrasing_produces_note_draft():
    facts = extract("I rated Inception 9/10 last night", "noted", SOLOMON)
    assert facts.notes, "expected at least one NoteDraft"
    draft = facts.notes[0]
    assert draft.film.lower().startswith("inception")
    assert draft.rating == 9.0
    assert draft.viewer == "Solomon Smith"


def test_remember_that_pattern_captures_free_fact():
    facts = extract(
        "remember that I'm allergic to subtitles. it ruins immersion.",
        "got it",
        SOLOMON,
    )
    assert any("allergic to subtitles" in f.lower() for f in facts.free_facts)


def test_strong_opinion_captured():
    facts = extract("I absolutely love Tarkovsky.", "noted", SOLOMON)
    assert any("tarkovsky" in f.lower() for f in facts.free_facts)


def test_empty_input_produces_empty_facts():
    facts = extract("", "", SOLOMON)
    assert facts.empty


def test_stranger_does_not_get_note_draft():
    # Strangers should not produce NoteDrafts because we don't know
    # which canonical viewer to file them under.
    facts = extract("I rated Inception 9/10", "noted", STRANGER)
    assert facts.notes == []


def test_extracted_facts_combine_signals():
    facts = extract(
        "stop mentioning films. also, for the record, I love Tarkovsky.",
        "ok",
        SOLOMON,
    )
    assert facts.prefs.get("suppress_films") == "1"
    assert any("tarkovsky" in f.lower() for f in facts.free_facts)


def test_rating_clamped_to_zero_to_ten():
    facts = extract("I rated The Room 12/10 ironically", "lol", SOLOMON)
    if facts.notes:
        assert 0.0 <= facts.notes[0].rating <= 10.0


def test_pronoun_film_reference_is_dropped():
    """Catch the live regression: 'I just watched Interstellar. I would
    rate it 1/10.' shouldn't produce a NoteDraft for film='it' since we
    have no conversation memory to resolve the pronoun.
    """
    facts = extract(
        "I just watched Interstellar. I would rate it 1/10.",
        "noted",
        SOLOMON,
    )
    pronoun_drafts = [n for n in facts.notes if n.film.lower() in ("it", "this", "that")]
    assert pronoun_drafts == []


def test_pronoun_dropped_singular_form():
    facts = extract("I'd rate this 7/10", "noted", SOLOMON)
    assert facts.notes == []
