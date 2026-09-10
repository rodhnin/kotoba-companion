"""Turn arbitration (barge-in) + clean cancellation.

Covers the timing/concurrency fix: one active turn per session, the previous turn cancelled and fully
torn down BEFORE the next starts (so SSE frames don't interleave and there are no orphaned tool runs).
"""
from __future__ import annotations

import asyncio
import types

import pytest

from kotoba.core import events, turns
import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def test_supersede_cancels_and_awaits_inflight_turn():
    """supersede() cancels the active turn and waits for it to actually finish (await), so the caller
    can safely start the next turn knowing the old one's teardown has run."""
    async def go():
        sid = "u_super"
        ran_finally = asyncio.Event()

        async def turn():
            try:
                await asyncio.sleep(10)
            finally:
                ran_finally.set()

        t = asyncio.create_task(turn())
        turns.register(sid, t)
        await asyncio.sleep(0.02)            # let it start
        await turns.supersede(sid)           # barge-in
        assert t.cancelled() or t.done()
        assert ran_finally.is_set()          # teardown completed before supersede returned

    asyncio.run(asyncio.wait_for(go(), timeout=5))


def test_barge_in_orders_working_off_before_new_working_on():
    """The old turn's `working off` (emitted in its finally on cancellation) lands BEFORE the new turn's
    `working on` — no interleaving, so the chip/face don't flicker."""
    async def go():
        sid = "u_order"
        q = events.register(sid)
        try:
            async def turn():
                await events.emit_task(sid, "working", on=True)
                try:
                    await asyncio.sleep(10)
                finally:
                    await events.emit_task(sid, "working", on=False)

            t1 = asyncio.create_task(turn())
            turns.register(sid, t1)
            await asyncio.sleep(0.02)         # let turn 1 emit `working on`

            await turns.supersede(sid)        # cancel turn 1 → its finally emits `working off`
            await events.emit_task(sid, "working", on=True)  # turn 2 lights up

            frames = []
            while not q.empty():
                frames.append(q.get_nowait())
        finally:
            events.unregister(sid)

        ons_offs = [f["on"] for f in frames if f.get("kind") == "working"]
        assert ons_offs == [True, False, True]  # on(old) → off(old) → on(new), clean

    asyncio.run(asyncio.wait_for(go(), timeout=5))


def test_execute_with_heartbeat_cancels_inner_tool_on_barge_in(clean_registry):
    """If the loop is cancelled mid-tool (barge-in), execute_with_heartbeat must cancel the actual tool
    task — otherwise a live run (e.g. an E2B sandbox) is orphaned (the shield would protect it)."""
    started = asyncio.Event()
    inner_cancelled = asyncio.Event()

    async def execute(args, ctx):
        started.set()
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            inner_cancelled.set()
            raise

    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": "slow"}, __name__="kotoba.tools.x.slow",
        ANNOUNCE="", HEARTBEAT=[], COMPLETE="done", FAIL="oops", execute=execute,
    )
    reg.register(ToolSpec(name="slow", module=mod, schema=mod.SCHEMA, toolset="file", risk="exec"))

    async def go():
        ctx = ToolContext(db=None, session_id="u_hb", client=None, mode="work")
        outer = asyncio.create_task(
            loop.execute_with_heartbeat("slow", {}, asyncio.Queue(), {}, ctx)
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        outer.cancel()
        try:
            await outer
        except asyncio.CancelledError:
            pass
        await asyncio.wait_for(inner_cancelled.wait(), timeout=2)  # no orphan

    asyncio.run(asyncio.wait_for(go(), timeout=8))


def test_supersede_reraises_the_callers_own_cancellation():
    """A caller cancelled while parked on supersede's `await task` must NOT run on: the broad
    BaseException swallow is for the AWAITED task's CancelledError only. Swallowed, the server went
    on to build a turn for a request whose client was already gone."""
    async def go():
        sid = "u_own_cancel"
        gate = asyncio.Event()

        async def slow_teardown():
            try:
                await asyncio.sleep(60)
            finally:
                await gate.wait()          # teardown takes long enough to be cancelled inside

        old = asyncio.create_task(slow_teardown())
        turns.register(sid, old)
        await asyncio.sleep(0.01)

        async def superseder():
            await turns.supersede(sid)
            return "ran on"

        caller = asyncio.create_task(superseder())
        await asyncio.sleep(0.05)          # caller is parked on `await task`
        caller.cancel()
        await asyncio.sleep(0.01)
        gate.set()                         # let the old turn's teardown finish either way
        with pytest.raises(asyncio.CancelledError):
            await caller
        turns._active.pop(sid, None)

    asyncio.run(asyncio.wait_for(go(), timeout=5))


def test_clear_is_identity_checked():
    """A superseded turn's late teardown must not evict the turn that replaced it."""
    async def go():
        sid = "u_identity"

        async def t():
            await asyncio.sleep(10)

        old, new = asyncio.create_task(t()), asyncio.create_task(t())
        turns.register(sid, old)
        turns.register(sid, new)           # barge-in installed the new turn
        turns.clear(sid, old)              # the old turn's finally runs late
        assert turns._active.get(sid) is new
        for task in (old, new):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        turns.clear(sid, new)
        assert turns._active.get(sid) is None

    asyncio.run(asyncio.wait_for(go(), timeout=5))
