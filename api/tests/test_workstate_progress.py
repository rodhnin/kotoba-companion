"""The background work-loop must write its CURRENT step into work_state so the voice turn reports the
REAL step ("reading example.com", "writing index.html") instead of inventing one ("still researching…").
Only WORK mode updates work_state; a companion turn must not touch it."""
from __future__ import annotations

import asyncio
import types

import kotoba.core.loop as loop
import kotoba.core.work_state as ws


def setup_function():
    ws._state.clear(); ws._tasks.clear()


def test_work_mode_action_updates_work_state_step():
    ws.start("s1", "build a page")
    ctx = types.SimpleNamespace(mode="work", subagent_id=None)
    asyncio.run(loop._emit_action_step(ctx, "s1", "write_file", {"path": "index.html"}))
    step = ws.get("s1")["step"]
    assert step and step != "starting…"          # a real step replaced the placeholder
    assert "index.html" in step or "write" in step.lower()


def test_companion_mode_does_not_touch_work_state():
    ws.start("s2", "running job")
    before = ws.get("s2")["step"]
    ctx = types.SimpleNamespace(mode="companion", subagent_id=None)
    asyncio.run(loop._emit_action_step(ctx, "s2", "web_search", {"query": "x"}))
    assert ws.get("s2")["step"] == before          # companion turn never writes progress
