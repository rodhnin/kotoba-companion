"""Parallel delegates must be ANNOUNCED before they run — the terminal card ('step' start) and the
`working` chip go out BEFORE the asyncio.gather, so the user sees the delegation while the helpers
grind (a 3-way research batch is minutes long), not after. Regression for the empty-Terminal bug:
the per-tool loop used to emit the card only when consuming the already-finished cached result.
"""
from __future__ import annotations

import asyncio
import json
import types

from kotoba.core import events
import kotoba.core.loop as loop
from kotoba.tools import ToolContext


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_delegate(call_id, goal):
    item = types.SimpleNamespace(
        type="function_call", name="delegate",
        arguments=json.dumps({"goal": goal, "toolset": "research"}), call_id=call_id,
    )
    return types.SimpleNamespace(type="response.output_item.done", item=item)


class _FakeStream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _FakeClient:
    def __init__(self, scripts):
        self._scripts = scripts
        self._i = 0
        self.responses = self

    async def create(self, **kw):
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _FakeStream(evs)


class _FakeDB:
    async def insert_audit_log(self, **kw):
        pass


def _run(scripts, session, monkeypatch, exec_fn=None):
    """Drive _run_iterations with scripted delegate calls; execution order is interleaved into the
    SAME event queue via a 'test_exec' frame emitted by the faked executor, so frame order IS the
    announce-vs-execute order. Returns (frames, exec_calls, input_items)."""
    exec_calls = []

    async def fake_exec(tool_name, args, queue, patterns, ctx, timeout=0):
        if exec_fn:
            await exec_fn(args)
        await events.emit_task(session, "test_exec", goal=str((args or {}).get("goal", "")))
        exec_calls.append((tool_name, (args or {}).get("goal")))
        return True, f"summary of {(args or {}).get('goal')}"

    monkeypatch.setattr(loop, "execute_with_heartbeat", fake_exec)

    q = events.register(session)
    ctx = ToolContext(db=_FakeDB(), session_id=session, client=None, mode="work")
    ctx.approval = None
    input_items = [{"role": "user", "content": "x"}]

    async def go():
        await loop._run_iterations(
            _FakeClient(scripts), ctx, input_items, asyncio.Queue(), {},
            max_iterations=5, mode="work", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )
        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        return frames

    frames = asyncio.run(go())
    events.unregister(session)
    return frames, exec_calls, input_items


def _idx(frames, pred):
    return [i for i, f in enumerate(frames) if pred(f)]


def test_parallel_delegates_announced_before_execution(monkeypatch):
    scripts = [
        [_ev_delegate("c_react", "research React"), _ev_delegate("c_vue", "research Vue")],
        [_ev_text("done")],
    ]
    frames, exec_calls, _ = _run(scripts, "u_par_announce", monkeypatch)

    starts = _idx(frames, lambda f: f.get("kind") == "step" and f.get("phase") == "start")
    execs = _idx(frames, lambda f: f.get("kind") == "test_exec")
    dones = _idx(frames, lambda f: f.get("kind") == "step" and f.get("phase") == "done")

    assert len(execs) == 2 and len(exec_calls) == 2  # ran once each — cached result reused, no re-run
    assert len(starts) == 2 and max(starts) < min(execs)  # BOTH cards out before ANY delegate ran
    assert sorted(frames[i]["id"] for i in starts) == ["c_react", "c_vue"]  # exactly once each
    assert sorted(frames[i]["id"] for i in dones) == ["c_react", "c_vue"]
    for i in dones:
        goal = "research React" if frames[i]["id"] == "c_react" else "research Vue"
        assert i > next(j for j in execs if frames[j]["goal"] == goal)  # each card closed after ITS run

    working_ons = [f for f in frames if f.get("kind") == "working" and f.get("on") is True]
    assert len(working_ons) == 1
    assert _idx(frames, lambda f: f.get("kind") == "working" and f.get("on") is True)[0] < min(execs)


def test_single_delegate_path_unchanged(monkeypatch):
    scripts = [[_ev_delegate("c_solo", "research Svelte")], [_ev_text("done")]]
    frames, exec_calls, _ = _run(scripts, "u_solo_announce", monkeypatch)

    starts = _idx(frames, lambda f: f.get("kind") == "step" and f.get("phase") == "start")
    execs = _idx(frames, lambda f: f.get("kind") == "test_exec")
    assert len(starts) == 1 and len(execs) == 1 and starts[0] < execs[0]
    assert len(exec_calls) == 1
    dones = _idx(frames, lambda f: f.get("kind") == "step" and f.get("phase") == "done")
    assert len(dones) == 1 and frames[dones[0]]["id"] == "c_solo" and frames[dones[0]]["ok"] is True


def test_parallel_delegate_card_closes_when_its_helper_finishes(monkeypatch):
    """A fast helper's card must get its 'done' frame as soon as THAT helper returns — not held hostage
    by a slow sibling still inside the gather (live case: Vue finished in 90s, its card spun for 7 more
    minutes waiting out two 420s sibling timeouts)."""
    gate = asyncio.Event()

    async def stagger(args):
        if (args or {}).get("goal") == "slow":
            await gate.wait()
            await asyncio.sleep(0.01)
        else:
            gate.set()

    scripts = [
        [_ev_delegate("c_slow", "slow"), _ev_delegate("c_fast", "fast")],
        [_ev_text("done")],
    ]
    frames, _, _ = _run(scripts, "u_early_close", monkeypatch, exec_fn=stagger)

    def one(pred):
        idx = _idx(frames, pred)
        assert len(idx) == 1, idx
        return idx[0]

    fast_done = one(lambda f: f.get("kind") == "step" and f.get("phase") == "done" and f.get("id") == "c_fast")
    slow_exec = one(lambda f: f.get("kind") == "test_exec" and f.get("goal") == "slow")
    slow_done = one(lambda f: f.get("kind") == "step" and f.get("phase") == "done" and f.get("id") == "c_slow")
    assert fast_done < slow_exec < slow_done  # fast card closed while the slow helper still ran; no dups


def test_spawned_delegate_past_cap_still_consumed_and_card_closed(monkeypatch):
    """A same-batch duplicate bumps per_tool past _DELEGATE_LIMIT before a SPAWNED delegate is consumed;
    its executed result must still be fed back and its announced card closed (no dangling spinner)."""
    scripts = [
        [_ev_delegate("c_x", "warmup topic")],
        [_ev_delegate("c_a1", "topic A"), _ev_delegate("c_a2", "topic A"), _ev_delegate("c_b", "topic B")],
        [_ev_text("done")],
    ]
    frames, exec_calls, input_items = _run(scripts, "u_cap_edge", monkeypatch)

    assert [g for _, g in exec_calls] == ["warmup topic", "topic A", "topic B"]  # dup never spawned
    starts = _idx(frames, lambda f: f.get("kind") == "step" and f.get("phase") == "start")
    dones = _idx(frames, lambda f: f.get("kind") == "step" and f.get("phase") == "done")
    assert sorted(frames[i]["id"] for i in starts) == ["c_a1", "c_b", "c_x"]
    assert sorted(frames[i]["id"] for i in dones) == ["c_a1", "c_b", "c_x"]  # every card closed

    outs = {i["call_id"]: i["output"] for i in input_items
            if isinstance(i, dict) and i.get("type") == "function_call_output"}
    assert "summary of topic B" in outs["c_b"]  # real result, not the "delegated enough" scold
    assert "ALREADY called" in outs["c_a2"]
