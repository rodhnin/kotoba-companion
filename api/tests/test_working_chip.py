"""The `working` chip is owned by the WORK lifecycle. work_runner brackets it — on right after
work_started, off in its finally (success or failure) — so the chip spans the whole background task
even when the loop's early iterations are built-in web searches or delegate-only turns (which announce
no parent-level action). A companion turn that ends while work is running must NOT clear it: the
loop's turn-end `working` off is skipped while work_state says the session's work is running; with no
work running, the turn-end off still fires (regression: a mid-work companion turn used to blank the
chip for the rest of a 90s+ research run)."""
from __future__ import annotations

import asyncio

from kotoba.core import events
import kotoba.core.loop as loop
import kotoba.core.work_runner as wr
import kotoba.core.work_state as ws


class _DB:
    async def ensure_session(self, *a, **k): pass
    async def insert_turn(self, *a, **k): pass
    async def insert_audit_log(self, *a, **k): pass
    async def list_approved_commands(self): return []


def setup_function():
    ws._state.clear(); ws._tasks.clear()


def _recorder(monkeypatch):
    emitted: list[tuple[str, dict]] = []

    async def fake_emit(session_id, kind, **data):
        emitted.append((kind, data))

    monkeypatch.setattr(wr, "emit_task", fake_emit)
    return emitted, fake_emit


def _kinds(emitted):
    return [k if k != "working" else f"working:{'on' if d.get('on') else 'off'}" for k, d in emitted]


def test_work_runner_brackets_working_chip(monkeypatch):
    emitted, _ = _recorder(monkeypatch)

    async def fake_loop(*a, **k):
        return "report written"

    monkeypatch.setattr("kotoba.core.loop.agentic_loop", fake_loop)
    ws.start("s1", "research")
    asyncio.run(wr._run("s1", "research", _DB(), {}, mcp=None))

    kinds = _kinds(emitted)
    assert kinds.count("working:on") == 1 and kinds.count("working:off") == 1
    assert kinds.index("work_started") < kinds.index("working:on")
    assert kinds.index("working:on") < kinds.index("work_done") < kinds.index("working:off")


def test_working_chip_cleared_on_failure(monkeypatch):
    emitted, _ = _recorder(monkeypatch)

    async def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr("kotoba.core.loop.agentic_loop", boom)
    ws.start("s2", "g")
    asyncio.run(wr._run("s2", "g", _DB(), {}, mcp=None))

    kinds = _kinds(emitted)
    assert "working:on" in kinds and "working:off" in kinds
    done = [d for k, d in emitted if k == "work_done"]
    assert [(d["ok"], d["summary"]) for d in done] == [(False, "")]


def test_working_chip_spans_subagent_only_run(monkeypatch):
    emitted, fake_emit = _recorder(monkeypatch)

    async def delegate_only_loop(*a, **k):
        await fake_emit("s3", "subagent_spawned", id="h1", goal="dig")
        await fake_emit("s3", "subagent_step", id="h1", text="searching")
        await fake_emit("s3", "subagent_done", id="h1", ok=True, summary="found it")
        return "synthesized"

    monkeypatch.setattr("kotoba.core.loop.agentic_loop", delegate_only_loop)
    ws.start("s3", "g")
    asyncio.run(wr._run("s3", "g", _DB(), {}, mcp=None))

    kinds = _kinds(emitted)
    assert kinds.index("working:on") < kinds.index("subagent_spawned")
    assert kinds.index("subagent_done") < kinds.index("working:off")


def _turn_end_frames(session: str, monkeypatch) -> list[dict]:
    """Run agentic_loop with a faked _run_iterations that latched _working_emitted (i.e. the turn
    announced an action tool) and return the real frames its finally pushed to the session queue."""
    monkeypatch.setattr(loop, "get_client", lambda: object())

    async def fake_iterations(client, ctx, *a, **k):
        ctx._working_emitted = True
        return "ok"

    monkeypatch.setattr(loop, "_run_iterations", fake_iterations)
    q = events.register(session)

    async def go():
        await loop.agentic_loop(
            [{"role": "user", "content": "x"}], session, _DB(), asyncio.Queue(), {},
        )
        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        return frames

    frames = asyncio.run(go())
    events.unregister(session)
    return frames


def test_turn_end_keeps_chip_while_work_runs(monkeypatch):
    ws.start("s4", "background research")
    frames = _turn_end_frames("s4", monkeypatch)
    assert not any(f.get("kind") == "working" and f.get("on") is False for f in frames)


def test_turn_end_clears_chip_when_no_work(monkeypatch):
    frames = _turn_end_frames("s5", monkeypatch)
    assert any(f.get("kind") == "working" and f.get("on") is False for f in frames)
