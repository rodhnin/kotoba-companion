"""A CancelledError nobody asked for is a death, not a stop.

The runner vanished mid-run with no DONE, no failed, no timed-out log -- state silently cleared --
because the `CancelledError` branch logs nothing, assuming every `CancelledError` is the user's stop.
It can also LEAK from inside the loop (a cancel scope dying mid-call), recorded as a clean, silent,
user-requested stop.

`Task.cancelling()` discriminates: a requested stop cancels the task (count >= 1); a leaked error arrives
with count 0 and now becomes a failure -- recorded, bracket-closed, announced.
"""
from __future__ import annotations

import asyncio
import logging

from kotoba.core import work_state


def _patch(monkeypatch, wr, loop_mod, loop_coro, frames):
    async def spy(session_id, kind, **data):
        frames.append((kind, data))

    monkeypatch.setattr(wr, "emit_task", spy)
    monkeypatch.setattr(loop_mod, "agentic_loop", loop_coro)


def test_stray_cancelled_error_is_a_failure_not_a_silent_stop(monkeypatch, caplog):
    async def main():
        import kotoba.core.loop as _loop
        import kotoba.core.work_runner as wr

        sid = "stray-cancel"
        frames: list = []
        work_state.clear(sid)

        async def leaky(*a, **kw):
            raise asyncio.CancelledError()      # the leak: no one cancelled the runner

        _patch(monkeypatch, wr, _loop, leaky, frames)
        work_state.start(sid, "investigar a fondo")
        t = asyncio.create_task(wr._run(sid, "investigar a fondo", None, {}, None))
        await asyncio.gather(t, return_exceptions=True)

        assert t.cancelled() is False, "nobody cancelled the runner; it must not end cancelled"
        assert work_state.get(sid)["status"] == "failed", "a spontaneous death is a failure"
        assert work_state.has_pending_announcement(sid), "the death must be announced, not silent"
        done = [d for k, d in frames if k == "work_done"]
        assert done and done[-1].get("ok") is False, "the bracket closes as not-ok"
        assert not done[-1].get("cancelled"), "and never as a user stop"
        work_state.clear(sid)

    with caplog.at_level(logging.WARNING, logger="kotoba"):
        asyncio.run(main())
    assert any("stray" in r.message.lower() for r in caplog.records), \
        "the death must leave a log line naming the stray CancelledError"


def test_requested_cancel_keeps_its_semantics_and_gains_a_log_line(monkeypatch, caplog):
    async def main():
        import kotoba.core.loop as _loop
        import kotoba.core.work_runner as wr

        sid = "requested-cancel"
        frames: list = []
        work_state.clear(sid)

        async def slow(*a, **kw):
            await asyncio.sleep(30)
            return "never"

        _patch(monkeypatch, wr, _loop, slow, frames)
        work_state.start(sid, "algo")
        t = asyncio.create_task(wr._run(sid, "algo", None, {}, None))
        await asyncio.sleep(0.05)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)

        assert work_state.get(sid)["status"] == "idle", "a stop clears, never fails"
        done = [d for k, d in frames if k == "work_done"]
        assert done and done[-1].get("cancelled") is True
        work_state.clear(sid)

    with caplog.at_level(logging.INFO, logger="kotoba"):
        asyncio.run(main())
    assert any("cancelled" in r.message.lower() and "work_runner" in r.message.lower()
               for r in caplog.records), \
        "a requested stop must leave a trace — the silent branch is how THE REPORT went unexplained"


def test_cancel_work_leaves_a_log_line(caplog):
    async def main():
        import kotoba.tools.builtin.cancel_work as cw

        sid = "cancel-log"
        work_state.clear(sid)
        work_state.start(sid, "algo")

        async def held():
            await asyncio.sleep(30)

        work_state.register_task(sid, asyncio.create_task(held()))
        with caplog.at_level(logging.INFO, logger="kotoba"):
            await cw.execute({}, type("C", (), {"session_id": sid})())
        await asyncio.sleep(0)
        work_state.clear(sid)

    asyncio.run(main())
    assert any("cancel_work" in r.message for r in caplog.records), \
        "/stop calls this directly and used to leave no trace at all in the log"
