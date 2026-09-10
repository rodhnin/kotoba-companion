"""The exact-signature dedup used to tell the model a repeated call "is DONE — the earlier result still
stands", assuming the first attempt had succeeded. It did not
always: the population most likely to repeat a call verbatim is the hallucinated-tool population that
now returns an honest failure, and a call rejected by the per-tool cap gets counted too — so its verbatim
repeat was told "DONE" over a call that FAILED or never ran.

The fix records each signature's real outcome (`outcomes[sig]`, set only after the tool actually runs)
and branches the dedup message: a repeat after SUCCESS keeps today's "it is DONE" line; a repeat after a
FAILURE — or after a call that never ran (cap-rejected) — gets a recovery line that tells the model to
change approach, never that it is done."""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args, call_id):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)
    return types.SimpleNamespace(type="response.output_item.done", item=item)


class _FakeStream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _FakeResponses:
    def __init__(self, scripts):
        self._scripts = scripts
        self.calls: list[dict] = []

    async def create(self, **kw):
        self.calls.append({"input": [dict(it) if isinstance(it, dict) else it for it in kw.get("input", [])]})
        return _FakeStream(self._scripts[min(len(self.calls) - 1, len(self._scripts) - 1)])


class _FakeClient:
    def __init__(self, scripts):
        self.responses = _FakeResponses(scripts)


class _FakeDB:
    async def insert_audit_log(self, **kw):
        pass


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def _register_tool(name, result):
    async def execute(args, ctx):
        return result(args) if callable(result) else result
    mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": name}, __name__=f"tools.x.{name}",
                                ANNOUNCE="", HEARTBEAT=[], COMPLETE="done", FAIL="oops", execute=execute)
    reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA, toolset="file", risk="read"))


def _run(scripts, session="m4"):
    client = _FakeClient(scripts)
    ctx = ToolContext(db=_FakeDB(), session_id=session, client=None, mode="companion")
    ctx.approval = None

    async def go():
        return await loop._run_iterations(
            client, ctx, [{"role": "user", "content": "go"}], asyncio.Queue(), {},
            max_iterations=10, mode="companion", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )

    asyncio.run(go())
    return client.responses.calls


def _outputs(items):
    return [it for it in items if isinstance(it, dict) and it.get("type") == "function_call_output"]


def _last_output_for(calls, idx):
    """The last function_call_output fed back in create call `idx`'s input. An output produced in
    iteration N lands in create call N+1's input."""
    return _outputs(calls[idx]["input"])[-1]["output"]


def test_repeat_of_a_failed_call_is_told_to_recover_not_that_it_is_done(clean_registry):
    """A tool that returns nothing fails (ok=False) every call; the verbatim repeat must get the honest
    recovery line, never 'it is DONE'."""
    _register_tool("flaky", lambda a: None)
    calls = _run([
        [_ev_call("flaky", {"x": 1}, "c1")],
        [_ev_call("flaky", {"x": 1}, "c2")],
        [_ev_text("done")],
    ])
    first = _last_output_for(calls, 1)
    second = _last_output_for(calls, 2)
    assert "failed" in first.lower(), "the first attempt really did fail"
    assert "it is DONE" not in second, "the repeat must not be told the failed result stands"
    assert "did NOT succeed" in second
    assert "different" in second.lower()


def test_repeat_of_a_successful_call_still_gets_the_done_message(clean_registry):
    """When the first call really succeeded, today's message is preserved verbatim."""
    _register_tool("readit", lambda a: "the file content")
    calls = _run([
        [_ev_call("readit", {"p": "a.txt"}, "c1")],
        [_ev_call("readit", {"p": "a.txt"}, "c2")],
        [_ev_text("done")],
    ])
    second = _last_output_for(calls, 2)
    assert "it is DONE" in second
    assert "the earlier result for this call still stands" in second


def test_repeat_of_a_cap_rejected_call_is_not_told_it_is_done(clean_registry):
    """The second aggravator: a call rejected by the per-tool cap increments `attempts` but never runs,
    so its verbatim repeat used to get 'the earlier result still stands' over a call that never happened.
    Its outcome is None (never recorded) -> the recovery line, not 'it is DONE'."""
    _register_tool("readit", lambda a: "ok")
    cap = loop._PER_TOOL_LIMIT
    scripts = [[_ev_call("readit", {"i": i}, f"c{i}")] for i in range(cap)]
    scripts.append([_ev_call("readit", {"i": 99}, "c_cap")])
    scripts.append([_ev_call("readit", {"i": 99}, "c_cap_again")])
    scripts.append([_ev_text("done")])
    calls = _run(scripts, session="m4cap")

    capped = _last_output_for(calls, cap + 1)
    repeat = _last_output_for(calls, cap + 2)
    assert "did not run" in capped.lower(), "the call was cap-rejected before running"
    assert "it is DONE" not in repeat
    assert "did NOT succeed" in repeat
