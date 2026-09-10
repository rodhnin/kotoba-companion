"""The WIRE half of stop-then-restart: what a client actually receives, in the order it receives it.
`cancel_work` cancels the old runner and launches the next job in the same turn, but the old task's
`CancelledError` lands ticks later, so the successor's `work_started` reaches the queue BEFORE the
old run's `work_done cancelled=True`. Every frame still names its own `run_id` -- clients latch the
newest `work_started`'s run_id and ignore bracket frames naming another run. This ordering is
structural, not a narrow race: even an instant cancel still lands after the successor's start,
because the cancelled path always crosses more awaits than the new runner's first emit. If runner
internals ever reverse this order, the client rule stays correct regardless.
"""
from __future__ import annotations

import asyncio

from kotoba.core import events, work_state


def test_the_old_runs_close_lands_after_the_new_runs_start_and_names_its_own_run(monkeypatch):
    async def main():
        import kotoba.core.loop as _loop
        import kotoba.core.work_runner as wr

        sid = "wire-stop-restart"
        work_state.clear(sid)
        q = events.register(sid)
        try:
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
                await asyncio.sleep(0.4)
                return "new done"

            monkeypatch.setattr(_loop, "agentic_loop", new_loop)
            wr.start(sid, "NEW goal", None, {}, None)
            new = work_state.pop_task(sid)
            assert new is not None
            work_state.register_task(sid, new)
            await asyncio.gather(old, new, return_exceptions=True)

            frames = []
            while not q.empty():
                frames.append(q.get_nowait())
            tasks = [f for f in frames if f.get("type") == "task"]

            starts = [f for f in tasks if f["kind"] == "work_started"]
            assert [f["goal"] for f in starts] == ["OLD goal", "NEW goal"]
            rid_old, rid_new = starts[0].get("run_id", ""), starts[1].get("run_id", "")
            assert rid_old and rid_new and rid_old != rid_new, \
                "the runner must stamp every bracket with its own run"

            i_new_start = tasks.index(starts[1])
            late = tasks[i_new_start + 1:]
            old_close = [f for f in late if f["kind"] == "work_done" and f.get("cancelled")]
            assert old_close, "reproduced order lost: the cancelled close must land AFTER the new start"
            assert all(f.get("run_id") == rid_old for f in old_close)
            old_off = [f for f in late if f["kind"] == "working" and f.get("on") is False
                       and f.get("run_id") == rid_old]
            assert old_off, "the old runner's finally still emits its working-off, late, named"

            for f in tasks:
                if f["kind"] in ("work_started", "working", "work_done"):
                    assert f.get("run_id"), f"an unstamped bracket frame reached the wire: {f}"

            new_close = [f for f in late if f["kind"] == "work_done" and f.get("run_id") == rid_new]
            assert new_close and new_close[0]["ok"] and new_close[0]["summary"] == "new done"
        finally:
            events.unregister(sid, q)
            work_state.clear(sid)
            work_state.pop_task(sid)

    asyncio.run(main())
