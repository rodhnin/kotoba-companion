"""A background work item must not run forever. If the agentic loop stalls (e.g. a research loop that
never converges), the work-runner times out, marks the work failed, and emits work_done so the voice can
tell the user instead of leaving them waiting through endless 'still researching' until the WS dies.

The stub below takes *a: `_work_timeout` now receives the transport captured at job creation, and a
zero-argument stub raised a TypeError that `_run`'s broad except turned into a `failed` with a reason
that happened to contain this test's own name — so it passed while measuring nothing. The assertion
names the timeout explicitly for the same reason."""
from __future__ import annotations

import asyncio

import kotoba.core.work_runner as wr
import kotoba.core.work_state as ws


class _DB:
    async def insert_turn(self, *a, **k): pass


def setup_function():
    ws._state.clear(); ws._tasks.clear()


def test_run_times_out_and_marks_failed(monkeypatch):
    emitted = []

    async def fake_emit(session_id, kind, **data):
        emitted.append((kind, data))

    async def hangs(*a, **k):
        await asyncio.sleep(5)  # never finishes within the (patched) budget
        return "unreached"

    monkeypatch.setattr(wr, "emit_task", fake_emit)
    monkeypatch.setattr("kotoba.core.loop.agentic_loop", hangs)
    monkeypatch.setattr(wr, "_work_timeout", lambda *a: 0.05)

    ws.start("s1", "find the hardest integral and solve it")
    asyncio.run(wr._run("s1", "find the hardest integral and solve it", _DB(), {}, mcp=None))

    snap = ws.get("s1")
    assert snap["status"] == "failed"
    assert snap["reason"] == "it took too long and I stopped it"
    assert any(k == "work_done" and d.get("ok") is False for k, d in emitted)
