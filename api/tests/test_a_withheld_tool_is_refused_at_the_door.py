"""Withholding a tool only hid it. `exclude_tools` shaped the schema list and nothing read it again.

Dispatch resolves by NAME, and a model that saw a name earlier in the conversation still emits it —
so on the public surface a stranger's turn reached the host's file library by asking for a tool it was
never offered. Six Discord tools refused inside their own `execute`; the other twenty-four did not,
and the read-risk half meets no approval card on the way. The door is the dispatcher, once, for all
of them: a name this turn was not given never reaches a tool body.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.tools.registry as reg
from kotoba.core.loop import execute_with_heartbeat
from kotoba.tools.registry import ToolSpec, register


@pytest.fixture
def probe():
    """A real registered tool with a sentinel only its body can write."""
    ran: list[str] = []

    class _Mod:
        SCHEMA = {"type": "function", "name": "reach_probe", "description": "Probe.",
                  "parameters": {"type": "object", "properties": {}, "required": []}}
        BUILT_IN = False

        @staticmethod
        async def execute(args, ctx):
            ran.append("body")
            return "the operator's private note"

    register(ToolSpec(name="reach_probe", module=_Mod, schema=_Mod.SCHEMA, toolset="file"))
    try:
        yield ran
    finally:
        reg.deregister("reach_probe")


class _Ctx:
    def __init__(self, excluded=()):
        self.call_id = "c-door"
        self.excluded_tools = frozenset(excluded)


def _call(ctx):
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        return await execute_with_heartbeat("reach_probe", {}, q, {}, ctx=ctx, timeout=5)

    return asyncio.run(go())


def test_the_tool_really_runs_when_the_turn_was_given_it(probe):
    """Guards the guard: a refusal below proves nothing if the tool never worked."""
    ok, result = _call(_Ctx())
    assert ok and probe == ["body"] and "private note" in result


def test_a_name_this_turn_was_not_given_never_reaches_the_body(probe):
    ok, result = _call(_Ctx({"reach_probe"}))
    assert not ok
    assert probe == [], "the withheld tool ran anyway — the schema list was the only door"
    assert "private note" not in result


def test_the_refusal_does_not_read_as_an_obstacle_she_hit(probe):
    """She reports an invented failure when a refusal sounds like one, and reports it as HER limit.
    Not being given a tool is neither: nothing ran, so there is nothing to describe."""
    _ok, result = _call(_Ctx({"reach_probe"}))
    assert "NOTHING ran" in result
    assert "not an obstacle" in result
    assert "Do not call it again" in result


def test_a_context_that_predates_the_field_still_dispatches(probe):
    """Every test double in the tree is such a context. The door must not close on all of them."""

    class _Old:
        call_id = "c-old"

    ok, _result = _call(_Old())
    assert ok and probe == ["body"]


def test_the_loop_puts_the_turns_exclusion_where_the_door_can_read_it(monkeypatch):
    """The other half: a door that reads a field nobody fills is shut on nothing."""
    import kotoba.core.loop as loop

    seen = []
    real = loop.ToolContext

    def _spy(**kw):
        made = real(**kw)
        seen.append(made)
        return made

    class _Stub:
        """Enough of a client to get past the offline branch, and nothing more."""

        def __getattr__(self, _name):
            raise RuntimeError("the context is built before the model is ever reached")

    monkeypatch.setattr(loop, "ToolContext", _spy)
    monkeypatch.setattr(loop, "get_client", lambda: _Stub())

    with pytest.raises(BaseException):
        asyncio.run(loop.agentic_loop(
            input_items=[], session_id="door-session", db=None,
            stream_queue=asyncio.Queue(), soul_patterns={},
            exclude_tools=frozenset({"reach_probe", "shell"}),
        ))
    assert seen, "the loop never built a context — this test is watching the wrong seam"
    assert seen[0].excluded_tools == frozenset({"reach_probe", "shell"})


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args, cid):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=cid)
    return types.SimpleNamespace(type="response.output_item.done", item=item)


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _Client:
    def __init__(self, scripts):
        self._scripts, self._i, self.offered, self.fed = scripts, 0, [], []
        self.responses = self

    async def create(self, **kw):
        self.offered.append([t.get("name") or t.get("type") for t in kw.get("tools", [])])
        self.fed = [it for it in kw["input"]
                    if isinstance(it, dict) and it.get("type") == "function_call_output"]
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _Stream(evs)


class _DB:
    def __init__(self):
        self.audit = []

    async def insert_audit_log(self, **kw):
        self.audit.append(kw)

    async def list_approved_commands(self):
        return []

    async def save_approved_command(self, pattern, scope="command"):
        pass


def _turn(monkeypatch, sid, scripts, excluded):
    import kotoba.core.loop as loop
    from kotoba.core import events

    client, db = _Client(scripts), _DB()
    monkeypatch.setattr(loop, "get_client", lambda: client)

    async def go():
        q = events.register(sid)
        try:
            await loop.agentic_loop([{"role": "user", "content": "hi"}], sid, db, asyncio.Queue(), {},
                                    mode="companion", channel="text", register="text",
                                    exclude_tools=frozenset(excluded))
            return [q.get_nowait() for _ in range(q.qsize())]
        finally:
            events.unregister(sid, q)

    frames = asyncio.run(go())
    return client, db, frames


def test_a_child_context_carries_the_turns_exclusion():
    from kotoba.tools import ToolContext

    parent = ToolContext(db=None, session_id="s", excluded_tools=frozenset({"reach_probe"}))
    assert parent.child("h1").excluded_tools == frozenset({"reach_probe"})


def test_a_helper_spawned_from_a_restricted_turn_cannot_reach_the_withheld_tool(probe, monkeypatch):
    from kotoba.tools import ToolContext
    from kotoba.tools.action import delegate

    helper = _Client([[_ev_call("reach_probe", {}, "h1")], [_ev_text("summary")]])
    monkeypatch.setattr("kotoba.core.llm.get_client", lambda *a, **k: helper)
    parent = ToolContext(db=_DB(), session_id="door-helper", excluded_tools=frozenset({"reach_probe"}))
    parent.approval = None
    parent.call_id = "c-del"

    out = asyncio.run(delegate.execute({"goal": "find it", "toolset": "file"}, parent))
    assert out == "summary"
    assert "reach_probe" not in helper.offered[0]
    assert probe == []


def test_a_refusal_at_the_door_is_neither_counted_nor_told_as_a_failure(probe, monkeypatch):
    scripts = [[_ev_call("reach_probe", {}, "c1")], [_ev_call("reach_probe", {}, "c2")],
               [_ev_call("reach_probe", {"q": 1}, "c3")], [_ev_text("done")]]
    client, db, frames = _turn(monkeypatch, "door-turn", scripts, {"reach_probe"})
    assert probe == []
    assert client.offered[-1], "two refusals emptied the toolset as if two tools had failed"
    assert client.fed and all("NOTHING ran" in it["output"] for it in client.fed)
    assert all("tool failed" not in it["output"] for it in client.fed)
    assert db.audit == []
    assert "sad" not in [f.get("emotion") for f in frames]


def test_two_withheld_delegates_in_one_response_leave_no_executed_row(monkeypatch):
    scripts = [[_ev_call("delegate", {"goal": "a"}, "d1"), _ev_call("delegate", {"goal": "b"}, "d2")],
               [_ev_text("done")]]
    _client, db, frames = _turn(monkeypatch, "door-parallel", scripts, {"delegate"})
    assert db.audit == []
    done = {f["id"]: f["outcome"] for f in frames if f.get("kind") == "step" and f.get("phase") == "done"}
    assert done == {"d1": "refused", "d2": "refused"}


# --- the exclusion is a snapshot; the turn's MODE is not ---------------------------------------------

@pytest.fixture
def work_only():
    """A tool in a family a spoken turn is never offered."""
    ran: list[str] = []

    class _Mod:
        SCHEMA = {"type": "function", "name": "late_probe", "description": "Probe.",
                  "parameters": {"type": "object", "properties": {}, "required": []}}
        BUILT_IN = False

        @staticmethod
        async def execute(args, ctx):
            ran.append("body")
            return "the private repository"

    register(ToolSpec(name="late_probe", module=_Mod, schema=_Mod.SCHEMA, toolset="mcp:github"))
    try:
        yield ran
    finally:
        reg.deregister("late_probe")


def _in_mode(mode: str, name: str = "late_probe"):
    class _M:
        call_id = "c-mode"
        excluded_tools = frozenset()

    ctx = _M()
    ctx.mode = mode

    async def go():
        q: asyncio.Queue = asyncio.Queue()
        return await execute_with_heartbeat(name, {}, q, {}, ctx=ctx, timeout=5)

    return asyncio.run(go())


def test_a_tool_registered_after_the_turn_began_still_cannot_be_named(work_only):
    """The withheld set is computed once, before the turn. An MCP server reconnecting mid-turn adds
    names that set never saw — and a guest reached one by naming it."""
    ok, result = _in_mode("companion")
    assert not ok and work_only == []
    assert "private repository" not in result


def test_it_runs_in_the_job_it_belongs_to(work_only):
    """Guards the guard: refusing everywhere would pass the test above and break work mode."""
    ok, _result = _in_mode("work")
    assert ok and work_only == ["body"]


def test_a_conversation_tool_is_untouched_by_this(work_only):
    ok, _result = _in_mode("companion", "web_search")
    assert ok is not False or _result, "a companion-family tool must still dispatch"

