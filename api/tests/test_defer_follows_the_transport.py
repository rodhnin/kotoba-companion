"""An approval blocks inline unless an ElevenLabs agent is holding the turn's clock open.

Deferring to a card and ending the turn is worse UX: the person hears nothing until a
separate announce turn. It exists for one reason — ElevenLabs times a silent turn out and
re-fires it, orphaning the approval. So the tools must ask about the transport, not the channel.

`channel` cannot answer it: our own voice socket is `channel="voice"` with nothing timing it
out, and a detached work job passes `channel="text"` even when born inside an ElevenLabs call.
The transport mark lines up instead — only the `/v1` producer knows an agent is listening."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import kotoba.core.deferred_exec as de
import kotoba.core.loop as loop
import kotoba.core.work_runner as wr
import kotoba.server as main
import kotoba.tools.action.execute_code as ec
import kotoba.tools.action.shell as shell
from kotoba.core import events, transport, work_state
from kotoba.core.approval import ApprovalGate
from kotoba.tools import ToolContext

_AUTH = {"Authorization": "Bearer k"}
_COMMAND = "pip install requests"


class _Item:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Event:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for event in self._evs:
                yield event
        return gen()


class _Client:
    """A scripted Responses client: one `shell` call, then a spoken line."""

    def __init__(self):
        rest = [
            [_Event("response.output_item.done",
                    item=_Item(type="function_call", name="shell", call_id="c1",
                               arguments=json.dumps({"command": _COMMAND})))],
            [_Event("response.output_text.delta", delta="Hecho.")],
        ]

        class _R:
            async def create(self, **kw):
                return _Stream(rest.pop(0) if rest else [])
        self.responses = _R()


class _Sandbox:
    def __init__(self):
        self.ran: list[str] = []

    async def run(self, command, timeout=60):
        self.ran.append(command)
        return type("R", (), {"stdout": "", "stderr": "", "exit_code": 0})()

    async def run_code(self, code, timeout=60):
        self.ran.append(code)
        return type("R", (), {"stdout": "", "stderr": "", "exit_code": 0})()


class _Ctx:
    """What core.loop puts on a ToolContext, with the transport stated rather than inferred."""

    def __init__(self, gate, channel: str, el_call_bound: bool, session_id="transport-probe"):
        self.approval, self.channel, self.el_call_bound = gate, channel, el_call_bound
        self.session_id, self.mode, self.call_id = session_id, "companion", "c1"
        self._sb = _Sandbox()

    async def ensure_sandbox(self):
        return self._sb


def _gate(tmp_path, answer=(True, False)):
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path)

    async def _ask(action, risk, family=None):
        return answer
    gate._ask = _ask
    return gate


@pytest.fixture(autouse=True)
def _a_screen_is_listening(monkeypatch):
    """Deferring needs somewhere to draw the card; that half is not what these tests vary."""
    monkeypatch.setattr(events, "has_listener", lambda sid: bool(sid))


# ---- the invariant: the ElevenLabs path is untouched -----------------------------------------------


@pytest.fixture
def v1_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "defer.db"))
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("KOTOBA_SANDBOX", "local")
    with TestClient(main.app) as c:
        yield c


def test_a_real_v1_turn_still_defers_its_approval(v1_client, monkeypatch):
    """The whole route, not the predicate: an ElevenLabs agent posts a turn, the model calls `shell`
    with a command the gate will not wave through, and the tool must hand the turn back at once with
    the card left on screen."""
    scheduled: list = []
    monkeypatch.setattr(loop, "get_client", lambda: _Client())
    monkeypatch.setattr(de, "schedule", lambda ctx, action, runner, **kw: scheduled.append(action))

    body = v1_client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": [{"role": "user", "content": "instala requests"}],
        "session_id": "defer-v1", "stream": True,
    })

    assert body.status_code == 200
    assert scheduled == [_COMMAND], (
        "the ElevenLabs path stopped deferring — a silent turn there is re-fired and the approval is "
        f"orphaned: {scheduled}")


def test_the_mark_alone_decides_on_the_same_channel(tmp_path):
    """Two turns identical but for the transport. Under the mark the command is carded and nothing
    runs; without it the same command on the same channel blocks on the gate and runs."""
    with transport.el_call_turn():
        el = ToolContext(db=None, session_id="same-channel", channel="voice",
                         approval=_gate(tmp_path))
    ours = ToolContext(db=None, session_id="same-channel", channel="voice",
                       approval=_gate(tmp_path))
    sb = _Sandbox()
    for ctx in (el, ours):
        ctx.ensure_sandbox = lambda _sb=sb: _done(_sb)

    scheduled: list = []
    real_schedule = de.schedule
    de.schedule = lambda ctx, action, runner, **kw: scheduled.append(action)
    try:
        el_line = asyncio.run(shell.execute({"command": _COMMAND}, el))
        ours_line = asyncio.run(shell.execute({"command": _COMMAND}, ours))
    finally:
        de.schedule = real_schedule

    assert el.el_call_bound is True and ours.el_call_bound is False
    assert scheduled == [_COMMAND], "the marked turn did not defer"
    assert "permission" in el_line.lower()
    assert sb.ran == [_COMMAND], "the unmarked voice turn never ran the command it approved inline"
    assert "exit=0" in ours_line


def _done(value):
    async def _c():
        return value
    return _c()


# ---- the four entry points ------------------------------------------------------------------------


@pytest.mark.parametrize("channel,el_bound,defers", [
    ("voice", True, True),    # /v1 — an ElevenLabs agent is holding the call open
    ("voice", False, False),  # our own voice WebSocket — nothing cuts this turn
    ("text", False, False),   # a typed web turn, and the CLI
])
@pytest.mark.parametrize("tool,args", [
    (shell, {"command": _COMMAND}),
    (ec, {"code": "import shutil; shutil.rmtree('/x')"}),
])
def test_each_entry_point_routes_by_its_transport(tmp_path, channel, el_bound, defers, tool, args):
    scheduled: list = []
    real_schedule = de.schedule
    de.schedule = lambda ctx, action, runner, **kw: scheduled.append(action)
    try:
        ctx = _Ctx(_gate(tmp_path), channel, el_bound)
        asyncio.run(tool.execute(args, ctx))
    finally:
        de.schedule = real_schedule

    assert bool(scheduled) is defers, (
        f"channel={channel} el_call_bound={el_bound} took the wrong branch")
    assert bool(ctx._sb.ran) is not defers, "a deferred action ran inline, or an inline one did not run"


def test_a_context_that_never_said_blocks_inline(tmp_path):
    """A hand-built double answers None. It reads the same as False, which is what a real ToolContext
    with nothing set already answers: nothing may be relaxed for it."""
    from kotoba.core.interaction import el_agent_turn

    class _Bare(_Ctx):
        def __init__(self, gate):
            super().__init__(gate, "voice", False)
            del self.el_call_bound

    ctx = _Bare(_gate(tmp_path))
    assert el_agent_turn(ctx) is None
    scheduled: list = []
    real_schedule = de.schedule
    de.schedule = lambda *a, **k: scheduled.append(a)
    try:
        asyncio.run(shell.execute({"command": _COMMAND}, ctx))
    finally:
        de.schedule = real_schedule
    assert scheduled == [] and ctx._sb.ran == [_COMMAND]


# ---- the detached job: it inherits the mark, and must not keep it ----------------------------------


def test_a_background_job_sheds_the_mark_but_keeps_the_ceiling(monkeypatch):
    """`asyncio.create_task` copies the context, so a job launched inside an ElevenLabs turn starts
    life marked — and would card every gated command for the next half hour, in a job nothing is
    listening to and nothing is timing out. It sheds the mark. What the transport genuinely still
    decides, the compute ceiling, travels as an argument and is unaffected."""
    seen: dict = {}

    async def fake_budget(coro, scope, budget, interactive_seconds_fn):
        coro.close()
        seen["budget"] = budget
        seen["inside"] = transport.el_call_bound()
        seen["ctx"] = ToolContext(db=None, session_id="shed", mode="work",
                                  channel="text").el_call_bound
        return "done"

    async def fake_emit(session_id, kind, **data):
        pass

    monkeypatch.setattr(wr, "_run_with_compute_budget", fake_budget)
    monkeypatch.setattr(wr, "emit_task", fake_emit)
    monkeypatch.delenv("KOTOBA_WORK_TIMEOUT", raising=False)

    async def go():
        with transport.el_call_turn():
            wr.start("shed", "build the thing", None, {}, None)
        await work_state.pop_task("shed")

    asyncio.run(go())

    assert seen["inside"] is False, "the detached job kept a clock that had already stopped"
    assert seen["ctx"] is False
    assert seen["budget"] == transport.WORK_TIMEOUT_EL_BOUND_SECONDS, (
        "shedding the mark must not move the ceiling — that one is captured at start()")
    assert work_state.get("shed")["el_call_bound"] is True, (
        "the job's own record of the transport it was born under is the reader's answer")
