"""Research citations must survive the delegate fan-out.

Live QA: a multi-helper research turn produced a good report with ZERO source URLs despite
25+ web searches. Two mechanics broke it, both pinned here: (1) ctx.child() created a fresh ToolContext,
so the URLs a helper's searches captured (ctx._sources, from url_citation annotations) died with the
child; (2) the loop injected citations.pending_note AFTER the model response that carried the tool calls,
so sources collected WHILE TOOLS RAN (exactly the delegate case) reached input_items only after the model
had already emitted the write_file with the report. The fix: child() shares the parent's accumulator, and
the note is injected at the START of each iteration, before the model call.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.core import citations, events
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


def _cite(url, title=""):
    return {"type": "url_citation", "url": url, "title": title}


# --- ctx.child() shares the parent's accumulator ----------------------------------------------------

def test_child_shares_parent_sources_accumulator():
    parent = ToolContext(db=None, session_id="s", mode="work")
    citations.collect_from_annotation(parent, _cite("https://parent.example", "P"))
    child = parent.child("sub1")
    assert child._sources is parent._sources
    citations.collect_from_annotation(child, _cite("https://helper.example", "H"))
    assert parent._sources == {"https://parent.example": "P", "https://helper.example": "H"}


def test_child_note_covers_only_its_own_new_sources():
    """A child shares the parent's accumulator but keeps its own high-water mark, so at spawn it has
    nothing new to report and its note afterwards names only what IT found."""
    parent = ToolContext(db=None, session_id="s", mode="work")
    citations.collect_from_annotation(parent, _cite("https://old.example", "Old"))
    child = parent.child("sub1")
    assert citations.pending_note(child) is None
    citations.collect_from_annotation(child, _cite("https://new.example", "New"))
    note = citations.pending_note(child)
    assert note and "https://new.example" in note and "https://old.example" not in note


def test_parent_note_includes_helper_sources_after_spawn():
    parent = ToolContext(db=None, session_id="s", mode="work")
    child = parent.child("sub1")
    citations.collect_from_annotation(child, _cite("https://found-by-helper.example", "F"))
    note = citations.pending_note(parent)
    assert note and "https://found-by-helper.example" in note


# --- delegate's real code path propagates the child's sources --------------------------------------

def test_delegate_propagates_helper_sources_to_parent(monkeypatch):
    from kotoba.tools.action import delegate

    async def fake_run_iterations(client, ctx, input_items, queue, soul_patterns, **kw):
        citations.collect_from_annotation(ctx, _cite("https://helper-src.example", "HS"))
        return "helper summary"

    import kotoba.core.loop; from kotoba import core

    monkeypatch.setattr(core.loop, "_run_iterations", fake_run_iterations)
    monkeypatch.setattr("kotoba.core.llm.get_client", lambda: object())

    async def go():
        events.register("sess-cite")
        parent = ToolContext(db=None, session_id="sess-cite", mode="work", spawn_depth=0)
        out = await delegate.execute({"goal": "research X", "toolset": "research"}, parent)
        events.unregister("sess-cite")
        return parent, out

    parent, out = asyncio.run(go())
    assert out == "helper summary"
    assert "https://helper-src.example" in getattr(parent, "_sources", {})
    note = citations.pending_note(parent)
    assert note and "https://helper-src.example" in note


# --- the loop injects the note BEFORE the next model call (the synthesis sees the URLs) -------------

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


class _RecordingResponses:
    def __init__(self, scripts):
        self._scripts, self._i = scripts, 0
        self.calls: list[dict] = []

    async def create(self, **kw):
        self.calls.append({"input": list(kw.get("input") or [])})
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _FakeStream(evs)


class _FakeClient:
    def __init__(self, scripts):
        self.responses = _RecordingResponses(scripts)


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


def test_sources_collected_during_tool_run_visible_before_next_model_call(clean_registry):
    """A tool (stand-in for a delegate helper batch) adds sources while it RUNS; the very next model call
    must already carry the 'Sources found' developer note — that call is where the report gets written."""

    async def execute(args, ctx):
        citations.collect_from_annotation(ctx, _cite("https://during-tool.example", "D"))
        return "collected"

    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": "cite_probe"},
        __name__="kotoba.tools.x.cite_probe", ANNOUNCE="", HEARTBEAT=[], COMPLETE="", FAIL="", execute=execute,
    )
    reg.register(ToolSpec(name="cite_probe", module=mod, schema=mod.SCHEMA, toolset="web", risk="read"))

    scripts = [[_ev_call("cite_probe", {}, 1)], [_ev_text("done")]]
    client = _FakeClient(scripts)
    events.register("cite_order")
    ctx = ToolContext(db=_FakeDB(), session_id="cite_order", client=None, mode="work")
    ctx.approval = None

    async def go():
        await loop._run_iterations(
            client, ctx, [{"role": "user", "content": "research this"}], asyncio.Queue(), {},
            max_iterations=4, mode="work", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )
    asyncio.run(go())
    events.unregister("cite_order")

    def _src_notes(items):
        return [m for m in items if isinstance(m, dict) and m.get("role") == "developer"
                and str(m.get("content")).startswith("[internal] Real URLs captured")]

    assert not _src_notes(client.responses.calls[0]["input"])
    notes = _src_notes(client.responses.calls[1]["input"])
    assert len(notes) == 1, "the sources note must reach the model call right after the tool ran"
    assert "https://during-tool.example" in notes[0]["content"]


# --- the fan-out guidance actually instructs citing the helpers' URLs ------------------------------

def test_delegation_guidance_instructs_citing_real_urls():
    from kotoba.core.tool_guidance import DELEGATION_GUIDANCE

    g = DELEGATION_GUIDANCE
    assert "internal note" in g and "REAL URLs" in g
    assert "Never invent" in g
