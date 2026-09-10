"""A read-only tool put NOTHING on the wire, and nothing is indistinguishable from a hang.

Only write/exec/network tools earn a `step` frame, and that rule is right — a read is not a row in a
transcript. But it left "what do you know about me" firing five tools in a row while every client sat
on a dead screen. `peek` is the smallest frame that closes it: one kind, one field, no id and no phase,
because there is nothing to correlate and nothing to complete. It is a status line and never a row.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.core.loop as loop
from kotoba.core import events
from kotoba.tools import ToolContext


def _done(item):
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _tool(call_id, name, args=None):
    return _done(types.SimpleNamespace(type="function_call", name=name,
                                       arguments=json.dumps(args or {}), call_id=call_id))


def _spoke():
    return _done(types.SimpleNamespace(type="message", content=[]))


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _Client:
    def __init__(self, turns):
        self.turns, self._i = turns, 0
        self.responses = self

    async def create(self, **kw):
        evs = self.turns[min(self._i, len(self.turns) - 1)]
        self._i += 1
        return _Stream(evs)


class _DB:
    async def insert_audit_log(self, **kw):
        pass


def _frames(turns, sid, monkeypatch, sub=None):
    async def fake_exec(name, args, queue, patterns, ctx, timeout=0):
        return True, "ok"

    monkeypatch.setattr(loop, "execute_with_heartbeat", fake_exec)

    async def go():
        q = events.register(sid)
        ctx = ToolContext(db=_DB(), session_id=sid, client=None, mode="companion", subagent_id=sub)
        ctx.approval = None
        await loop._run_iterations(
            _Client(turns), ctx, [{"role": "user", "content": "x"}], asyncio.Queue(), {},
            max_iterations=len(turns), mode="companion",
            allow_risk={"read", "write", "exec", "network"}, toolset_filter=None,
        )
        out = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister(sid)
        return out

    return asyncio.run(go())


def _peeks(frames):
    return [f["tool"] for f in frames if f.get("kind") == "peek"]


def test_a_read_names_itself_and_then_lets_go(monkeypatch):
    """The second assertion is the invariant, not a detail: a peek is a status line, so the read that
    raised it still draws no step card and still reaches no transcript."""
    frames = _frames([[_tool("c1", "memory_recall", {"query": "me"})], [_spoke()]],
                     "peek-read", monkeypatch)
    assert _peeks(frames) == ["memory_recall", ""]
    assert not [f for f in frames if f.get("kind") == "step"]


def test_five_reads_in_a_row_each_get_named(monkeypatch):
    frames = _frames([
        [_tool("c1", "memory_recall", {}), _tool("c2", "skill_list", {}),
         _tool("c3", "read_file", {"path": "a.md"})],
        [_spoke()],
    ], "peek-many", monkeypatch)
    assert _peeks(frames) == ["memory_recall", "", "skill_list", "", "read_file", ""]


def test_an_action_never_peeks(monkeypatch):
    frames = _frames([[_tool("c1", "delegate", {"goal": "g"})], [_spoke()]],
                     "peek-action", monkeypatch)
    assert _peeks(frames) == []
    assert [f["phase"] for f in frames if f.get("kind") == "step"] == ["start", "done"]


def test_an_action_after_a_read_takes_the_name_off_the_bar_before_it_starts(monkeypatch):
    """Ordering matters: the bar ranks a peek above the working chip, so a name left standing while an
    action runs is the bar reporting a tool she has already left."""
    frames = _frames([
        [_tool("c1", "memory_recall", {}), _tool("c2", "delegate", {"goal": "g"})],
        [_spoke()],
    ], "peek-mixed", monkeypatch)
    kinds = [(f.get("kind"), f.get("tool", f.get("phase", ""))) for f in frames
             if f.get("kind") in ("peek", "step")]
    assert kinds == [("peek", "memory_recall"), ("peek", ""), ("step", "start"), ("step", "done")]


def test_a_helpers_reads_do_not_touch_the_main_bar(monkeypatch):
    frames = _frames([[_tool("c1", "memory_recall", {})], [_spoke()]],
                     "peek-sub", monkeypatch, sub="h1")
    assert _peeks(frames) == []


def test_the_same_name_twice_is_one_frame():
    """_peek is by-value: a caller that re-announces the same tool writes nothing."""
    ctx = types.SimpleNamespace(session_id="peek-dedup")

    async def go():
        q = events.register("peek-dedup")
        await loop._peek(ctx, "peek-dedup", "read_file")
        await loop._peek(ctx, "peek-dedup", "read_file")
        await loop._peek(ctx, "peek-dedup", "")
        await loop._peek(ctx, "peek-dedup", "")
        out = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("peek-dedup")
        return out

    assert _peeks(asyncio.run(go())) == ["read_file", ""]


def test_a_turn_cut_mid_read_does_not_leave_the_bar_naming_it(monkeypatch):
    """The one case the per-tool clear cannot reach. ask_user/ask_secret/clarify are reads that block on
    a human for as long as it takes, so a cancel lands inside one — and a stale peek outranks every
    out-of-turn phrase on the bar, so it would still be claiming she is waiting on you at an idle
    prompt."""
    monkeypatch.setattr(loop, "get_client", lambda: object())

    async def cancelled_mid_read(client, ctx, *a, **k):
        ctx._peek_tool = "ask_user"
        raise asyncio.CancelledError()

    monkeypatch.setattr(loop, "_run_iterations", cancelled_mid_read)

    class _GateDB:
        async def insert_audit_log(self, *a, **k): pass
        async def list_approved_commands(self): return []

    async def go():
        q = events.register("peek-cut")
        with pytest.raises(asyncio.CancelledError):
            await loop.agentic_loop([{"role": "user", "content": "x"}], "peek-cut",
                                    _GateDB(), asyncio.Queue(), {})
        out = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("peek-cut")
        return out

    assert _peeks(asyncio.run(go())) == [""]


def test_a_turn_that_read_nothing_says_nothing(monkeypatch):
    monkeypatch.setattr(loop, "get_client", lambda: object())

    async def quiet(client, ctx, *a, **k):
        return "hello"

    monkeypatch.setattr(loop, "_run_iterations", quiet)

    class _GateDB:
        async def insert_audit_log(self, *a, **k): pass
        async def list_approved_commands(self): return []

    async def go():
        q = events.register("peek-quiet")
        await loop.agentic_loop([{"role": "user", "content": "x"}], "peek-quiet",
                                _GateDB(), asyncio.Queue(), {})
        out = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("peek-quiet")
        return out

    assert _peeks(asyncio.run(go())) == []
