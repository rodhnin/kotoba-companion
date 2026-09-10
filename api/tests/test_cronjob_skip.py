"""cronjob(skip) — 'done for this occurrence' on a recurring reminder, without killing the series.

The calendar is the only shared state, so acknowledging an occurrence means moving `due_at` one period
along its own cadence — and the trap is a job that ALREADY fired this period: its due sits a full
period out minus the minutes since, and pushing that eats the FOLLOWING occurrence. The guard is the
half-period line, pinned here against a REAL cron fire because the naive guard ((due - now) >= period)
holds only at the exact instant of the fire and fails a minute later.
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone

from kotoba.core import cron, events, pending_reminder
from kotoba.db.database import Database
from kotoba.tools.action import cronjob


class _Ctx:
    def __init__(self, db, session_id="s1"):
        self.db = db
        self.session_id = session_id


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def _db():
    db = Database("sqlite:///" + (tempfile.mkdtemp() + "/kotoba.db"))
    await db.connect()
    return db


def test_skip_pushes_an_imminent_occurrence_one_period():
    async def go():
        db = await _db()
        ctx = _Ctx(db)
        slot = _now() + timedelta(hours=6)
        await db.insert_cronjob(message="llamar a tu hermana", due_at=_fmt(slot),
                                session_id="s1", recurring="daily")
        job = (await db.list_cronjobs())[0]
        out = await cronjob.execute({"action": "skip", "id": job["id"][:6]}, ctx)
        after = (await db.list_cronjobs())[0]
        await db.close()
        return out, after["due_at"], _fmt(slot + timedelta(days=1))

    out, due, expected = asyncio.run(go())
    assert out.startswith("Done for now"), out
    assert due == expected, "one period along its OWN slot — no drift, no extra day"


def test_skip_after_a_real_fire_does_not_eat_tomorrow():
    """The trap, driven end to end: cron fires and reschedules, the user says 'ya la llamé' minutes
    later, the model calls skip. due_at must not move again — and the stashed, still-unvoiced copy
    must be discarded so she doesn't remind them right after they said it's done."""
    async def go():
        db = await _db()
        events.event_queues.clear()
        q = events.register("live")
        try:
            await db.insert_cronjob(message="llamar a tu hermana",
                                    due_at=_fmt(_now() - timedelta(minutes=5)),
                                    session_id="live", recurring="daily")
            await cron._tick(db)
            fired = (await db.list_cronjobs())[0]
            assert pending_reminder.has_pending("live"), "the fire must have stashed a copy"
            out = await cronjob.execute({"action": "skip", "id": fired["id"][:6]}, _Ctx(db))
            after = (await db.list_cronjobs())[0]
            return out, fired["due_at"], after["due_at"], pending_reminder.has_pending("live")
        finally:
            events.unregister("live", q)
            pending_reminder.clear("live")
            await db.close()

    out, before, after, stash_left = asyncio.run(go())
    assert after == before, f"skip ate the NEXT occurrence: {before} -> {after}"
    assert "next one is" in out and before in out, out
    assert stash_left is False, "'I already did it' must silence the undelivered copy"


def test_skip_finds_the_job_by_message_and_touches_only_its_stash():
    async def go():
        db = await _db()
        ctx = _Ctx(db)
        await db.insert_cronjob(message="llamar a tu hermana", due_at=_fmt(_now() + timedelta(hours=2)),
                                session_id="s1", recurring="daily")
        pending_reminder.clear("s1")
        pending_reminder.add("s1", "llamar a tu hermana")
        pending_reminder.add("s1", "tomar la medicina")
        out = await cronjob.execute({"action": "skip", "message": "llamar a su hermana"}, ctx)
        left = list(pending_reminder._pending.get("s1", []))
        pending_reminder.clear("s1")
        await db.close()
        return out, left

    out, left = asyncio.run(go())
    assert out.startswith("Done for now"), out
    assert left == ["tomar la medicina"], "the other pending reminder must be untouched"


def test_skip_on_a_one_time_reminder_cancels_it():
    async def go():
        db = await _db()
        await db.insert_cronjob(message="recoger el paquete", due_at=_fmt(_now() + timedelta(hours=3)),
                                session_id="s1", recurring=None)
        job = (await db.list_cronjobs())[0]
        out = await cronjob.execute({"action": "skip", "id": job["id"][:6]}, _Ctx(db))
        jobs = await db.list_cronjobs()
        await db.close()
        return out, jobs

    out, jobs = asyncio.run(go())
    assert "one-time" in out and "off" in out, out
    assert jobs == [], "a one-shot the user already did has nothing left to fire"


def test_skip_without_a_unique_target_asks_instead_of_guessing():
    async def go():
        db = await _db()
        ctx = _Ctx(db)
        await db.insert_cronjob(message="tomar agua por la mañana",
                                due_at=_fmt(_now() + timedelta(hours=1)), session_id="s1", recurring="daily")
        await db.insert_cronjob(message="tomar agua por la noche",
                                due_at=_fmt(_now() + timedelta(hours=9)), session_id="s1", recurring="daily")
        bare = await cronjob.execute({"action": "skip"}, ctx)
        ambiguous = await cronjob.execute({"action": "skip", "message": "tomar agua"}, ctx)
        jobs = await db.list_cronjobs()
        await db.close()
        return bare, ambiguous, jobs

    bare, ambiguous, jobs = asyncio.run(go())
    assert "Which reminder" in bare and "Which reminder" in ambiguous
    assert len(jobs) == 2 and all(not j["fired_at"] for j in jobs), "guessing would have moved one"


def test_remove_discards_the_undelivered_stashed_copy():
    async def go():
        db = await _db()
        await db.insert_cronjob(message="llamar al banco", due_at=_fmt(_now() + timedelta(hours=1)),
                                session_id="s1", recurring=None)
        job = (await db.list_cronjobs())[0]
        pending_reminder.clear("s1")
        pending_reminder.add("s1", "llamar al banco")
        out = await cronjob.execute({"action": "remove", "id": job["id"][:6]}, _Ctx(db))
        left = pending_reminder.has_pending("s1")
        pending_reminder.clear("s1")
        await db.close()
        return out, left

    out, left = asyncio.run(go())
    assert out.startswith("Cancelled"), out
    assert left is False, "she voiced a reminder the user had just cancelled"
