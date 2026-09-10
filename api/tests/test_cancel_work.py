"""cancel_work stops the running background work (cancels the task, clears state)."""
from __future__ import annotations

import asyncio

import kotoba.core.work_state as ws
from kotoba.tools import ToolContext
import kotoba.tools.builtin.cancel_work as cw


def setup_function():
    ws._state.clear(); ws._tasks.clear()


def test_cancel_work_cancels_running_task():
    async def go():
        async def forever():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                raise
        ws.start("s1", "g")
        ws.register_task("s1", asyncio.create_task(forever()))
        ctx = ToolContext(db=None, session_id="s1", mode="companion")
        out = await cw.execute({}, ctx)
        await asyncio.sleep(0)  # let the cancellation propagate
        return out
    out = asyncio.run(go())
    assert "stopped" in out.lower() or "dej" in out.lower() or "cancel" in out.lower()
    assert ws.is_running("s1") is False


def test_cancel_work_when_nothing_running():
    ctx = ToolContext(db=None, session_id="s2", mode="companion")
    out = asyncio.run(cw.execute({}, ctx))
    # "no " alone would also match the success message — require the actual no-op wording.
    assert "nothing running" in out.lower()
    assert "stopped" not in out.lower(), "must not claim it stopped anything"
