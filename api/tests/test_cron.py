"""The cronjob tool — create, list, remove — and the worker that fires a job once it is due."""
from __future__ import annotations

import asyncio

from kotoba.core.cron import _tick
from kotoba.db.database import Database
from kotoba.tools import ToolContext
from kotoba.tools.action import cronjob


def _db(tmp_path):
    return Database("sqlite:///" + str(tmp_path / "cron.db"))


def test_create_list_remove(tmp_path):
    async def go():
        db = _db(tmp_path)
        await db.connect()
        try:
            ctx = ToolContext(db=db, session_id="s", mode="work")
            created = await cronjob.execute(
                {"action": "create", "message": "drink water", "in_minutes": 60}, ctx
            )
            listed = await cronjob.execute({"action": "list"}, ctx)
            jobs = await db.list_cronjobs()
            jid = jobs[0]["id"]
            removed = await cronjob.execute({"action": "remove", "id": jid[:6]}, ctx)
            after = await db.list_cronjobs()
            return created, listed, removed, after
        finally:
            await db.close()

    created, listed, removed, after = asyncio.run(go())
    assert "Reminder set" in created and "drink water" in created
    assert "drink water" in listed
    assert "Cancelled" in removed
    assert after == []


def test_create_requires_time(tmp_path):
    async def go():
        db = _db(tmp_path)
        await db.connect()
        try:
            ctx = ToolContext(db=db, session_id="s", mode="work")
            return await cronjob.execute({"action": "create", "message": "no time given"}, ctx)
        finally:
            await db.close()

    assert asyncio.run(go()) is None  # graceful failure: no due time was given


def test_worker_fires_due_job(tmp_path):
    """A job due in the past fires once: emits a reminder event + gets marked fired."""
    from kotoba.core import events

    async def go():
        db = _db(tmp_path)
        await db.connect()
        try:
            await db.insert_cronjob(message="stand up", due_at="2000-01-01 00:00:00", session_id="sess1")
            q = events.register("sess1")
            await _tick(db)
            frame = q.get_nowait()
            still_due = await db.due_cronjobs()
            return frame, still_due
        finally:
            events.unregister("sess1")
            await db.close()

    frame, still_due = asyncio.run(go())
    assert frame["type"] == "task" and frame["kind"] == "reminder" and frame["message"] == "stand up"
    assert still_due == []


def test_recurring_job_reschedules(tmp_path):
    """A recurring job that fires is rescheduled into the future: no longer due, still listed."""
    async def go():
        db = _db(tmp_path)
        await db.connect()
        try:
            await db.insert_cronjob(
                message="daily standup", due_at="2000-01-01 00:00:00",
                session_id="sess2", recurring="daily",
            )
            from kotoba.core import events

            events.register("sess2")
            await _tick(db)
            return await db.due_cronjobs(), await db.list_cronjobs()
        finally:
            from kotoba.core import events

            events.unregister("sess2")
            await db.close()

    due, listed = asyncio.run(go())
    assert due == []
    assert any(j["message"] == "daily standup" for j in listed)


def test_cron_due_time_bounds():
    """Due-time bounds, from an audit: nothing may overflow and nothing may be scheduled in the past.

    A huge offset must be refused rather than raising OverflowError, a negative one refused as past,
    an absurdly long numeric string refused, an unparseable date refused — and an ordinary offset
    still accepted."""
    from kotoba.tools.action.cronjob import _parse_due
    assert _parse_due({"in_minutes": 10**18}) is None
    assert _parse_due({"in_minutes": -100000}) is None
    assert _parse_due({"in_minutes": "9" * 50}) is None
    assert _parse_due({"in_minutes": 90}) is not None
    assert _parse_due({"due_at": "not-a-date"}) is None


def test_a_tick_with_nobody_listening_leaves_the_job_due(tmp_path):
    """Delivery is per-process and in memory. A process that claims a job it has nobody to hand to
    settles it against a queue no one reads, and the reminder is gone — so it must not claim."""
    from kotoba.core import events

    async def go():
        db = _db(tmp_path)
        await db.connect()
        try:
            await db.insert_cronjob(message="beber agua", due_at="2000-01-01 00:00:00",
                                    session_id="sess1")
            events.event_queues.clear()
            await _tick(db)
            still_due = len(await db.due_cronjobs())

            queue = events.register("someone")
            await _tick(db)
            fired = queue.get_nowait()
            events.unregister("someone", queue)
            return still_due, len(await db.due_cronjobs()), fired
        finally:
            await db.close()

    due_before, due_after, fired = asyncio.run(go())
    assert due_before == 1, "a tick with no listener must leave it for whoever comes online"
    assert due_after == 0
    assert fired["kind"] == "reminder" and fired["message"] == "beber agua"
