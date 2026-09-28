"""Pure comparison of an incoming item against stored state.

Rules:
- A field the incoming item leaves empty never erases a stored value.
- Same source kind, different value -> UPDATED (the source is authoritative for itself).
- Different source kind, different official deadline -> CONFLICT. Never auto-resolved.
"""

from __future__ import annotations

from agents.gandalf.models import (
    TRACKED_FIELDS,
    AcademicItem,
    DiffOutcome,
    DiffResult,
    FieldChange,
)


def _norm(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def compare(
    stored: dict[str, str | None] | None,
    stored_source_kind: str | None,
    incoming: AcademicItem,
) -> DiffResult:
    """``stored`` maps tracked field name -> normalized string, or None if unseen."""
    if stored is None:
        return DiffResult(outcome=DiffOutcome.NEW, key=incoming.key)

    changes: list[FieldChange] = []
    for field in TRACKED_FIELDS:
        new = _norm(getattr(incoming, field))
        old = stored.get(field)
        if new is None or new == old:
            continue
        changes.append(FieldChange(field=field, old=old, new=new))

    if not changes:
        return DiffResult(outcome=DiffOutcome.UNCHANGED, key=incoming.key)

    cross_source = stored_source_kind != incoming.source_kind
    deadline_differs = any(c.field == "official_due_at" and c.old is not None for c in changes)
    outcome = DiffOutcome.CONFLICT if cross_source and deadline_differs else DiffOutcome.UPDATED
    return DiffResult(outcome=outcome, key=incoming.key, changes=changes)
