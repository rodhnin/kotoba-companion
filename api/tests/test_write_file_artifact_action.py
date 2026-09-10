"""The `artifact` frame's action must tell an overwrite from a creation. `write_file` always emitted
"created", so FilesPanel badged a file NEW ("new", mint) on every overwrite of the same report. The
existence check has to run BEFORE the tool does — afterwards the file exists either way — so the loop
snapshots it next to the execution, not at the emit.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.core import events
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args, call_id):
    item = types.SimpleNamespace(type="function_call", name=name,
                                 arguments=json.dumps(args), call_id=call_id)
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
        self._i = 0

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


def _register_writing_tool(workdir):
    """A write_file stand-in that REALLY writes into the jailed workdir, so the loop's pre-execution
    existence check sees what the real tool would leave behind."""
    async def execute(args, ctx):
        p = workdir / args["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        return f"Wrote {len(args['content'])} characters to {args['path']}."
    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": "write_file"}, __name__="tools.x.write_file",
        ANNOUNCE="", HEARTBEAT=[], COMPLETE="done", FAIL="oops", execute=execute,
    )
    reg.register(ToolSpec(name="write_file", module=mod, schema=mod.SCHEMA,
                          toolset="file", risk="write"))


def _run_turn(scripts, session, workdir):
    q = events.register(session)
    ctx = ToolContext(db=_FakeDB(), session_id=session, client=None, mode="work", workdir=workdir)
    ctx.approval = None

    async def go():
        await loop._run_iterations(
            _FakeClient(scripts), ctx, [{"role": "user", "content": "x"}], asyncio.Queue(), {},
            max_iterations=5, mode="work", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )
        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        return frames

    frames = asyncio.run(go())
    events.unregister(session)
    return frames


def test_first_write_is_created_second_is_edited(clean_registry, tmp_path):
    _register_writing_tool(tmp_path)
    scripts = [
        [_ev_call("write_file", {"path": "notes.txt", "content": "one"}, "c1")],
        [_ev_call("write_file", {"path": "notes.txt", "content": "two words"}, "c2")],
        [_ev_text("All done!")],
    ]
    frames = _run_turn(scripts, "u_art1", tmp_path)
    arts = [f for f in frames if f.get("kind") == "artifact"]
    assert [a["action"] for a in arts] == ["created", "edited"]
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "two words"


def test_overwrite_of_a_preexisting_file_is_edited(clean_registry, tmp_path):
    (tmp_path / "report.txt").write_text("old", encoding="utf-8")
    _register_writing_tool(tmp_path)
    scripts = [
        [_ev_call("write_file", {"path": "report.txt", "content": "fresh"}, "c1")],
        [_ev_text("Saved.")],
    ]
    frames = _run_turn(scripts, "u_art2", tmp_path)
    arts = [f for f in frames if f.get("kind") == "artifact"]
    assert [a["action"] for a in arts] == ["edited"]
