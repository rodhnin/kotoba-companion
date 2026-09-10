"""cronjob(create) must be IDEMPOTENT. ElevenLabs fires redundant turns (turn-taking / echo while the call
is live), and a small model re-runs the same tool on each — observed live as ONE "remind me to breathe"
turning into 7 duplicate reminders. Creating a near-identical reminder (same session, overlapping due time,
similar message) must return the existing one instead of inserting a duplicate."""
from __future__ import annotations

import asyncio
import tempfile


from kotoba.tools.action import cronjob


class _Ctx:
    def __init__(self, db, session_id="s1"):
        self.db = db
        self.session_id = session_id


def test_identical_reminder_not_duplicated():
    async def go():
        from kotoba.db.database import Database

        db = Database("sqlite:///" + tempfile.mktemp(suffix=".db"))
        await db.connect()
        ctx = _Ctx(db)
        r1 = await cronjob.execute({"action": "create", "message": "respira profundo", "in_minutes": 1}, ctx)
        # the storm: same intent re-fired on redundant EL turns (varied case / wording)
        r2 = await cronjob.execute({"action": "create", "message": "Respira profundo", "in_minutes": 1}, ctx)
        r3 = await cronjob.execute({"action": "create", "message": "respira profundo, solo una vez", "in_minutes": 1}, ctx)
        jobs = await db.list_cronjobs()
        assert len(jobs) == 1, f"expected 1 reminder, got {len(jobs)}: {[j['message'] for j in jobs]}"
        assert r1 and r2 and r3  # each call still returns a friendly confirmation (no error)

    asyncio.new_event_loop().run_until_complete(go())


def test_distinct_reminders_still_create():
    async def go():
        from kotoba.db.database import Database

        db = Database("sqlite:///" + tempfile.mktemp(suffix=".db"))
        await db.connect()
        ctx = _Ctx(db)
        await cronjob.execute({"action": "create", "message": "tomar agua", "in_minutes": 1}, ctx)
        await cronjob.execute({"action": "create", "message": "llamar a mamá", "in_minutes": 1}, ctx)
        jobs = await db.list_cronjobs()
        # genuinely different reminders at a similar time are NOT collapsed
        assert len(jobs) == 2, f"expected 2 distinct reminders, got {len(jobs)}"

    asyncio.new_event_loop().run_until_complete(go())


def test_explicit_one_time_overrides_model_recurring():
    """The small model reflexively tags 'daily'/'hourly'; an EXPLICIT one-time request (the user's own
    words, carried on ctx.user_text) must win → the stored reminder is NOT recurring."""
    async def go():
        from kotoba.db.database import Database

        db = Database("sqlite:///" + tempfile.mktemp(suffix=".db"))
        await db.connect()
        ctx = _Ctx(db)
        ctx.user_text = "Recuérdame en 1 minuto que tome té, una sola vez por favor."
        await cronjob.execute({"action": "create", "message": "Tomar té", "in_minutes": 1, "recurring": "daily"}, ctx)
        jobs = await db.list_cronjobs()
        assert len(jobs) == 1 and not jobs[0]["recurring"], f"recurring should be cleared: {jobs}"

    asyncio.new_event_loop().run_until_complete(go())


def test_genuine_recurring_is_kept():
    """When the user actually asks to repeat, recurring stays."""
    async def go():
        from kotoba.db.database import Database

        db = Database("sqlite:///" + tempfile.mktemp(suffix=".db"))
        await db.connect()
        ctx = _Ctx(db)
        ctx.user_text = "recuérdame tomar agua todos los días"
        await cronjob.execute({"action": "create", "message": "Tomar agua", "in_minutes": 1, "recurring": "daily"}, ctx)
        jobs = await db.list_cronjobs()
        assert jobs[0]["recurring"] == "daily", f"recurring should be kept: {jobs}"

    asyncio.new_event_loop().run_until_complete(go())


def test_same_message_far_apart_creates_two():
    async def go():
        from kotoba.db.database import Database

        db = Database("sqlite:///" + tempfile.mktemp(suffix=".db"))
        await db.connect()
        ctx = _Ctx(db)
        await cronjob.execute({"action": "create", "message": "estirar", "in_minutes": 1}, ctx)
        await cronjob.execute({"action": "create", "message": "estirar", "in_minutes": 120}, ctx)  # 2h later
        jobs = await db.list_cronjobs()
        assert len(jobs) == 2, "same message at clearly different times is a real second reminder"

    asyncio.new_event_loop().run_until_complete(go())
