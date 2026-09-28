from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agents.gandalf.models import AcademicItem, DiffOutcome, Evidence
from agents.gandalf.store import GandalfStore


def _item(
    due: datetime | None = datetime(2026, 10, 12, 23, 59, tzinfo=UTC),
    *,
    source_kind: str = "canvas",
    lms_id: str | None = "a4",
    **kw,
) -> AcademicItem:
    evidence = [Evidence(field="official_due_at", quote="Due Oct 12 at 11:59pm")] if due else []
    return AcademicItem(
        course_id="cs210",
        lms_id=lms_id,
        type="ASSIGNMENT",
        title="Homework 4",
        official_due_at=due,
        source_kind=source_kind,
        evidence=evidence,
        **kw,
    )


@pytest.fixture
def store():
    s = GandalfStore()
    yield s
    s.close()


def test_rescan_identical_is_unchanged_and_does_not_duplicate(store):
    assert store.apply(_item()).outcome == DiffOutcome.NEW
    assert store.apply(_item()).outcome == DiffOutcome.UNCHANGED
    assert store.count_items() == 1
    assert store.changes("cs210:a4") == []
    assert len(store.evidence("cs210:a4")) == 1


def test_changed_deadline_same_source_is_updated_with_history(store):
    store.apply(_item())
    new_due = datetime(2026, 10, 14, 23, 59, tzinfo=UTC)
    result = store.apply(_item(new_due))
    assert result.outcome == DiffOutcome.UPDATED
    row = store.get_item("cs210:a4")
    assert row["official_due_at"] == new_due.isoformat()
    (change,) = store.changes("cs210:a4")
    assert change["field"] == "official_due_at"
    assert change["old_value"] == "2026-10-12T23:59:00+00:00"
    assert change["new_value"] == new_due.isoformat()


def test_cross_source_different_deadline_is_conflict_and_keeps_stored(store):
    store.apply(_item())
    other = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)
    result = store.apply(_item(other, source_kind="syllabus"))
    assert result.outcome == DiffOutcome.CONFLICT
    assert store.get_item("cs210:a4")["official_due_at"] == "2026-10-12T23:59:00+00:00"
    assert "CONFLICT" in store.flags("cs210:a4")
    assert {e["source_kind"] for e in store.evidence("cs210:a4")} == {"canvas", "syllabus"}


def test_missing_incoming_field_never_erases_stored_value(store):
    store.apply(_item(points=10))
    result = store.apply(_item(None))
    assert result.outcome == DiffOutcome.UNCHANGED
    assert store.get_item("cs210:a4")["official_due_at"] is not None


def test_deadline_uncertain_is_flagged(store):
    store.apply(_item(deadline_uncertain=True))
    assert "DEADLINE_UNCERTAIN" in store.flags("cs210:a4")


def test_no_lms_id_falls_back_to_title_key_and_needs_review(store):
    item = _item(lms_id=None)
    assert item.key == "cs210:ASSIGNMENT:homework 4"
    store.apply(item)
    store.apply(item)
    assert store.count_items() == 1
    assert "NEEDS_REVIEW" in store.flags(item.key)


def test_removed_only_after_two_missed_scans_and_never_deleted(store):
    store.apply(_item())
    assert store.complete_scan("cs210", set()) == []
    assert store.get_item("cs210:a4")["removed_at"] is None
    assert store.complete_scan("cs210", set()) == ["cs210:a4"]
    assert store.get_item("cs210:a4")["removed_at"] is not None
    assert store.count_items() == 1


def test_seen_item_resets_miss_counter(store):
    store.apply(_item())
    store.complete_scan("cs210", set())
    store.apply(_item())
    assert store.complete_scan("cs210", set()) == []


def test_deadline_without_evidence_is_rejected():
    with pytest.raises(ValidationError):
        AcademicItem(
            course_id="c", type="ASSIGNMENT", title="t", source_kind="canvas",
            official_due_at=datetime(2026, 10, 12, tzinfo=UTC),
        )


def test_naive_datetime_is_rejected():
    with pytest.raises(ValidationError):
        _item(datetime(2026, 10, 12, 23, 59))
