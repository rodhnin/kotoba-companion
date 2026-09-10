"""Regression: an approval-gated command in a turn an ELEVENLABS AGENT is holding must NOT block (EL times
the silent turn out and re-fires, orphaning the approval → "I approved and nothing happened"). It DEFERS —
shows the card, returns a short line immediately, and runs in the background once approved, announcing via
work_state. Everything else blocks inline: work mode (its detached runner is bound to no call), the CLI,
a typed web turn, and our own voice socket, where nothing cuts a turn off."""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.deferred_exec as de
import kotoba.tools.action.shell as shell
from kotoba.core import work_state
from kotoba.core.approval import ApprovalGate


@pytest.fixture(autouse=True)
def _screen_is_up(monkeypatch):
    """These model a VOICE turn with the app open. Deferral and the input card exist only to put
    something on a screen, so they are reachable only when a screen is listening — the code now checks
    that instead of promising a card nobody would see."""
    from kotoba.core import events

    monkeypatch.setattr(events, "has_listener", lambda sid: bool(sid))




class _Sandbox:
    def __init__(self):
        self.ran = []

    async def run(self, command, timeout=60):
        self.ran.append(command)
        return type("R", (), {"stdout": f"out:{command}", "stderr": "", "exit_code": 0})()


class _Ctx:
    """A minimal tool context.

    Deferral is decided by the TRANSPORT — `el_call_bound`, the mark `/v1` sets. The channel rides
    along because it still picks the approval window; the companion rows here model the ElevenLabs
    turn this file was written about, and the work runner's detached job never is one."""

    def __init__(self, gate, mode="companion", channel=None, el_call_bound=None):
        self.approval = gate
        self.mode = mode
        self.channel = channel if channel is not None else ("text" if mode == "work" else "voice")
        self.el_call_bound = (mode != "work") if el_call_bound is None else el_call_bound
        self.session_id = "vs1"
        self._sb = _Sandbox()

    async def ensure_sandbox(self):
        return self._sb


def _host_gate(tmp_path):
    return ApprovalGate(host_exec=True, workspace_root=tmp_path)


def test_voice_turn_defers_instead_of_blocking(tmp_path, monkeypatch):
    """A non-auto-safe command in a held turn returns IMMEDIATELY with a line saying permission was
    asked for, and does not run synchronously — it is scheduled for after the approval.

    The scheduled step keeps `step_kind="shell"` so the deferred result lands back on the bash row
    rather than on a generic one."""
    scheduled = {}

    def fake_schedule(ctx, action, runner, label=None, step_kind="tool"):
        scheduled["action"] = action
        scheduled["runner"] = runner
        scheduled["step_kind"] = step_kind

    monkeypatch.setattr(de, "schedule", fake_schedule)
    ctx = _Ctx(_host_gate(tmp_path))
    out = asyncio.run(shell.execute({"command": "pip install requests"}, ctx))
    assert "permission" in out.lower() and "approve" in out.lower()
    assert ctx._sb.ran == []
    assert scheduled.get("action") == "pip install requests"
    assert scheduled.get("step_kind") == "shell"


def test_auto_safe_command_still_runs_inline(tmp_path):
    """An in-workspace read is auto-safe, so it runs inline with no deferral even in a voice turn."""
    (tmp_path / "f.txt").write_text("hi")
    ctx = _Ctx(_host_gate(tmp_path))
    out = asyncio.run(shell.execute({"command": "cat f.txt"}, ctx))
    assert ctx._sb.ran == ["cat f.txt"]
    assert "exit=0" in out


def test_deferred_runs_and_announces_on_approval(tmp_path, monkeypatch):
    """The detached task, from the other side: approval granted, the runner executes, and work_state
    ends up holding a done announcement that has not been spoken yet."""
    work_state.finish("vs1", "", [])
    monkeypatch.setattr(de, "request_approval", lambda sid, action, timeout=180.0, **k: _co((True, False)))

    async def runner():
        return "Corrí `pip install requests` (salió con código 0)."

    asyncio.run(de._run_on_approval(_Ctx(_host_gate(tmp_path)), "pip install requests", runner, "pip install requests"))
    snap = work_state.get("vs1")
    assert snap["status"] == "done" and snap["announced"] is False
    assert "install requests" in snap["summary"]


def test_deferred_denied_announces_gracefully(tmp_path, monkeypatch):
    """A denial never reaches the runner, and it is announced as a decline rather than a failure.

    An answer of (False, False) — refused, with no verdict of its own — is the user declining, and she
    says so: the three ways of not running are three different sentences."""
    monkeypatch.setattr(de, "request_approval", lambda sid, action, timeout=180.0, **k: _co((False, False)))
    ran = {"n": 0}

    async def runner():
        ran["n"] += 1
        return "should not run"

    asyncio.run(de._run_on_approval(_Ctx(_host_gate(tmp_path)), "rm stuff", runner, "rm stuff"))
    assert ran["n"] == 0
    snap = work_state.get("vs1")
    assert snap["status"] == "done" and "said no" in snap["summary"]
    assert "rm stuff" in snap["summary"]


def test_work_mode_does_not_defer(tmp_path, monkeypatch):
    """In work mode the runner is bound to no held call, so it keeps blocking inline on the confirm
    path and never defers. The gate's ask is stubbed to auto-approve so that inline path completes."""
    scheduled = {"n": 0}
    monkeypatch.setattr(de, "schedule", lambda *a, **k: scheduled.__setitem__("n", scheduled["n"] + 1))
    gate = _host_gate(tmp_path)
    gate._ask = lambda action, risk, family=None: _co((True, False))
    ctx = _Ctx(gate, mode="work")
    out = asyncio.run(shell.execute({"command": "pip install x"}, ctx))
    assert scheduled["n"] == 0
    assert ctx._sb.ran == ["pip install x"]


_SPANISH = ("corrí", "ejecuté", "no pude", "listo,", "te pedí", "permiso en pantalla", "apruébalo",
            "visto bueno", "intenté", "entorno", "salió con código", "resultado:", "aviso:", "tu código")


def _no_spanish(text: str) -> bool:
    low = (text or "").lower()
    return not any(w in low for w in _SPANISH)


def test_deferred_and_defer_lines_carry_no_hardcoded_spanish(tmp_path, monkeypatch):
    """The soul's language is `auto`, so a Spanish literal baked into a code path leaks into an
    English conversation. All five strings on this path are covered: the spoken defer line, the
    deferred command's own report, the denied summary, the failed summary and the canned done line a
    falsy summary falls back to. `_SPANISH` is the word list those lines are checked against."""
    captured = {}
    monkeypatch.setattr(de, "schedule", lambda ctx, action, runner, **kw: captured.update(runner=runner))
    ctx = _Ctx(_host_gate(tmp_path))
    defer_line = asyncio.run(shell.execute({"command": "pip install requests"}, ctx))
    assert _no_spanish(defer_line), defer_line
    assert _no_spanish(asyncio.run(captured["runner"]()))

    monkeypatch.setattr(de, "request_approval", lambda sid, action, timeout=180.0, **k: _co((False, False)))
    asyncio.run(de._run_on_approval(ctx, "rm stuff", lambda: _co("x"), "rm stuff"))
    assert _no_spanish(work_state.get("vs1")["summary"])

    monkeypatch.setattr(de, "request_approval", lambda sid, action, timeout=180.0, **k: _co((True, False)))

    async def _boom():
        raise RuntimeError("nope")

    asyncio.run(de._run_on_approval(ctx, "rm stuff", _boom, "rm stuff"))
    assert _no_spanish(work_state.get("vs1")["summary"])

    async def _empty():
        return ""

    asyncio.run(de._run_on_approval(ctx, "ls", _empty, "ls"))
    assert _no_spanish(work_state.get("vs1")["summary"])


def test_execute_code_defer_line_is_english(tmp_path, monkeypatch):
    """The same language rule for execute_code: neither its defer line nor the card label is Spanish."""
    import kotoba.tools.action.execute_code as ec

    labels = {}
    monkeypatch.setattr(de, "schedule", lambda ctx, action, runner, label=None, **kw: labels.update(label=label))
    out = asyncio.run(ec.execute({"code": "import shutil; shutil.rmtree('/')"}, _Ctx(_host_gate(tmp_path))))
    assert _no_spanish(out), out
    assert _no_spanish(labels["label"])


def _co(value):
    async def _c(*a, **k):
        return value
    return _c()
