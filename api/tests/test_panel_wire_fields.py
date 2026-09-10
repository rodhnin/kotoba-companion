"""Three columns a renderer could not fill because the frame never carried them.

Every one of these was readable only IN PROCESS, at the call site, so the CLI (which does run in
process) got away with reading it there and every other client had to invent it: `subagent_spawned`
named no toolset, so every helper on screen was the same anonymous "helper"; `subagent_step` carried
two independent strings with no outcome, so a failure was distinguishable only by the "  ! " prefix
_result_text writes; `work_started` named no goal, so the job's own row had nothing to say.
"""
from __future__ import annotations

import asyncio
import json
import types

import kotoba.core.loop as loop
import kotoba.core.work_runner as wr
import kotoba.core.work_state as ws
from kotoba.core import events
from kotoba.tools import ToolContext
from kotoba.tools.action import delegate


def _spawn_frame(toolset, monkeypatch):
    async def fake_run_iterations(client, ctx, input_items, queue, soul_patterns, **kw):
        return "done"

    monkeypatch.setattr(loop, "_run_iterations", fake_run_iterations)
    monkeypatch.setattr("kotoba.core.llm.get_client", lambda: object())

    async def go():
        q = events.register("wire-spawn")
        ctx = ToolContext(db=None, session_id="wire-spawn", mode="work", spawn_depth=0)
        args = {"goal": "read both pages"}
        if toolset is not None:
            args["toolset"] = toolset
        await delegate.execute(args, ctx)
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("wire-spawn")
        return frames

    frames = asyncio.run(go())
    return next(f for f in frames if f.get("kind") == "subagent_spawned")


def test_subagent_spawned_names_the_toolset(monkeypatch):
    assert _spawn_frame("browser", monkeypatch)["toolset"] == "browser"
    assert _spawn_frame("research", monkeypatch)["toolset"] == "research"


def test_a_helper_with_no_toolset_asked_for_is_the_default_one(monkeypatch):
    assert _spawn_frame(None, monkeypatch)["toolset"] == "research"


def test_a_toolset_outside_the_enum_is_labelled_as_what_it_actually_got(monkeypatch):
    """The schema is not strict, so this arrives. _TOOLSET_MAP falls the helper back to 'web' tools —
    a label that kept the model's word would claim a specialist that was never assembled."""
    assert _spawn_frame("telepathy", monkeypatch)["toolset"] == "web"
    assert delegate._TOOLSET_MAP.get("telepathy", "web") == "web"


def _done(item):
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _tool(call_id, name, args=None):
    return _done(types.SimpleNamespace(type="function_call", name=name,
                                       arguments=json.dumps(args or {}), call_id=call_id))


def _websearch():
    return _done(types.SimpleNamespace(type="web_search_call", id="ws1", action=None))


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


def _helper_steps(turns, monkeypatch, ok=True):
    async def fake_exec(name, args, queue, patterns, ctx, timeout=0):
        return ok, "the output" if ok else "it blew up"

    monkeypatch.setattr(loop, "execute_with_heartbeat", fake_exec)
    sid = "wire-steps"

    async def go():
        q = events.register(sid)
        ctx = ToolContext(db=_DB(), session_id=sid, client=None, mode="work", subagent_id="h1")
        ctx.approval = None
        await loop._run_iterations(
            _Client(turns), ctx, [{"role": "user", "content": "x"}], asyncio.Queue(), {},
            max_iterations=len(turns), mode="work",
            allow_risk={"read", "write", "exec", "network"}, toolset_filter=None,
        )
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister(sid)
        return [f for f in frames if f.get("kind") == "subagent_step"]

    return asyncio.run(go())


def test_the_line_that_says_what_she_is_about_to_do_claims_no_outcome(monkeypatch):
    steps = _helper_steps([[_tool("c1", "shell", {"command": "ls"})], [_done(
        types.SimpleNamespace(type="message", content=[]))]], monkeypatch)
    action = next(s for s in steps if s["text"].startswith("$ ls"))
    assert "ok" in action and action["ok"] is None


def test_a_helpers_result_line_carries_its_outcome(monkeypatch):
    """The last assertion is the whole reason for the field: the only OTHER trace of a failed helper
    step is a two-space-bang prefix on a display string, which a renderer would have had to sniff."""
    turns = [[_tool("c1", "shell", {"command": "ls"})], [_done(
        types.SimpleNamespace(type="message", content=[]))]]

    good = _helper_steps(turns, monkeypatch, ok=True)
    assert [s["ok"] for s in good] == [None, True]

    bad = _helper_steps(turns, monkeypatch, ok=False)
    assert [s["ok"] for s in bad] == [None, False]
    assert bad[-1]["text"].startswith("  ! ")


def test_a_helpers_built_in_web_search_is_a_finished_action(monkeypatch):
    steps = _helper_steps([[_websearch()], [_done(
        types.SimpleNamespace(type="message", content=[]))]], monkeypatch)
    assert [(s["text"], s["ok"]) for s in steps] == [("searched the web", True)]


class _WorkDB:
    async def ensure_session(self, *a, **k): pass
    async def insert_turn(self, *a, **k): pass


def _work_frames(goal, monkeypatch):
    emitted = []

    async def fake_emit(session_id, kind, **data):
        emitted.append((kind, data))

    async def fake_loop(*a, **k):
        return "Done."

    monkeypatch.setattr(wr, "emit_task", fake_emit)
    monkeypatch.setattr("kotoba.core.loop.agentic_loop", fake_loop)
    ws._state.clear(); ws._tasks.clear()
    ws.start("wire-work", goal)
    asyncio.run(wr._run("wire-work", goal, _WorkDB(), {}, mcp=None))
    ws._state.clear(); ws._tasks.clear()
    return emitted


def test_work_started_names_the_job(monkeypatch):
    frames = _work_frames("get the real latency numbers", monkeypatch)
    started = next(d for k, d in frames if k == "work_started")
    assert started["goal"] == "get the real latency numbers"


def test_a_runaway_goal_cannot_bloat_the_frame(monkeypatch):
    frames = _work_frames("x" * 4000, monkeypatch)
    started = next(d for k, d in frames if k == "work_started")
    assert len(started["goal"]) == 500


# --- the key she is actually using -----------------------------------------------------------------

class _NoKeys:
    async def get_key(self, name):
        return None


def _providers(monkeypatch, env: str | None):
    import asyncio

    from kotoba.core import settings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if env is not None:
        monkeypatch.setenv("OPENAI_API_KEY", env)
    out = asyncio.run(settings._llm_settings({}, [], _NoKeys()))
    return {p["id"]: p for p in out["providers"]}


def test_a_key_in_the_environment_is_named_as_the_one_in_use(monkeypatch):
    """The runtime falls back to it, so it IS her key. The panel asked only whether the app had one
    saved, so a working install rendered an empty box and looked unconfigured."""
    openai = _providers(monkeypatch, "sk-arealkeyvalue")["openai"]
    assert openai["has_key"] is False
    assert openai["key_env"] == "OPENAI_API_KEY"


def test_nothing_is_claimed_when_there_is_no_key_anywhere(monkeypatch):
    openai = _providers(monkeypatch, None)["openai"]
    assert openai["has_key"] is False and openai["key_env"] == ""


def test_a_placeholder_copied_from_the_example_is_not_a_key(monkeypatch):
    """The same filter the runtime uses: `.env.example` ships a stand-in, and calling it configured
    sends someone off to debug a 401 instead of pasting a key."""
    from kotoba.core import llm

    raw = "sk-..."                                  # what api/.env.example ships, verbatim
    assert llm.looks_placeholder(raw), "the probe value must be one the filter really rejects"
    assert _providers(monkeypatch, raw)["openai"]["key_env"] == ""

