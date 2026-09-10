"""Two ways she told the user something that was not true.

Cancelling background work announced a FAILURE: `clear()` raced the cancelled task's own
`failed/cancelled/announced=False` write, and since `task.cancel()` can't run synchronously,
`clear()` always lost -- the next turn opened with "(Background work failed.)" for a stopped job.

A due reminder was addressed to the session that created it, but the frontend mints a fresh id on
every mount, so after any reload it went to nobody's session while the job was still consumed --
set a reminder, reload the tab, and it vanished with no error.
"""
from __future__ import annotations

import asyncio

from kotoba.core import cron, work_state


# --- 1. cancelling is not failing ------------------------------------------------------------------

def test_cancelling_work_leaves_nothing_to_announce(monkeypatch):
    """Drives the REAL work_runner. The previous version re-implemented the cancel branch inside its own
    fake coroutine, so reintroducing the bug in the runner left the whole suite green."""
    async def main():
        import kotoba.core.loop as _loop
        import kotoba.core.work_runner as wr

        sid = "cancel-clean"
        work_state.clear(sid)

        async def slow_loop(*a, **kw):
            await asyncio.sleep(30)      # still working when the user stops it
            return "never reached"

        monkeypatch.setattr(wr, "emit_task", _swallow)
        monkeypatch.setattr(_loop, "agentic_loop", slow_loop)

        work_state.start(sid, "scrape the listings")
        t = asyncio.create_task(wr._run(sid, "scrape the listings", None, {}, None))
        work_state.register_task(sid, t)
        await asyncio.sleep(0.05)

        work_state.pop_task(sid).cancel()   # what cancel_work / _leave do
        work_state.clear(sid)
        await asyncio.gather(t, return_exceptions=True)
        await asyncio.sleep(0.05)

        assert work_state.get(sid)["status"] != "failed", "the user asked for the stop; it is not a failure"
        assert work_state.has_pending_announcement(sid) is False, "nothing to announce"
        assert work_state.prompt_note(sid) == "", "the next turn must not be told the work failed"
        work_state.clear(sid)

    asyncio.run(main())


async def _swallow(*a, **kw):
    return None


def test_the_runner_closes_the_work_started_bracket_on_cancel(monkeypatch):
    """The frontend keeps the ElevenLabs call alive on work_started; without the closing frame it pings
    for ~28 minutes and every ping suppresses her speech."""
    async def main():
        import kotoba.core.loop as _loop
        import kotoba.core.work_runner as wr

        frames = []

        async def spy(session_id, kind, **data):
            frames.append((kind, data))

        async def slow_loop(*a, **kw):
            await asyncio.sleep(30)
            return "x"

        monkeypatch.setattr(wr, "emit_task", spy)
        monkeypatch.setattr(_loop, "agentic_loop", slow_loop)
        sid = "cancel-bracket"
        work_state.clear(sid)
        work_state.start(sid, "algo")
        t = asyncio.create_task(wr._run(sid, "algo", None, {}, None))
        await asyncio.sleep(0.05)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        kinds = [k for k, _ in frames]
        assert "work_started" in kinds
        done = [d for k, d in frames if k == "work_done"]
        assert done, f"the bracket was never closed: {kinds}"
        assert done[-1].get("cancelled") is True, "a stop must not read as a finished job"
        assert done[-1].get("ok") is True, "and it is not a failure either"
        work_state.clear(sid)

    asyncio.run(main())



def test_a_real_failure_is_still_announced():
    """The distinction has to cut both ways, or a genuine failure goes silent."""
    sid = "real-fail"
    work_state.clear(sid)
    work_state.start(sid, "thing")
    work_state.fail(sid, "it took too long and I stopped it")
    assert work_state.has_pending_announcement(sid) is True
    assert "just failed" in work_state.prompt_note(sid)
    work_state.clear(sid)


# --- 2. a reminder goes to whoever is listening ----------------------------------------------------

def test_a_reminder_goes_to_the_live_session_not_the_dead_creator():
    from kotoba.core import events

    events.event_queues.clear()
    q = events.register("live-session")
    try:
        assert cron._delivery_sessions("session-that-created-it") == ["live-session"]
    finally:
        events.unregister("live-session", q)


def test_with_nobody_listening_it_falls_back_to_the_recorded_session():
    """Unchanged offline behaviour — this fix is about the case where the user IS sitting there."""
    from kotoba.core import events

    events.event_queues.clear()
    assert cron._delivery_sessions("creator") == ["creator"]
    assert cron._delivery_sessions(None) == []


def test_a_job_that_cannot_be_settled_is_left_due_and_not_announced():
    """Settle before emit: emitting first meant a DB error re-fired the same reminder every 30s."""
    from kotoba.core import events, pending_reminder

    events.event_queues.clear()
    q = events.register("s")

    class _DB:
        async def due_cronjobs(self):
            return [{"id": 1, "message": "take the pills", "session_id": "s", "due_at": "2026-01-01 08:00:00"}]

        async def mark_cronjob_fired(self, job_id):
            raise RuntimeError("database is locked")

        async def reschedule_cronjob(self, job_id, due, was_due=None):  # pragma: no cover - not recurring
            raise AssertionError("should not reschedule a one-time job")

    try:
        asyncio.run(cron._tick(_DB()))
        assert q.empty(), "nothing may be announced for a job that was not settled"
        assert pending_reminder.prompt_note("s") == "", "and nothing may be stashed either"
    finally:
        events.unregister("s", q)
