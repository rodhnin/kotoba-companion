"""Unified agentic loop — the MODEL decides what to use (no keyword router). We drive _run_iterations
with a scripted fake LLM stream and assert the workspace UI is driven by what she actually DOES:
  - chitchat (no tool call) → NO `working` frame, no terminal.
  - a read tool (web/memory) → light: no `working`, no terminal step.
  - an action tool (write/exec/network) → `working` on (once) + `step` + `artifact` + audit row.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from kotoba.core import events
import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


# --- fakes ------------------------------------------------------------------------------------------

def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=f"c_{name}")
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
    """Scripted: each create() call returns the next pre-built list of events."""
    def __init__(self, scripts):
        self._scripts = scripts
        self._i = 0

    async def create(self, **kw):
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _FakeStream(evs)


class _FakeClient:
    def __init__(self, scripts):
        self.responses = _FakeResponses(scripts)


class _FakeDB:
    def __init__(self):
        self.audit = []

    async def insert_audit_log(self, **kw):
        self.audit.append(kw)


def _register_tool(name, risk, result="ok"):
    async def execute(args, ctx):
        return result
    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": name}, __name__=f"tools.x.{name}",
        ANNOUNCE="", HEARTBEAT=[], COMPLETE="done", FAIL="oops", execute=execute,
    )
    reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA,
                          toolset=("file" if risk != "read" else "web"), risk=risk))


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def _run_turn(scripts, session):
    """Run one main-agent turn through _run_iterations with a scripted client; return emitted frames."""
    q = events.register(session)
    db = _FakeDB()
    ctx = ToolContext(db=db, session_id=session, client=None, mode="work")
    ctx.approval = None  # tools here don't need approval

    async def go():
        await loop._run_iterations(
            _FakeClient(scripts), ctx, [{"role": "user", "content": "x"}], asyncio.Queue(), {},
            max_iterations=5, mode="work", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )
        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        return frames, db

    frames, db = asyncio.run(go())
    events.unregister(session)
    kinds = [(f.get("type"), f.get("kind")) for f in frames]
    return frames, kinds, db


def test_chitchat_no_working(clean_registry):
    # Model just talks, no tool call → no working/terminal frames at all.
    _, kinds, db = _run_turn([[_ev_text("Hi there!")]], "u_chat")
    assert ("task", "working") not in kinds
    assert ("task", "step") not in kinds
    assert db.audit == []


def test_read_tool_stays_light(clean_registry):
    _register_tool("fake_read", "read")
    scripts = [[_ev_call("fake_read", {"q": "cats"})], [_ev_text("Here's what I found.")]]
    _, kinds, db = _run_turn(scripts, "u_read")
    assert ("task", "working") not in kinds   # a lookup doesn't light up the workspace
    assert ("task", "step") not in kinds
    assert db.audit == []


def test_action_tool_lights_up_workspace(clean_registry):
    _register_tool("write_file", "write", result="Wrote 5 characters to hello.txt.")
    scripts = [[_ev_call("write_file", {"path": "hello.txt", "content": "hello"})], [_ev_text("All done!")]]
    frames, kinds, db = _run_turn(scripts, "u_act")
    assert ("task", "working") in kinds                      # focus pose + chip turned on
    working_ons = [f for f in frames if f.get("kind") == "working" and f.get("on") is True]
    assert len(working_ons) == 1                             # only once, even if more tools ran
    assert ("task", "step") in kinds                         # terminal got the action
    assert ("task", "artifact") in kinds                     # file surfaced to Files panel
    assert any(a["risk_kind"] == "write" for a in db.audit)  # audited
    # Typed terminal BLOCK: a 'start' frame (kind + action) correlated by id with a 'done' frame (ok+result).
    steps = [f for f in frames if f.get("kind") == "step"]
    start = next(f for f in steps if f.get("phase") == "start")
    done = next(f for f in steps if f.get("phase") == "done")
    assert start["step_kind"] == "file" and "hello.txt" in start["action"]
    assert start["id"] == done["id"] and done["ok"] is True   # same block, completed
    assert "Wrote" in done["result"]


def test_screenshot_image_saved_to_files(clean_registry):
    """A tool that returns images (e.g. a browser screenshot) → the image is saved to file_store and an
    `artifact` frame surfaces it in the Files panel, with a real filename for the model to reference."""
    from kotoba.tools import ToolResult
    from kotoba.core import file_store

    async def execute(args, ctx):
        return ToolResult(text="here's the page", images=["data:image/png;base64,QUJD"])
    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": "browser__browser_take_screenshot"},
        __name__="kotoba.tools.x.shot", ANNOUNCE="", HEARTBEAT=[], COMPLETE="done", FAIL="oops", execute=execute,
    )
    reg.register(ToolSpec(name="browser__browser_take_screenshot", module=mod, schema=mod.SCHEMA,
                          toolset="mcp:browser", risk="network"))

    file_store.clear("u_shot")
    scripts = [[_ev_call("browser__browser_take_screenshot", {})], [_ev_text("Got it!")]]
    frames, kinds, _ = _run_turn(scripts, "u_shot")

    arts = [f for f in frames if f.get("kind") == "artifact"]
    assert arts
    shot_name = arts[0]["path"]
    # Filed by source under screenshots/<source>/, library-unique name screenshot-<n>-<uid>.png (so the
    # shared on-disk library stays organized and never overwrites a capture). This is a browser tool.
    assert shot_name.startswith("screenshots/browser/screenshot-1-") and shot_name.endswith(".png")
    entry = file_store.get_entry("u_shot", shot_name)
    assert entry and entry["kind"] == "image" and entry["content"].startswith("data:image/")
