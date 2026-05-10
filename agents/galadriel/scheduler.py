"""Schedule expansion: given a job spec, return when it should next fire.

Pure logic, no I/O. Two schedule kinds:
  - ``cron``: standard 5-field cron expression in the named tz
  - ``at``:   one-shot ISO 8601 datetime
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from croniter import croniter

ScheduleKind = Literal["cron", "at"]


@dataclass(frozen=True)
class Schedule:
    kind: ScheduleKind
    expr: str | None = None
    tz: str = "UTC"
    at_iso: str | None = None


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def next_run_ms(schedule: Schedule, now_ms: int | None = None) -> int | None:
    """Epoch-ms of the next firing, or ``None`` for an ``at`` whose target has passed."""
    if now_ms is None:
        now_ms = _now_ms()

    if schedule.kind == "cron":
        if not schedule.expr:
            raise ValueError("cron schedule requires expr")
        tz = ZoneInfo(schedule.tz)
        base = datetime.fromtimestamp(now_ms / 1000, tz=tz)
        it = croniter(schedule.expr, base)
        nxt: datetime = it.get_next(datetime)
        return int(nxt.timestamp() * 1000)

    if schedule.kind == "at":
        if not schedule.at_iso:
            raise ValueError("at schedule requires at_iso")
        target = datetime.fromisoformat(schedule.at_iso)
        if target.tzinfo is None:
            target = target.replace(tzinfo=ZoneInfo(schedule.tz))
        target_ms = int(target.timestamp() * 1000)
        if target_ms < now_ms:
            return None
        return target_ms

    raise ValueError(f"unknown schedule kind: {schedule.kind!r}")


def is_one_shot(schedule: Schedule) -> bool:
    return schedule.kind == "at"
