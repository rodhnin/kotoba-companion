"""ONE approval, THREE executions — a live defect, and the invariant that closes it.

Live trace: one spoken request produced three `execute_code` calls with cosmetically
different arguments (timeout, whitespace), each opening its own approval card. History
stores only user/assistant TEXT, never the tool call or its result, so the model re-derived
the same request from the `__work_done__` announce turns and re-emitted it — past the
byte-exact per-turn `attempts` guard in `core.loop`.

The invariant: one user request gets ONE card and runs the gated action at most ONCE, no
matter how many times the model re-emits it."""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.deferred_exec as de
import kotoba.tools.action.execute_code as ec
import kotoba.tools.action.shell as sh
from kotoba.core import interaction, work_state
from kotoba.core.approval import ApprovalGate

SID = "reemit-1"
REQUEST = "Ejecuta un script de Python que sume los numeros del 1 al 100 y dime el total."


class _Sandbox:
    def __init__(self):
        self.code_runs: list[str] = []
        self.cmd_runs: list[str] = []

    async def run_code(self, code, timeout=60):
        self.code_runs.append(code)
        return type("R", (), {"stdout": "5050", "stderr": "", "exit_code": 0})()

    async def run(self, command, timeout=60):
        self.cmd_runs.append(command)
        return type("R", (), {"stdout": "ok", "stderr": "", "exit_code": 0})()


class _Ctx:
    """The fields core.loop sets on a real companion turn. The live trace above came through `/v1`, so
    the transport mark is on — that is what makes these calls defer rather than block."""

    def __init__(self, gate, user_text=REQUEST, session_id=SID):
        self.approval = gate
        self.mode = "companion"
        self.channel = "voice"
        self.el_call_bound = True
        self.session_id = session_id
        self.user_text = user_text
        self.call_id = ""
        self._open_steps: dict = {}
        self._sb = _Sandbox()

    async def ensure_sandbox(self):
        return self._sb


@pytest.fixture(autouse=True)
def _clean():
    de.forget_session(SID)
    de.forget_session("reemit-2")
    work_state.clear(SID)
    yield
    de.forget_session(SID)
    de.forget_session("reemit-2")
    work_state.clear(SID)


def _gate(tmp_path):
    return ApprovalGate(host_exec=True, workspace_root=tmp_path)


async def _settle(session_id=SID):
    """Let every detached deferred task for this session finish."""
    for _ in range(4):
        await asyncio.sleep(0)
    for task in list(de._tasks.get(session_id, set())):
        await asyncio.gather(task, return_exceptions=True)


def _with_channel(session_id=SID):
    """deferred_exec only ever runs on the voice channel, where an SSE queue exists — and
    request_approval now refuses to open a card into a session nobody is listening to."""
    from kotoba.core import events
    return events.register(session_id)


def _cards_open(session_id=SID) -> int:
    return len([f for f in (interaction._pending.get(session_id) or {}).values() if not f.done()])


def test_one_approval_one_execution(tmp_path):
    """The exact live sequence: three re-emissions of the same intent, approved exactly once.

    The first call opens the card; the two that follow are the `__work_done__` announce turns
    re-deriving the same request, with the whitespace and the timeout differing exactly as they did
    live — so no byte-exact signature could catch them. One approval buys one execution, nothing is
    left waiting to be approved a second time, and the re-emissions are told the truth rather than
    quietly opening another card."""
    ctx = _Ctx(_gate(tmp_path))

    async def _main():
        _with_channel()
        first = await ec.execute({"code": "print(sum(range(1,101)))", "timeout": 60}, ctx)
        second = await ec.execute({"code": "print(sum(range(1, 101)))", "timeout": 60}, ctx)
        third = await ec.execute({"code": "print(sum(range(1, 101)))", "timeout": 30}, ctx)
        await asyncio.sleep(0)

        assert _cards_open() == 1, "a re-emission must not stack a second approval card"

        assert interaction.resolve(SID, {"approved": True, "always": False}) is True
        await _settle()
        return first, second, third

    first, second, third = asyncio.run(_main())

    assert ctx._sb.code_runs == ["print(sum(range(1,101)))"], ctx._sb.code_runs
    assert _cards_open() == 0
    assert "approve" in first.lower()
    for again in (second, third):
        assert "already" in again.lower(), again


def test_the_run_result_is_handed_back_to_a_later_re_emission(tmp_path):
    """Turn 3 re-emitted AFTER the code had already run. It must get the real result back — that is
    what lets the answer be "five thousand and fifty" instead of a third request for approval."""
    ctx = _Ctx(_gate(tmp_path))

    async def _main():
        _with_channel()
        await ec.execute({"code": "print(sum(range(1,101)))"}, ctx)
        await asyncio.sleep(0)
        interaction.resolve(SID, {"approved": True, "always": False})
        await _settle()
        return await ec.execute({"code": "print(sum(range(1, 101)))"}, ctx)

    after = asyncio.run(_main())
    assert ctx._sb.code_runs == ["print(sum(range(1,101)))"]
    assert "5050" in after, after
    assert "already" in after.lower()


def test_a_new_user_request_may_run_the_same_thing_again(tmp_path):
    """The guard is scoped to ONE user request — "run it again" must still run."""
    ctx = _Ctx(_gate(tmp_path))

    async def _main():
        _with_channel()
        await ec.execute({"code": "print(sum(range(1,101)))"}, ctx)
        await asyncio.sleep(0)
        interaction.resolve(SID, {"approved": True, "always": False})
        await _settle()
        ctx.user_text = "Vuelve a correrlo, por favor."      # a NEW user turn: "run it again, please"
        await ec.execute({"code": "print(sum(range(1,101)))"}, ctx)
        await asyncio.sleep(0)
        assert _cards_open() == 1, "a fresh request must be able to ask again"
        interaction.resolve(SID, {"approved": True, "always": False})
        await _settle()

    asyncio.run(_main())
    assert len(ctx._sb.code_runs) == 2


def test_a_declined_action_is_not_re_carded_for_the_same_request(tmp_path):
    """Saying No must not turn into a nag loop: the re-emission is told it was declined, not re-asked."""
    ctx = _Ctx(_gate(tmp_path))

    async def _main():
        _with_channel()
        await sh.execute({"command": "rm -rf build"}, ctx)
        await asyncio.sleep(0)
        interaction.resolve(SID, {"approved": False, "always": False})
        await _settle()
        again = await sh.execute({"command": "rm -rf  build"}, ctx)
        await asyncio.sleep(0)
        return again

    again = asyncio.run(_main())
    assert ctx._sb.cmd_runs == []
    assert _cards_open() == 0, "a declined action must not re-open the card"
    assert "said no" in again.lower() and "did not happen" in again.lower(), again
    assert "do not ask again" in again.lower(), again


def test_two_different_commands_in_one_request_each_get_a_card(tmp_path):
    """The guard keys on the ACTION, not just the request — a genuine multi-step still asks for both."""
    ctx = _Ctx(_gate(tmp_path), user_text="Crea un venv e instala requests")

    async def _main():
        _with_channel()
        await sh.execute({"command": "python -m venv .venv"}, ctx)
        await sh.execute({"command": "pip install requests"}, ctx)
        await asyncio.sleep(0)
        return _cards_open()

    assert asyncio.run(_main()) == 2
    de.cancel(SID)


# --- the same invariant, driven through the real agentic loop across two turns ----------------------

import json
import types

import kotoba.core.loop as loop
from kotoba.core import events, transport
from kotoba.tools import ToolContext


def _ev_call(name, args, call_id):
    item = types.SimpleNamespace(
        type="function_call", name=name, arguments=json.dumps(args), call_id=call_id
    )
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

    async def list_approved_commands(self):
        return []

    async def save_approved_command(self, *a, **k):
        return None


def test_agentic_loop_reemission_across_turns_runs_once(tmp_path, monkeypatch):
    """End to end through `agentic_loop`: the model emits execute_code on turn 1 (card up), the user
    approves ONCE, and the `__work_done__` announce turn re-emits the same snippet respelled. The code
    must run once.

    Every assertion runs while the loop is LIVE: `asyncio.run()`'s shutdown cancels the detached
    deferred task, which drains `core.interaction._pending`, so checking cards afterwards proves
    nothing. The emission counter is the honest proof that turn 2 really re-emitted the call — its
    audit row cannot be, because an emission that never runs no longer writes one."""
    sid = "reemit-loop"
    de.forget_session(sid)
    work_state.clear(sid)
    sandbox = _Sandbox()

    monkeypatch.setenv("KOTOBA_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr("kotoba.core.sandbox.backend_name", lambda: "local")
    monkeypatch.setattr("kotoba.core.sandbox.sandbox_available_sync", lambda: True)
    monkeypatch.setattr(ToolContext, "ensure_sandbox", lambda self: _done_co(sandbox))
    monkeypatch.setattr(loop, "model_name", lambda *a, **k: "test-model")
    monkeypatch.setattr(loop, "model_call_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(loop, "is_reasoning_model", lambda *a, **k: False)
    monkeypatch.setattr("kotoba.tools.registry._check_cache", {})

    request = [{"role": "user", "content": REQUEST}]
    turn1 = [[_ev_call("execute_code", {"code": "print(sum(range(1,101)))", "timeout": 60}, "c1")],
             [_ev_text("Solo aprueba la ejecución en pantalla.")]]
    # the announce turn: same request in history, snippet respelled exactly as it was live
    turn2 = [[_ev_call("execute_code", {"code": "print(sum(range(1, 101)))", "timeout": 30}, "c2")],
             [_ev_text("El total es cinco mil cincuenta.")]]

    emissions = {"n": 0}   # counts every time the tool reached the deferral gate
    _real_schedule = de.schedule
    monkeypatch.setattr(de, "schedule",
                        lambda *a, **k: (emissions.__setitem__("n", emissions["n"] + 1), _real_schedule(*a, **k))[1])

    async def _main():
        _with_channel()
        events.register(sid)
        db = _DB()
        approvals = 0

        monkeypatch.setattr(loop, "get_client", lambda: _Client(turn1))
        # The live trace came through /v1, and only there does the turn hand back with the card still
        # up — every other transport blocks on it inline.
        with transport.el_call_turn():
            await loop.agentic_loop(list(request), sid, db, asyncio.Queue(), {}, mode="companion")
        await asyncio.sleep(0)
        assert _cards_open(sid) == 1, "turn 1 puts exactly one card on screen"

        assert interaction.resolve(sid, {"approved": True, "always": False}) is True
        approvals += 1
        await _settle(sid)
        assert sandbox.code_runs == ["print(sum(range(1,101)))"]

        # the announce turn re-emits the same snippet, respelled
        monkeypatch.setattr(loop, "get_client", lambda: _Client(turn2))
        with transport.el_call_turn():
            await loop.agentic_loop(list(request), sid, db, asyncio.Queue(), {}, mode="companion")
        await asyncio.sleep(0)
        assert emissions["n"] == 2, "the model really did re-emit the call on turn 2"
        # the trail: one human decision, one run, nothing for the re-emission
        assert [(r["approver"], r.get("detail")) for r in db.audit] == [
            ("user", "decision"), ("user", "executed"),
        ], db.audit

        # the user keeps answering anything that appears, as they did live
        while _cards_open(sid):
            interaction.resolve(sid, {"approved": True, "always": False})
            approvals += 1
            await _settle(sid)

        events.unregister(sid)
        return approvals

    approvals = asyncio.run(_main())

    assert approvals == 1, f"the re-emission put another card on screen ({approvals} approvals)"
    assert sandbox.code_runs == ["print(sum(range(1,101)))"], sandbox.code_runs
    de.forget_session(sid)
    work_state.clear(sid)


def _done_co(value):
    async def _c(*a, **k):
        return value
    return _c()
