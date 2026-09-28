"""SQLite store for Gandalf (ADR 0008).

Nothing is ever deleted: removal sets ``removed_at``. A CONFLICT keeps the
stored value, records the incoming one in ``item_changes`` and evidence, and
raises a CONFLICT flag until a human resolves it.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from agents.gandalf.diff import compare
from agents.gandalf.models import (
    TRACKED_FIELDS,
    AcademicItem,
    DiffOutcome,
    DiffResult,
    Flag,
)

SCHEMA_VERSION = 1
MISSES_BEFORE_REMOVED = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    course_id TEXT NOT NULL,
    lms_id TEXT,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    official_due_at TEXT,
    points TEXT,
    source_kind TEXT NOT NULL,
    source_url TEXT,
    misses INTEGER NOT NULL DEFAULT 0,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    removed_at TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id),
    field TEXT NOT NULL,
    quote TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    captured_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS item_changes (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id),
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    outcome TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    detected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS item_flags (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id),
    flag TEXT NOT NULL,
    detail TEXT,
    raised_at TEXT NOT NULL,
    cleared_at TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _norm(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class GandalfStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        if self._db.execute("SELECT 1 FROM schema_version").fetchone() is None:
            self._db.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    def apply(self, item: AcademicItem) -> DiffResult:
        """Diff ``item`` against stored state and persist it, atomically."""
        with self._db:
            row = self._db.execute("SELECT * FROM items WHERE key = ?", (item.key,)).fetchone()
            stored = {f: row[f] for f in TRACKED_FIELDS} if row else None
            result = compare(stored, row["source_kind"] if row else None, item)
            now = _now()

            if row is None:
                item_id = self._insert(item, now)
                if not item.lms_id:
                    self._flag(item_id, "NEEDS_REVIEW", "no LMS id; identity is title-based", now)
            else:
                item_id = row["id"]
                self._db.execute("UPDATE items SET misses = 0 WHERE id = ?", (item_id,))
                if result.outcome != DiffOutcome.UNCHANGED:
                    self._record_changes(item_id, item, result, now)
                    if result.outcome == DiffOutcome.UPDATED:
                        self._write_fields(item_id, item, now)
                    else:
                        self._flag(item_id, "CONFLICT", self._conflict_detail(result), now)
                if row["removed_at"] is not None:
                    self._db.execute("UPDATE items SET removed_at = NULL WHERE id = ?", (item_id,))

            if item.deadline_uncertain:
                self._flag(item_id, "DEADLINE_UNCERTAIN", "deadline lacks a firm time", now)
            for ev in item.evidence:
                self._db.execute(
                    "INSERT INTO evidence (item_id, field, quote, source_kind, captured_at)"
                    " SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS ("
                    " SELECT 1 FROM evidence WHERE item_id=? AND field=? AND quote=?"
                    " AND source_kind=?)",
                    (item_id, ev.field, ev.quote, item.source_kind, now,
                     item_id, ev.field, ev.quote, item.source_kind),
                )
            return result

    def complete_scan(self, course_id: str, seen_keys: set[str]) -> list[str]:
        """Count a miss for unseen active items; remove after repeated misses."""
        removed: list[str] = []
        now = _now()
        with self._db:
            rows = self._db.execute(
                "SELECT id, key, misses FROM items WHERE course_id = ? AND removed_at IS NULL",
                (course_id,),
            ).fetchall()
            for row in rows:
                if row["key"] in seen_keys:
                    continue
                misses = row["misses"] + 1
                if misses >= MISSES_BEFORE_REMOVED:
                    self._db.execute(
                        "UPDATE items SET misses = ?, removed_at = ? WHERE id = ?",
                        (misses, now, row["id"]),
                    )
                    self._db.execute(
                        "INSERT INTO item_changes (item_id, field, old_value, new_value,"
                        " outcome, source_kind, detected_at) VALUES (?, 'removed_at', NULL, ?,"
                        " ?, 'scan', ?)",
                        (row["id"], now, DiffOutcome.REMOVED.value, now),
                    )
                    removed.append(row["key"])
                else:
                    self._db.execute(
                        "UPDATE items SET misses = ? WHERE id = ?", (misses, row["id"])
                    )
        return removed

    def get_item(self, key: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._db.execute(
            "SELECT * FROM items WHERE key = ?", (key,)
        ).fetchone()
        return row

    def count_items(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0])

    def changes(self, key: str) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT c.* FROM item_changes c JOIN items i ON i.id = c.item_id"
            " WHERE i.key = ? ORDER BY c.id",
            (key,),
        ).fetchall()

    def flags(self, key: str, *, active_only: bool = True) -> list[str]:
        clause = " AND f.cleared_at IS NULL" if active_only else ""
        rows = self._db.execute(
            "SELECT f.flag FROM item_flags f JOIN items i ON i.id = f.item_id"
            f" WHERE i.key = ?{clause} ORDER BY f.id",
            (key,),
        ).fetchall()
        return [r["flag"] for r in rows]

    def evidence(self, key: str) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT e.* FROM evidence e JOIN items i ON i.id = e.item_id"
            " WHERE i.key = ? ORDER BY e.id",
            (key,),
        ).fetchall()

    # -- internals ---------------------------------------------------------

    def _insert(self, item: AcademicItem, now: str) -> int:
        cur = self._db.execute(
            "INSERT INTO items (key, course_id, lms_id, type, title, description,"
            " official_due_at, points, source_kind, source_url, discovered_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item.key, item.course_id, item.lms_id, item.type, item.title, item.description,
             _norm(item.official_due_at), _norm(item.points), item.source_kind,
             item.source_url, now, now),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def _write_fields(self, item_id: int, item: AcademicItem, now: str) -> None:
        for field in TRACKED_FIELDS:
            value = _norm(getattr(item, field))
            if value is not None:
                self._db.execute(f"UPDATE items SET {field} = ? WHERE id = ?", (value, item_id))
        self._db.execute(
            "UPDATE items SET source_url = COALESCE(?, source_url), updated_at = ? WHERE id = ?",
            (item.source_url, now, item_id),
        )

    def _record_changes(
        self, item_id: int, item: AcademicItem, result: DiffResult, now: str
    ) -> None:
        for c in result.changes:
            self._db.execute(
                "INSERT INTO item_changes (item_id, field, old_value, new_value, outcome,"
                " source_kind, detected_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item_id, c.field, c.old, c.new, result.outcome.value, item.source_kind, now),
            )

    def _flag(self, item_id: int, flag: Flag, detail: str, now: str) -> None:
        exists = self._db.execute(
            "SELECT 1 FROM item_flags WHERE item_id = ? AND flag = ? AND cleared_at IS NULL",
            (item_id, flag),
        ).fetchone()
        if exists is None:
            self._db.execute(
                "INSERT INTO item_flags (item_id, flag, detail, raised_at) VALUES (?, ?, ?, ?)",
                (item_id, flag, detail, now),
            )

    @staticmethod
    def _conflict_detail(result: DiffResult) -> str:
        return "; ".join(f"{c.field}: {c.old!r} vs {c.new!r}" for c in result.changes)
