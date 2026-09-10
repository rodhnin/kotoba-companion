"""A stop must not reach past its own job.

`cancel_work` cancels the old runner task and clears the record, but the next tool call in the same
turn may be `start_work` ("stop that, do X instead"). The old task processes its CancelledError ticks
later, so its clear/pop landed on the NEW job — the session read idle mid-run, a third start_work
passed the is-running guard, and the ending announced an empty goal. Reproduced with a 0.2s teardown.

End-state writers are now keyed to the run: they no-op when a different run owns the record, and a task
pop refuses one that is not the caller's own. A canceller with no run id still clears unconditionally —
the user's stop targets the session, not one run."""
from __future__ import annotations

import asyncio

from kotoba.core import work_state


def test_stop_then_restart_keeps_the_new_job(monkeypatch):
    async def main():
        import kotoba.core.loop as _loop
        import kotoba.core.work_runner as wr

        sid = "stop-restart"
        work_state.clear(sid)
        frames: list = []

        async def spy(session_id, kind, **data):
            frames.append((kind, dict(data)))

        monkeypatch.setattr(wr, "emit_task", spy)

        async def old_loop(*a, **kw):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await asyncio.sleep(0.2)  # the real loop's teardown awaits too
                raise
            return "old"

        monkeypatch.setattr(_loop, "agentic_loop", old_loop)
        wr.start(sid, "OLD goal", None, {}, None)
        await asyncio.sleep(0.05)

        # cancel_work's exact sequence, then the same turn launches the next job.
        old = work_state.pop_task(sid)
        assert old is not None
        old.cancel()
        work_state.clear(sid)

        async def new_loop(*a, **kw):
            await asyncio.sleep(0.6)
            return "new done"

        monkeypatch.setattr(_loop, "agentic_loop", new_loop)
        wr.start(sid, "NEW goal", None, {}, None)

        await asyncio.sleep(0.4)  # old teardown is over; the new job is mid-run
        assert work_state.is_running(sid), "the old job's teardown wiped the new job's record"
        assert work_state.get(sid)["goal"] == "NEW goal"
        new_task = work_state.pop_task(sid)
        assert new_task is not None and not new_task.done(), \
            "cancel_work could no longer reach the new job"
        work_state.register_task(sid, new_task)

        await asyncio.gather(old, new_task, return_exceptions=True)
        s = work_state.get(sid)
        assert s["status"] == "done" and s["goal"] == "NEW goal" and s["summary"] == "new done"
        assert work_state.has_pending_announcement(sid)
        work_state.clear(sid)
        work_state.pop_task(sid)

    asyncio.run(main())
