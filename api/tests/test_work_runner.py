"""work_runner runs the agentic loop in WORK mode to completion off the voice turn, recording status in
work_state and emitting a `work_done` SSE frame at the end (done or failed)."""
from __future__ import annotations

import asyncio

import kotoba.core.work_runner as wr
import kotoba.core.work_state as ws


class _DB:
    async def ensure_session(self, *a, **k): pass
    async def insert_turn(self, *a, **k): pass


def setup_function():
    ws._state.clear(); ws._tasks.clear()


def test_run_marks_done_and_emits_work_done(monkeypatch):
    emitted = []

    async def fake_emit(session_id, kind, **data):
        emitted.append((kind, data))

    async def fake_loop(*a, **k):
        return "Built index.html with the screenshot."

    monkeypatch.setattr(wr, "emit_task", fake_emit)
    monkeypatch.setattr("kotoba.core.loop.agentic_loop", fake_loop)

    ws.start("s1", "build page")
    asyncio.run(wr._run("s1", "build page", _DB(), {}, mcp=None))

    assert ws.get("s1")["status"] == "done"
    assert ws.get("s1")["summary"] == "Built index.html with the screenshot."
    assert any(k == "work_done" for k, _ in emitted)


def test_run_marks_failed_on_exception(monkeypatch):
    async def fake_emit(session_id, kind, **data): pass
    async def boom(*a, **k): raise RuntimeError("sandbox down")
    monkeypatch.setattr(wr, "emit_task", fake_emit)
    monkeypatch.setattr("kotoba.core.loop.agentic_loop", boom)

    ws.start("s2", "g")
    asyncio.run(wr._run("s2", "g", _DB(), {}, mcp=None))
    assert ws.get("s2")["status"] == "failed"
    assert "sandbox down" in ws.get("s2")["reason"]


def test_empty_summary_is_named_not_dressed_as_done(monkeypatch):
    """The loop's last iteration can end on tool calls (cap exhaustion) or an empty message, returning
    "". Substituting "Done." announced a completion nobody witnessed — the untrue-instead-of-"I can't"
    family. The honest ending names what is known: it ended, and left no summary."""
    emitted = []

    async def fake_emit(session_id, kind, **data):
        emitted.append((kind, data))

    async def fake_loop(*a, **k):
        return "   "

    monkeypatch.setattr(wr, "emit_task", fake_emit)
    monkeypatch.setattr("kotoba.core.loop.agentic_loop", fake_loop)

    ws.start("s3", "g")
    asyncio.run(wr._run("s3", "g", _DB(), {}, mcp=None))

    st = ws.get("s3")
    assert st["status"] == "done"
    assert "Done." not in st["summary"] and "summary" in st["summary"]
    done = next(d for k, d in emitted if k == "work_done")
    assert done["summary"] == st["summary"][:500]
