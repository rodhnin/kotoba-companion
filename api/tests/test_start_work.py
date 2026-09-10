"""start_work launches background work and returns immediately; refuses a second concurrent job."""
from __future__ import annotations

import asyncio

import kotoba.core.work_state as ws
from kotoba.tools import ToolContext
import kotoba.tools.builtin.start_work as sw


class _DB:
    async def insert_turn(self, *a, **k): pass


def setup_function():
    ws._state.clear(); ws._tasks.clear()


def test_start_work_launches_and_returns(monkeypatch):
    launched = {}

    def fake_start(session_id, goal, db, soul_patterns, mcp, request="", **kw):
        launched["goal"] = goal
        launched["request"] = request
        ws.start(session_id, goal)  # mimic work_runner.start marking running

    monkeypatch.setattr(sw.work_runner, "start", fake_start)
    ctx = ToolContext(db=_DB(), session_id="s1", mode="companion")
    out = asyncio.run(sw.execute({"goal": "build a page"}, ctx))
    assert launched["goal"] == "build a page"
    assert "pantalla" in out.lower() or "screen" in out.lower() or "work" in out.lower()


def test_start_work_refuses_when_already_running(monkeypatch):
    monkeypatch.setattr(sw.work_runner, "start", lambda *a, **k: None)
    ws.start("s1", "existing job")
    ctx = ToolContext(db=_DB(), session_id="s1", mode="companion")
    out = asyncio.run(sw.execute({"goal": "another"}, ctx))
    assert "existing job" in out  # tells the model it's already busy with the current goal
