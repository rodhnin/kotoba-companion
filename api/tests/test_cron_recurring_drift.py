"""Recurring reminders must keep their time-of-day (reschedule from the job's own due_at, not from now()),
and a backend that was down across several periods must NOT fire a backlog burst — it advances to the next
future slot. Plus: finished/cancelled jobs purge after a cutoff."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone

from kotoba.core.cron import _next_due, _RECUR


def test_daily_keeps_time_of_day_no_drift():
    """The job is due at 09:00:00 and the poller only picks it up at 09:00:28. The next fire must be
    09:00:00 the NEXT day, not 09:00:28 — rescheduling from now() drifts the job later every day."""
    now = datetime(2026, 6, 20, 9, 0, 28)
    nxt = _next_due("2026-06-20 09:00:00", _RECUR["daily"], now)
    assert nxt == "2026-06-21 09:00:00", nxt


def test_backend_down_skips_missed_periods_no_burst():
    """A daily job was due at 08:00 and the backend stayed down for five days. It must advance to the
    next FUTURE slot — 08:00 tomorrow — rather than replay the five days it missed as a burst."""
    now = datetime(2026, 6, 20, 9, 0, 0)
    nxt = _next_due("2026-06-15 08:00:00", _RECUR["daily"], now)
    assert nxt == "2026-06-21 08:00:00", nxt


def test_hourly_steps_one_hour():
    now = datetime(2026, 6, 20, 9, 0, 5)
    nxt = _next_due("2026-06-20 09:00:00", _RECUR["hourly"], now)
    assert nxt == "2026-06-20 10:00:00", nxt


def test_unparseable_due_falls_back_to_now_based():
    now = datetime(2026, 6, 20, 9, 0, 0)
    nxt = _next_due("not-a-date", _RECUR["daily"], now)
    assert nxt == "2026-06-21 09:00:00", nxt  # now + 1 day


def test_purge_removes_old_finished_keeps_recent_and_recurring():
    async def go():
        from kotoba.db.database import Database

        db = Database("sqlite:///" + (tempfile.mkdtemp() + "/kotoba.db"))
        await db.connect()
        old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        # old fired one-shot (purge), old cancelled (purge), recent fired (keep), active recurring (keep)
        await db.conn.execute(
            "INSERT INTO cronjobs (id, message, due_at, recurring, fired_at, active, created_at) "
            "VALUES ('a','old fired',:d,NULL,:d,1,:d),"
            "       ('b','old cancelled',:d,NULL,NULL,0,:d),"
            "       ('c','recent fired',:r,NULL,:r,1,:r),"
            "       ('d','active recurring',:r,'daily',NULL,1,:r)",
            {"d": old, "r": recent},
        )
        await db.conn.commit()
        removed = await db.purge_finished_cronjobs(30)
        assert removed == 2, removed
        ids = {r["id"] for r in await db.list_cronjobs()}  # list shows active=1
        assert "c" in ids and "d" in ids and "a" not in ids and "b" not in ids

    asyncio.new_event_loop().run_until_complete(go())
