"""Anti-loop caps must be MODE-AWARE. The companion caps (8 total calls, refuse a tool after 3, drop all
tools after 2 failures) are right for a quick voice turn but STRANGLE work mode: a real browser login+search
is dozens of calls, re-snapshotting the page is correct repetition, and stale-ref failures are routine. With
the companion caps, work mode hit the ceiling mid-task and the loop's forced final text became a fabricated
"all done" summary. These tests pin the work-mode ceilings and the browser exemption.
"""
from __future__ import annotations

import kotoba.core.loop as loop


def test_caps_for_work_vs_companion():
    work_max, work_fail = loop._caps_for("work")
    comp_max, comp_fail = loop._caps_for("companion")
    assert work_max > comp_max          # work gets far more total tool calls
    assert work_fail > comp_fail        # work tolerates more transient failures
    assert comp_max == loop._MAX_TOOL_CALLS and comp_fail == loop._COMPANION_FAIL_LIMIT  # companion untouched


def test_work_ceilings_are_browser_task_sized():
    work_max, work_fail = loop._caps_for("work")
    assert work_max >= 40               # a real login+search needs dozens of calls
    assert work_fail >= 6               # routine stale-ref failures shouldn't kill the task


def test_companion_caps_unchanged_defaults():
    # Regression guard: the companion turn keeps its tight anti-over-preparation caps.
    assert loop._MAX_TOOL_CALLS == 8
    assert loop._PER_TOOL_LIMIT == 3
    assert loop._COMPANION_FAIL_LIMIT == 2


# --- behavioral: browser_* exempt from the per-tool cap in WORK mode (re-snapshot is correct) -----------

import asyncio
import json
import types

import pytest

from kotoba.core import events
import kotoba.tools.registry as reg
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args, i):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=f"c{i}")
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
        self._scripts, self._i = scripts, 0

    async def create(self, **kw):
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _FakeStream(evs)


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


def _run(mode, calls):
    """Drive _run_iterations: the model calls browser_snapshot `calls` times (varied args), then answers.
    Returns how many times the tool's execute() actually ran."""
    ran = {"n": 0}

    async def execute(args, ctx):
        ran["n"] += 1
        return "snapshot ok"

    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": "browser__browser_snapshot"},
        __name__="kotoba.tools.x.browser_snapshot", ANNOUNCE="", HEARTBEAT=[], COMPLETE="", FAIL="", execute=execute,
    )
    reg.register(ToolSpec(name="browser__browser_snapshot", module=mod, schema=mod.SCHEMA,
                          toolset="mcp:browser", risk="network"))

    scripts = [[_ev_call("browser__browser_snapshot", {"depth": i}, i)] for i in range(calls)]
    scripts.append([_ev_text("done")])

    sess = f"caps_{mode}_{calls}"
    events.register(sess)
    ctx = ToolContext(db=_FakeDB(), session_id=sess, client=None, mode=mode)
    ctx.approval = None

    async def go():
        await loop._run_iterations(
            _FakeClient(scripts), ctx, [{"role": "user", "content": "x"}], asyncio.Queue(), {},
            max_iterations=calls + 2, mode=mode, allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )
    asyncio.run(go())
    events.unregister(sess)
    return ran["n"]


def test_browser_snapshot_not_capped_in_work_mode(clean_registry):
    # 5 varied snapshot calls in WORK mode → all 5 run (re-snapshot is the correct workflow).
    assert _run("work", 5) == 5


def test_browser_snapshot_capped_in_companion_mode(clean_registry):
    # Same 5 calls in COMPANION mode → refused after _PER_TOOL_LIMIT (3); execute runs at most 3 times.
    assert _run("companion", 5) <= loop._PER_TOOL_LIMIT
