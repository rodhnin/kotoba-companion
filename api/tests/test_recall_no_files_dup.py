"""recall_image / view_capture RE-OPEN images that already live in visual-memory / Files — they are NOT
new captures. The loop must NOT re-save their returned images to Files (the "photos appear in Files again"
duplication every time she recalls). A real browser screenshot still lands in Files."""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from kotoba.core import events
import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.tools import ToolContext, ToolResult
from kotoba.tools.registry import ToolSpec


def _ev_call(name, args, i):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=f"c{i}")
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


class _Stream:
    def __init__(self, evs): self._evs = evs
    def __aiter__(self):
        async def g():
            for e in self._evs: yield e
        return g()


class _Resp:
    def __init__(self, scripts): self._s, self._i = scripts, 0
    async def create(self, **kw):
        s = self._s[min(self._i, len(self._s) - 1)]; self._i += 1; return _Stream(s)


class _Client:
    def __init__(self, scripts): self.responses = _Resp(scripts)


class _DB:
    async def insert_audit_log(self, **kw): pass


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def _run_one_tool(tool_name, clean_registry):
    """Drive _run_iterations: the model calls `tool_name` once (returns an image), then answers. Captures
    emitted task events; returns the list of 'artifact' (Files) events."""
    _IMG = "data:image/png;base64,QUJD"

    async def execute(args, ctx):
        return ToolResult(text="here", images=[_IMG])

    mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": tool_name},
                                __name__=f"x.{tool_name}", ANNOUNCE="", HEARTBEAT=[], COMPLETE="", FAIL="",
                                execute=execute)
    reg.register(ToolSpec(name=tool_name, module=mod, schema=mod.SCHEMA, toolset="memory", risk="read"))

    artifacts = []
    sess = f"dup_{tool_name}"
    events.register(sess)
    orig_emit = loop.emit_task

    async def cap_emit(session_id, kind, **data):
        if kind == "artifact":
            artifacts.append(data)
        return await orig_emit(session_id, kind, **data)

    loop.emit_task = cap_emit
    try:
        scripts = [[_ev_call(tool_name, {"query": "x"}, 0)], [_ev_text("done")]]
        ctx = ToolContext(db=_DB(), session_id=sess, client=None, mode="companion")
        ctx.approval = None

        async def go():
            await loop._run_iterations(
                _Client(scripts), ctx, [{"role": "user", "content": "x"}], asyncio.Queue(), {},
                max_iterations=3, mode="companion", allow_risk={"read", "write", "exec", "network"},
                toolset_filter=None,
            )
        asyncio.run(go())
    finally:
        loop.emit_task = orig_emit
        events.unregister(sess)
    return artifacts


def test_recall_image_does_not_create_files_artifact(clean_registry):
    assert _run_one_tool("recall_image", clean_registry) == []   # no Files duplication


def test_view_capture_does_not_create_files_artifact(clean_registry):
    assert _run_one_tool("view_capture", clean_registry) == []
