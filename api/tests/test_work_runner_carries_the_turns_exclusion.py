from __future__ import annotations

import asyncio

import kotoba.core.loop as loop
from kotoba.core import work_runner, work_state


def _launch(monkeypatch, sid, **start_kwargs):
    seen = {}

    async def fake_loop(input_items, session_id, db, queue, soul, **kw):
        seen.update(kw)
        return "done"

    monkeypatch.setattr(loop, "agentic_loop", fake_loop)

    async def go():
        work_runner.start(sid, "do the thing", None, {}, None, **start_kwargs)
        await work_state.pop_task(sid)

    asyncio.run(go())
    return seen


def test_the_run_is_offered_what_the_launching_turn_was_offered(monkeypatch):
    seen = _launch(monkeypatch, "wr-excl", exclude_tools=frozenset({"ask_user", "open_link"}))
    assert seen["mode"] == "work"
    assert seen["exclude_tools"] == frozenset({"ask_user", "open_link"})


def test_a_turn_with_no_exclusion_launches_an_unrestricted_run(monkeypatch):
    seen = _launch(monkeypatch, "wr-open")
    assert seen.get("exclude_tools") is None
