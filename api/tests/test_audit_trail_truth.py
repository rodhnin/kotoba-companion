"""Live QA: the audit log recorded EMISSION, not execution — and called it "approved".

Measured on the live database: approver `work-loop` 387 rows, `user` 12, `auto-safe` 2. Two causes:
approved=1/approver="work-loop" was written for every action tool call, including one that only put
an approval card on screen and never ran — and the same field doubled for "the tool succeeded", so a
tool that ran and errored was filed as denied. The deferred path also asked the user directly, bypassing
ApprovalGate's own auditor, so approvals a human actually gave for host commands were the ones missing.

A row now means one event, named in `detail`: "decision" (someone said yes or no) or "executed"/
"executed:failed" (it really ran), with `approver` saying on whose authority."""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.core.deferred_exec as de
import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.core import events, work_state
from kotoba.core.approval import ApprovalGate
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


class _Ctx:
    def __init__(self, gate, session_id):
        self.approval = gate
        self.mode = "companion"
        self.session_id = session_id
        self.call_id = "c1"
        self.user_text = "run the thing"
        self._open_steps = {}


def _co(value):
    async def _c(*a, **k):
        return value
    return _c()


def _gate_recording(rows):
    async def audit(action, risk, ok, who, detail):
        rows.append({"action": action, "approved": ok, "approver": who, "detail": detail})

    return ApprovalGate(host_exec=True, audit=audit)


def _run_deferred(ctx, action, runner, monkeypatch, answer):
    monkeypatch.setattr(de, "request_approval", lambda s, a, **k: _co(answer))
    # A real session has an SSE consumer, and that is what makes the answer attributable to a HUMAN: with
    # no queue registered the card emit is a silent no-op, so the audit records approver="error" instead.
    q = events.register(ctx.session_id)

    async def _main():
        de.schedule(ctx, action, runner, label=action, step_kind="shell")
        await asyncio.sleep(0)
        for task in list(de._tasks.get(ctx.session_id, set())):
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(_main())
    finally:
        events.unregister(ctx.session_id, q)


def test_a_human_approval_of_a_deferred_command_is_recorded(monkeypatch):
    sid = "audit-approved"
    de.forget_session(sid)
    work_state.clear(sid)
    rows = []
    _run_deferred(_Ctx(_gate_recording(rows), sid), "pip install requests",
                  lambda: _co("ran it"), monkeypatch, (True, False))

    assert [(r["approver"], r["approved"], r["detail"]) for r in rows] == [
        ("user", True, "decision"),   # the click, which used to leave no trace at all
        ("user", True, "executed"),   # …and the run it authorised
    ]
    assert all(r["action"] == "pip install requests" for r in rows)
    de.forget_session(sid)
    work_state.clear(sid)


def test_a_refusal_is_recorded_and_no_execution_is_claimed(monkeypatch):
    sid = "audit-declined"
    de.forget_session(sid)
    work_state.clear(sid)
    rows = []
    _run_deferred(_Ctx(_gate_recording(rows), sid), "rm -rf build",
                  lambda: _co("never"), monkeypatch, (False, False))

    assert [(r["approver"], r["approved"], r["detail"]) for r in rows] == [("user", False, "decision")]
    de.forget_session(sid)
    work_state.clear(sid)


def test_a_run_that_blew_up_is_filed_as_executed_not_as_denied(monkeypatch):
    sid = "audit-failed"
    de.forget_session(sid)
    work_state.clear(sid)
    rows = []

    async def boom():
        raise RuntimeError("nope")

    _run_deferred(_Ctx(_gate_recording(rows), sid), "pip install nope", boom, monkeypatch, (True, False))

    assert rows[-1]["detail"] == "executed:failed"
    assert rows[-1]["approved"] is True, "it was authorised; it just failed — that is not a denial"
    de.forget_session(sid)
    work_state.clear(sid)


def test_ran_nothing_covers_both_a_card_on_screen_and_a_suppressed_re_emission(monkeypatch):
    """The predicate core/loop consults before writing a row: a call that deferred, and a re-emission
    the guard refused, both executed nothing."""
    sid = "audit-nothing"
    de.forget_session(sid)
    monkeypatch.setattr(de, "request_approval", lambda s, a, **k: _co((False, False)))
    ctx = _Ctx(None, sid)

    async def _main():
        assert de.ran_nothing(ctx, "c1") is False
        de.schedule(ctx, "ls -la", lambda: _co("x"), label="ls -la")
        assert de.ran_nothing(ctx, "c1") is True          # card up, nothing ran
        assert de.ran_nothing(ctx, "other") is False
        for task in list(de._tasks.get(sid, set())):
            await asyncio.gather(task, return_exceptions=True)
        ctx.call_id = "c2"                                 # the model re-emits the same action
        assert de.schedule(ctx, "ls -la", lambda: _co("x"), label="ls -la") is not None
        assert de.ran_nothing(ctx, "c2") is True           # refused → still nothing ran

    asyncio.run(_main())
    de.forget_session(sid)
    work_state.clear(sid)


# --- through the loop -------------------------------------------------------------------------------

def _ev_call(name, args, call_id):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


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
    def __init__(self):
        self.audit = []

    async def insert_audit_log(self, **kw):
        self.audit.append(kw)


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def _register(name, execute):
    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": name}, __name__=f"tools.x.{name}",
        ANNOUNCE="", HEARTBEAT=[], COMPLETE="done", FAIL="oops", execute=execute,
    )
    reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA, toolset="file", risk="exec"))


def _turn(session, tool_name):
    q = events.register(session)
    db = _DB()
    ctx = ToolContext(db=db, session_id=session, client=None, mode="work")
    ctx.approval = None

    async def go():
        await loop._run_iterations(
            _Client([[_ev_call(tool_name, {"x": 1}, "call_1")], [_ev_text("done")]]),
            ctx, [{"role": "user", "content": "x"}], asyncio.Queue(), {},
            max_iterations=3, mode="work", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )
        return db

    db = asyncio.run(go())
    events.unregister(session, q)
    return db


def test_the_loop_writes_no_row_for_a_call_that_only_put_a_card_on_screen(clean_registry):
    async def defers(args, ctx):
        de._mark_no_execution(ctx, ctx.call_id)   # what schedule() does when it defers
        return "I asked for your permission on screen."

    _register("pretend_defer", defers)
    assert _turn("audit-loop-defer", "pretend_defer").audit == [], "nothing ran — nothing to log"


def test_the_loop_logs_a_failed_tool_as_executed_not_as_denied(clean_registry):
    async def boom(args, ctx):
        raise RuntimeError("nope")

    _register("pretend_boom", boom)
    rows = _turn("audit-loop-fail", "pretend_boom").audit
    assert len(rows) == 1
    assert rows[0]["approved"] is False and rows[0]["detail"] == "executed:failed"


def test_the_loop_logs_a_tool_that_ran(clean_registry):
    async def fine(args, ctx):
        return "ok"

    _register("pretend_ok", fine)
    rows = _turn("audit-loop-ok", "pretend_ok").audit
    assert len(rows) == 1
    assert rows[0]["approved"] is False and rows[0]["detail"] == "executed"
    assert rows[0]["approver"] == "agent-loop"   # ran on the loop's own authority, nobody was asked
