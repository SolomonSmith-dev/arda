"""Pydantic records for Gandalf. Official data only ever comes from a source.

An item's official deadline must carry evidence (the exact source text), and
datetimes must be timezone-aware. Recommended personal deadlines live in a
separate table and are never written through these models.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ItemType = Literal[
    "ASSIGNMENT",
    "QUIZ",
    "EXAM",
    "LECTURE",
    "READING",
    "ANNOUNCEMENT",
    "GRADE",
    "SYLLABUS_CHANGE",
    "OTHER",
]
Flag = Literal[
    "CONFLICT",
    "NEEDS_REVIEW",
    "DEADLINE_UNCERTAIN",
    "ACCESS_FAILED",
    "AUTH_REQUIRED",
]

# Fields whose change is recorded and can trigger UPDATED / CONFLICT.
TRACKED_FIELDS: tuple[str, ...] = ("title", "description", "official_due_at", "points")


class DiffOutcome(StrEnum):
    NEW = "NEW"
    UNCHANGED = "UNCHANGED"
    UPDATED = "UPDATED"
    REMOVED = "REMOVED"
    CONFLICT = "CONFLICT"


class Evidence(BaseModel):
    field: str
    quote: str = Field(min_length=1)


class AcademicItem(BaseModel):
    course_id: str
    lms_id: str | None = None
    type: ItemType
    title: str = Field(min_length=1)
    description: str | None = None
    official_due_at: datetime | None = None
    deadline_uncertain: bool = False
    points: float | None = None
    source_kind: str
    source_url: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _deadline_is_grounded(self) -> AcademicItem:
        if self.official_due_at is None:
            return self
        if self.official_due_at.tzinfo is None:
            raise ValueError("official_due_at must be timezone-aware")
        if not any(e.field == "official_due_at" for e in self.evidence):
            raise ValueError("official_due_at requires evidence for that field")
        return self

    @property
    def key(self) -> str:
        """Identity used to dedupe across scans."""
        if self.lms_id:
            return f"{self.course_id}:{self.lms_id}"
        return f"{self.course_id}:{self.type}:{' '.join(self.title.lower().split())}"


class FieldChange(BaseModel):
    field: str
    old: str | None
    new: str | None


class DiffResult(BaseModel):
    outcome: DiffOutcome
    key: str
    changes: list[FieldChange] = Field(default_factory=list)
