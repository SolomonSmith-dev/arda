from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from agents.galadriel.scheduler import Schedule, is_one_shot, next_run_ms


def test_cron_next_run_is_in_future_within_window():
    sched = Schedule(kind="cron", expr="*/5 * * * *", tz="UTC")
    now_ms = int(time.time() * 1000)
    nxt = next_run_ms(sched, now_ms)
    assert nxt is not None
    assert nxt > now_ms
    assert nxt <= now_ms + 5 * 60 * 1000 + 1000


def test_cron_daily_at_8am_pacific():
    sched = Schedule(kind="cron", expr="0 8 * * *", tz="America/Los_Angeles")
    # 2026-05-09 14:00 UTC = 07:00 Pacific (PDT). Next 08:00 PT = 15:00 UTC.
    now_ms = int(datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc).timestamp() * 1000)
    nxt = next_run_ms(sched, now_ms)
    assert nxt == int(datetime(2026, 5, 9, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)


def test_cron_weekly_monday_8am_pacific_skips_to_next_monday():
    sched = Schedule(kind="cron", expr="0 8 * * 1", tz="America/Los_Angeles")
    # 2026-05-12 (Tuesday) 18:00 UTC = 11:00 Pacific. Next Monday is 2026-05-18.
    now_ms = int(datetime(2026, 5, 12, 18, 0, tzinfo=timezone.utc).timestamp() * 1000)
    nxt = next_run_ms(sched, now_ms)
    expected = int(datetime(2026, 5, 18, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert nxt == expected


def test_cron_missing_expr_raises():
    with pytest.raises(ValueError, match="requires expr"):
        next_run_ms(Schedule(kind="cron"))


def test_at_future_returns_target_ms():
    target = datetime.now(timezone.utc) + timedelta(hours=1)
    sched = Schedule(kind="at", at_iso=target.isoformat())
    nxt = next_run_ms(sched)
    assert nxt is not None
    assert abs(nxt - int(target.timestamp() * 1000)) < 100


def test_at_past_returns_none():
    target = datetime.now(timezone.utc) - timedelta(hours=1)
    sched = Schedule(kind="at", at_iso=target.isoformat())
    assert next_run_ms(sched) is None


def test_at_naive_iso_uses_schedule_tz():
    # 2030-01-01 09:00 in Pacific = 17:00 UTC
    sched = Schedule(kind="at", at_iso="2030-01-01T09:00:00", tz="America/Los_Angeles")
    nxt = next_run_ms(sched)
    expected = int(datetime(2030, 1, 1, 17, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert nxt == expected


def test_at_missing_iso_raises():
    with pytest.raises(ValueError, match="requires at_iso"):
        next_run_ms(Schedule(kind="at"))


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown schedule kind"):
        next_run_ms(Schedule(kind="bogus"))  # type: ignore[arg-type]


def test_is_one_shot():
    assert is_one_shot(Schedule(kind="at", at_iso="2030-01-01T00:00:00Z"))
    assert not is_one_shot(Schedule(kind="cron", expr="0 0 * * *"))
