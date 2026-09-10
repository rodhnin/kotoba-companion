from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.tools.registry as reg
from kotoba.core import events
from kotoba.tools.registry import ToolSpec, register


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
        self._scripts, self._i, self.fed = scripts, 0, []
        self.responses = self

    async def create(self, **kw):
        self.fed.extend(it for it in kw["input"]
                        if isinstance(it, dict) and it.get("type") == "function_call_output")
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


@pytest.fixture
def withheld_probe():
    class _Mod:
        SCHEMA = {"type": "function", "name": "reach_probe", "description": "Probe.",
                  "parameters": {"type": "object", "properties": {}, "required": []}}
        BUILT_IN = False

        @staticmethod
        async def execute(args, ctx):
            return "the operator's private note"

    register(ToolSpec(name="reach_probe", module=_Mod, schema=_Mod.SCHEMA, toolset="file"))
    try:
        yield
    finally:
        reg.deregister("reach_probe")


def _turn(monkeypatch, sid, parent_scripts, helper_scripts, excluded):
    import kotoba.core.loop as loop

    parent, helper, db = _Client(parent_scripts), _Client(helper_scripts), _DB()
    monkeypatch.setattr(loop, "get_client", lambda: parent)
    monkeypatch.setattr("kotoba.core.llm.get_client", lambda *a, **k: helper)

    async def go():
        q = events.register(sid)
        try:
            await loop.agentic_loop([{"role": "user", "content": "go"}], sid, db, asyncio.Queue(), {},
                                    mode="work", channel="text", register="text",
                                    exclude_tools=frozenset(excluded), narrate_tools=False)
            return [q.get_nowait() for _ in range(q.qsize())]
        finally:
            events.unregister(sid, q)

    frames = asyncio.run(go())
    return parent, db, frames


def test_helpers_fanned_out_after_a_refusal_are_graded_on_their_own_result(monkeypatch, withheld_probe):
    scripts = [
        [_ev_call("reach_probe", {}, "c1")],
        [_ev_call("delegate", {"goal": "a"}, "d1"), _ev_call("delegate", {"goal": "b"}, "d2")],
        [_ev_text("done")],
    ]
    parent, db, frames = _turn(monkeypatch, "stale-id", scripts, [[_ev_text("helper summary")]],
                               {"reach_probe"})

    done = {f["id"]: f["outcome"] for f in frames if f.get("kind") == "step" and f.get("phase") == "done"}
    assert "c1" not in done
    assert done["d1"] == "ok" and done["d2"] == "ok", done
    assert [a["detail"] for a in db.audit] == ["executed", "executed"]
    fed = {it["call_id"]: it["output"] for it in parent.fed}
    assert "tool failed" not in fed["d1"] and "tool failed" not in fed["d2"]
    finished = [f for f in frames if f.get("kind") == "subagent_done"]
    # Counted first: `all` over an empty list is true, so a run that emitted no frame at all read
    # exactly like two helpers that came back fine.
    assert len(finished) == 2, finished
    assert all(f["ok"] for f in finished)
