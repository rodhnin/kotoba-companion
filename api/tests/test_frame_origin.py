"""Every frame an agentic run emits names the run that emitted it.

The live defect this closes: rows from a detached long job landed inside a new turn's reply and
split it in half — every client handler routed on "is a turn running right now?" because nothing on
the wire said whose frame it was. `run_id` is the origin field: minted once per loop invocation,
stamped on every frame the run emits, and the work-runner mints one for its detached loop and stamps
its own bracket with the SAME id, so a client can pair the bracket with the loop frames inside it. A
helper's child context inherits the parent's id, wherever it draws. Consumers read the key
defensively; a frame without it predates the field and nothing breaks."""
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


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args, call_id):
    item = types.SimpleNamespace(
        type="function_call", name=name, arguments=json.dumps(args), call_id=call_id
    )
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
        self._scripts, self._i = scripts, 0
        self.responses = self

    async def create(self, **kw):
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _Stream(evs)


class _DB:
    async def insert_audit_log(self, **kw):
        pass

    async def list_approved_commands(self):
        return []

    async def save_approved_command(self, *a, **k):
        pass


_SCRIPT = [
    [_ev_call("shell", {"command": "ls"}, "c1"), _ev_call("memory_recall", {"query": "me"}, "c2")],
    [_ev_text("listo")],
]


def _run_turn(monkeypatch, sid, **kw):
    import kotoba.tools.registry as reg

    monkeypatch.setattr(loop, "model_name", lambda *a, **k: "test-model")
    monkeypatch.setattr(loop, "model_call_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(loop, "is_reasoning_model", lambda *a, **k: False)
    monkeypatch.setattr(loop, "get_client", lambda: _Client([list(t) for t in _SCRIPT]))
    monkeypatch.setattr("kotoba.tools.registry._check_cache", {})
    reg.discover()

    async def fake_exec(name, args, queue, patterns, ctx, timeout=0):
        return True, "ran fine"

    monkeypatch.setattr(loop, "execute_with_heartbeat", fake_exec)

    async def go():
        q = events.register(sid)
        await loop.agentic_loop(
            [{"role": "user", "content": "hola"}], sid, _DB(),
            asyncio.Queue(), {}, mode="companion", channel="text", **kw,
        )
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister(sid)
        return frames

    return asyncio.run(go())


def test_every_frame_of_one_turn_carries_the_same_minted_run_id(monkeypatch):
    frames = _run_turn(monkeypatch, "origin-t1")
    assert frames, "the scripted turn must have emitted frames"
    rids = {f.get("run_id") for f in frames}
    assert len(rids) == 1, f"one run, one id — got {rids}"
    rid = rids.pop()
    assert isinstance(rid, str) and rid, "the id must be minted even when the caller passed none"
    kinds = {f.get("kind") or f.get("type") for f in frames}
    assert {"step", "peek", "emotion", "working"} <= kinds, f"missing frame kinds: {kinds}"


def test_two_invocations_mint_two_different_run_ids(monkeypatch):
    a = _run_turn(monkeypatch, "origin-t2a")
    b = _run_turn(monkeypatch, "origin-t2b")
    rid_a = a[0].get("run_id")
    rid_b = b[0].get("run_id")
    assert rid_a and rid_b and rid_a != rid_b


def test_a_caller_supplied_run_id_is_used_verbatim(monkeypatch):
    frames = _run_turn(monkeypatch, "origin-t3", run_id="turn-a1")
    assert frames and {f.get("run_id") for f in frames} == {"turn-a1"}


class _WorkDB:
    async def ensure_session(self, *a, **k):
        pass

    async def insert_turn(self, *a, **k):
        pass


def test_the_detached_work_bracket_and_its_loop_share_one_run_id(monkeypatch):
    """The routing story only works if the client can LEARN the long job's id: work_started carries the
    same run_id the detached loop will stamp on every frame it emits."""
    seen = {}

    async def fake_loop(*a, **k):
        seen.update(k)
        return "Done."

    monkeypatch.setattr("kotoba.core.loop.agentic_loop", fake_loop)
    ws._state.clear()
    ws._tasks.clear()

    async def go():
        q = events.register("origin-work")
        ws.start("origin-work", "the goal")
        await wr._run("origin-work", "the goal", _WorkDB(), {}, mcp=None)
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("origin-work")
        return frames

    frames = asyncio.run(go())
    ws._state.clear()
    ws._tasks.clear()
    rid = seen.get("run_id")
    assert isinstance(rid, str) and rid, "the runner must hand its minted id to the loop"
    kinds = [f.get("kind") for f in frames]
    assert "work_started" in kinds and "work_done" in kinds
    assert {f.get("run_id") for f in frames} == {rid}


def test_a_helpers_frames_carry_the_parents_run_id(monkeypatch):
    seen = {}

    async def fake_run_iterations(client, ctx, *a, **kw):
        seen["child_rid"] = getattr(ctx, "run_id", None)
        return "found it"

    monkeypatch.setattr(loop, "_run_iterations", fake_run_iterations)
    monkeypatch.setattr("kotoba.core.llm.get_client", lambda: object())

    async def go():
        q = events.register("origin-sub")
        ctx = ToolContext(db=None, session_id="origin-sub", mode="work", spawn_depth=0)
        ctx.run_id = "r-parent"
        await delegate.execute({"goal": "look it up"}, ctx)
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("origin-sub")
        return frames

    frames = asyncio.run(go())
    assert seen["child_rid"] == "r-parent"
    assert frames and {f.get("run_id") for f in frames} == {"r-parent"}


def test_the_task_list_frame_names_its_run():
    from kotoba.tools.builtin import todo

    async def go():
        q = events.register("origin-todo")
        ctx = ToolContext(db=None, session_id="origin-todo", mode="work")
        ctx.run_id = "r-todo"
        await todo.execute({"steps": ["uno", "dos"], "active": 1}, ctx)
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("origin-todo")
        return frames

    frames = asyncio.run(go())
    lists = [f for f in frames if f.get("kind") == "task_list"]
    assert lists and all(f.get("run_id") == "r-todo" for f in lists)


def test_report_ready_and_its_artifact_name_their_run(monkeypatch):
    from kotoba.tools.action import make_report

    monkeypatch.setattr(make_report, "_template", lambda: "{{TITLE}}|{{SUMMARY}}")

    async def go():
        q = events.register("origin-rep")
        ctx = ToolContext(db=None, session_id="origin-rep", mode="work")
        ctx.run_id = "r-rep"
        await make_report.execute({"title": "Latency", "summary": "It is fine."}, ctx)
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("origin-rep")
        return frames

    frames = asyncio.run(go())
    kinds = {f.get("kind") for f in frames}
    assert "report_ready" in kinds
    assert all(f.get("run_id") == "r-rep" for f in frames), frames


def test_a_deferred_rows_completion_carries_the_run_that_opened_it(monkeypatch):
    from kotoba.core import deferred_exec

    captured = {}

    async def fake_run(ctx, action, runner, label, row, key, family):
        captured.update(row)

    monkeypatch.setattr(deferred_exec, "_run_on_approval", fake_run)

    async def go():
        ctx = ToolContext(db=None, session_id="origin-def", mode="work", channel="text")
        ctx.run_id = "r-def"
        ctx.call_id = "c1"
        deferred_exec.schedule(ctx, "run: thing", None, label="thing", step_kind="shell")
        await asyncio.sleep(0)
        q = events.register("origin-def")
        await deferred_exec._finish_step("origin-def", captured, True, "out", "ok")
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("origin-def")
        return frames

    frames = asyncio.run(go())
    assert captured.get("run_id") == "r-def"
    assert frames and frames[0].get("run_id") == "r-def"


def test_a_frame_with_a_falsy_run_id_travels_without_the_key():
    """"Key present ⇒ real id" is the contract routing relies on: a producer with no run behind it
    passes run_id="" and the wire carries no key at all, never an empty one a comparison could match."""

    async def go():
        q = events.register("origin-empty")
        await events.emit_task("origin-empty", "step", phase="start", id="x", run_id="")
        await events.emit_emotion("origin-empty", "happy")
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("origin-empty")
        return frames

    frames = asyncio.run(go())
    assert all("run_id" not in f for f in frames), frames
