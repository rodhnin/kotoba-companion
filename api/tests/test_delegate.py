"""Subagent delegation: isolation, depth limit, summary-only returns, lifecycle events.

The tail of this file pins the two halves of the ×-that-wasn't — measured live, two of three helpers
reported "failed" on a run that had done its work: an empty final text was reported as a failure, and
the helper
could never write one — SUB_MAX_ITERATIONS bypassed the main loop's iterations = tool_calls + 1
arithmetic, so the gate that withdraws every tool for the last, tool-less synthesis pass evaluated
against work mode's cap of 40 calls, unreachable in twelve iterations.
"""
from __future__ import annotations

import asyncio
import json
import types

from kotoba.core import events
from kotoba.tools import ToolContext
from kotoba.tools.action import delegate


def test_child_context_isolated_and_deeper():
    parent = ToolContext(db=None, session_id="s", mode="work", spawn_depth=0)
    child = parent.child("sub1")
    assert child.spawn_depth == 1
    assert child.subagent_id == "sub1"
    assert child.session_id == "s"  # same session → progress reaches the user's screen
    # shares workdir/sandbox/approval references
    assert child.db is parent.db


def test_depth_limit_blocks_nested_helpers():
    async def go():
        ctx = ToolContext(db=None, session_id="s", mode="work", spawn_depth=delegate.MAX_SPAWN_DEPTH)
        return await delegate.execute({"goal": "do a thing"}, ctx)

    out = asyncio.run(go())
    assert out is not None and "deeper" in out.lower()


def test_a_helper_itself_cannot_delegate():
    """The constant's comment has always said a helper (depth 1) may not spawn its own helpers, and the
    guard let it: `spawn_depth >= 2` refused only a grandchild, a depth no context can reach because
    the helper's toolset filter never offers `delegate`. The guard now says what the comment says."""
    async def go():
        helper = ToolContext(db=None, session_id="s", mode="work", spawn_depth=0).child("h1")
        return await delegate.execute({"goal": "spawn one more"}, helper)

    out = asyncio.run(go())
    assert out is not None and "deeper" in out.lower()


def test_empty_goal_returns_none():
    async def go():
        ctx = ToolContext(db=None, session_id="s", mode="work", spawn_depth=0)
        return await delegate.execute({"goal": "   "}, ctx)

    assert asyncio.run(go()) is None


def test_delegate_emits_lifecycle_and_returns_only_summary(monkeypatch):
    """delegate should emit subagent_spawned → subagent_done and return ONLY the helper's summary
    (the parent never sees the helper's intermediate steps in the return value)."""

    async def fake_run_iterations(client, ctx, input_items, queue, soul_patterns, **kw):
        # Simulate the child loop doing internal work + producing a summary.
        assert ctx.subagent_id is not None  # ran as a subagent
        await events.emit_task(ctx.session_id, "subagent_step", id=ctx.subagent_id, text="internal step")
        return "Found three palettes and picked a warm one."

    import kotoba.core.loop; from kotoba import core

    monkeypatch.setattr(core.loop, "_run_iterations", fake_run_iterations)
    monkeypatch.setattr("kotoba.core.llm.get_client", lambda: object())

    async def go():
        q = events.register("sess-d")
        ctx = ToolContext(db=None, session_id="sess-d", mode="work", spawn_depth=0)
        summary = await delegate.execute({"goal": "Research palettes", "toolset": "research"}, ctx)
        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        events.unregister("sess-d")
        return summary, frames

    summary, frames = asyncio.run(go())
    assert summary == "Found three palettes and picked a warm one."
    kinds = [f.get("kind") for f in frames if f.get("type") == "task"]
    assert "subagent_spawned" in kinds and "subagent_done" in kinds
    done = next(f for f in frames if f.get("kind") == "subagent_done")
    assert "warm one" in done["summary"]
    assert done["ok"] is True


def test_delegate_surfaces_real_error(monkeypatch):
    """When the child raises, subagent_done carries the REAL error (not a generic placeholder)."""

    async def boom(client, ctx, input_items, queue, soul_patterns, **kw):
        raise ValueError("network unreachable: api.example.com")

    import kotoba.core.loop; from kotoba import core

    monkeypatch.setattr(core.loop, "_run_iterations", boom)
    monkeypatch.setattr("kotoba.core.llm.get_client", lambda: object())

    async def go():
        q = events.register("sess-e")
        ctx = ToolContext(db=None, session_id="sess-e", mode="work", spawn_depth=0)
        ret = await delegate.execute({"goal": "fetch the API"}, ctx)
        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        events.unregister("sess-e")
        return ret, frames

    ret, frames = asyncio.run(go())
    done = next(f for f in frames if f.get("kind") == "subagent_done")
    assert done["ok"] is False
    assert "network unreachable" in done["summary"]  # the real exception text, not a placeholder
    assert "ValueError" in done["summary"]
    assert "network unreachable" in (ret or "")  # parent sees it too


def test_cancelled_helper_stamps_done_and_still_raises(monkeypatch):
    """CancelledError is BaseException, so `except Exception` skipped the closing subagent_done and the
    helper's chibi spun forever. It must now (a) emit subagent_done ok=False and (b) STILL propagate —
    swallowing the cancel would break the loop's finally / turns.supersede contract."""
    started = asyncio.Event()

    async def hang(client, ctx, input_items, queue, soul_patterns, **kw):
        started.set()
        await asyncio.sleep(3600)

    import kotoba.core.loop; from kotoba import core

    monkeypatch.setattr(core.loop, "_run_iterations", hang)
    monkeypatch.setattr("kotoba.core.llm.get_client", lambda: object())

    async def go():
        q = events.register("sess-x")
        ctx = ToolContext(db=None, session_id="sess-x", mode="work", spawn_depth=0)
        task = asyncio.create_task(delegate.execute({"goal": "a long job"}, ctx))
        await started.wait()
        task.cancel()
        raised = False
        try:
            await task
        except asyncio.CancelledError:
            raised = True
        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        events.unregister("sess-x")
        return raised, frames

    raised, frames = asyncio.run(go())
    assert raised is True                     # the CancelledError still reached the caller
    kinds = [f.get("kind") for f in frames if f.get("type") == "task"]
    assert "subagent_spawned" in kinds and "subagent_done" in kinds
    done = next(f for f in frames if f.get("kind") == "subagent_done")
    assert done["ok"] is False and "stopped" in done["summary"].lower()


def test_an_empty_write_up_is_not_a_failure(monkeypatch):
    """A helper that did its work and returned no closing prose must come back ok=True with a truthful
    line, not a × and "(helper failed: …)" — the parent reacts to that by redoing the whole subtask."""

    async def silent_helper(client, ctx, input_items, queue, soul_patterns, **kw):
        return ""

    import kotoba.core.loop
    from kotoba import core

    monkeypatch.setattr(core.loop, "_run_iterations", silent_helper)
    monkeypatch.setattr("kotoba.core.llm.get_client", lambda: object())

    async def go():
        q = events.register("sess-empty")
        ctx = ToolContext(db=None, session_id="sess-empty", mode="work", spawn_depth=0)
        ret = await delegate.execute({"goal": "collect the sources"}, ctx)
        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        events.unregister("sess-empty")
        return ret, frames

    ret, frames = asyncio.run(go())
    done = next(f for f in frames if f.get("kind") == "subagent_done")
    assert done["ok"] is True
    assert "failed" not in done["summary"].lower()
    assert ret and "helper failed" not in ret


def test_the_helper_loop_always_reaches_a_tool_less_synthesis_pass(monkeypatch):
    """The arithmetic of `_default_max_iterations`, applied to the helper's own budget:
    delegate hands _run_iterations a tool-call cap one below its iteration cap, so a model calling one
    tool per iteration hits the gate that withdraws every tool BEFORE iterations run out, and the last
    pass is forced to answer in words instead of stopping mid-chain with nothing said."""
    seen = {}

    async def capture(client, ctx, input_items, queue, soul_patterns, **kw):
        seen.update(kw)
        return "done"

    import kotoba.core.loop
    from kotoba import core

    monkeypatch.setattr(core.loop, "_run_iterations", capture)
    monkeypatch.setattr("kotoba.core.llm.get_client", lambda: object())

    async def go():
        ctx = ToolContext(db=None, session_id="sess-arith", mode="work", spawn_depth=0)
        await delegate.execute({"goal": "measure it"}, ctx)

    asyncio.run(go())
    assert seen.get("max_tool_calls") is not None, "delegate must size the tool budget itself"
    assert seen["max_tool_calls"] < seen["max_iterations"], (
        "iterations = tool calls + 1: room for the synthesis pass, or the helper stops mid-chain"
    )


def _done_item(item):
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _call_item(call_id, name, args):
    return _done_item(types.SimpleNamespace(type="function_call", name=name,
                                            arguments=json.dumps(args), call_id=call_id))


def _text_ev(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


class _ToolHungryClient:
    """Calls one tool per iteration for as long as it is offered one; answers in words the moment the
    tool list arrives empty. Records the offered list per call."""

    def __init__(self):
        self.offered: list[list] = []
        self._n = 0
        self.responses = self

    async def create(self, *, tools=None, **kw):
        self.offered.append(list(tools or []))
        self._n += 1
        if tools:
            return _Str([_call_item(f"c{self._n}", "shell", {"command": f"step {self._n}"})])
        return _Str([_text_ev("synthesized from what I have")])


class _Str:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


def test_a_capped_run_ends_in_words_not_mid_chain(monkeypatch):
    """Drive the real _run_iterations the way delegate now sizes it (max_tool_calls one under
    max_iterations): the final iteration must be offered NO tools and its words are the summary."""
    import kotoba.core.loop as loop
    import kotoba.tools.registry as reg

    monkeypatch.setattr(loop, "model_name", lambda *a, **k: "test-model")
    monkeypatch.setattr(loop, "model_call_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(loop, "is_reasoning_model", lambda *a, **k: False)
    monkeypatch.setattr("kotoba.tools.registry._check_cache", {})
    reg.discover()

    async def fake_exec(name, args, queue, patterns, ctx, timeout=0):
        return True, "ran"

    monkeypatch.setattr(loop, "execute_with_heartbeat", fake_exec)
    client = _ToolHungryClient()

    async def go():
        ctx = ToolContext(db=None, session_id="sess-cap", mode="work", subagent_id="h9")
        return await loop._run_iterations(
            client, ctx, [{"role": "user", "content": "x"}], asyncio.Queue(), {},
            max_iterations=3, mode="work",
            allow_risk={"read", "write", "exec", "network"}, toolset_filter=None,
            max_tool_calls=2,
        )

    summary = asyncio.run(go())
    assert summary == "synthesized from what I have"
    assert client.offered[-1] == [], "the last pass must have had every tool withdrawn"
    assert len(client.offered) == 3
