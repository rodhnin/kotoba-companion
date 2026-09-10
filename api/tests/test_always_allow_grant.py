"""Live QA found that "always allow" on a CODE card had never once worked.

core/deferred_exec called `persist_always(action)` with no family, so command_family("run Python:
print(…)") stored the junk first token "run" — while execute_code gates on family="execute_code". The
two keys can never meet, so the button did nothing, ever, and approved_commands stayed empty.

The grant now lands under the key the tool actually gates on. Because "always allow ALL Python on this
machine" is a far broader promise than "always allow npm", the card is also told which family it is
about (and told to hide the button when nothing would be persisted at all).
"""
from __future__ import annotations

import pytest

import asyncio

import kotoba.core.deferred_exec as de
import kotoba.tools.action.execute_code as ec
import kotoba.tools.action.shell as shell
from kotoba.core import events, interaction, work_state
from kotoba.core.approval import ApprovalGate, command_family


@pytest.fixture(autouse=True)
def _screen_is_up(monkeypatch):
    """These model an ElevenLabs turn with the app open. Deferral and the input card exist only to put
    something on a screen, so they are reachable only when a screen is listening — the code now checks
    that instead of promising a card nobody would see."""
    from kotoba.core import events

    monkeypatch.setattr(events, "has_listener", lambda sid: bool(sid))




class _Sandbox:
    def __init__(self):
        self.code, self.ran = [], []

    async def run_code(self, code, timeout=60):
        self.code.append(code)
        return type("R", (), {"stdout": "42", "stderr": "", "exit_code": 0})()

    async def run(self, command, timeout=60):
        self.ran.append(command)
        return type("R", (), {"stdout": "ok", "stderr": "", "exit_code": 0})()


class _Ctx:
    def __init__(self, gate, session_id):
        self.approval = gate
        self.mode = "companion"
        # The card path these tests are about is reached only when an ElevenLabs agent holds the turn.
        self.channel = "voice"
        self.el_call_bound = True
        self.session_id = session_id
        self.call_id = "c1"
        self.user_text = "add up the first hundred numbers"
        self._open_steps = {}
        self._sb = _Sandbox()

    async def ensure_sandbox(self):
        return self._sb


def _co(value):
    async def _c(*a, **k):
        return value
    return _c()


def _drain(sid):
    async def _wait():
        for task in list(de._tasks.get(sid, set())):
            await asyncio.gather(task, return_exceptions=True)
    return _wait()


def test_always_allow_on_a_code_card_saves_the_family_execute_code_gates_on(tmp_path, monkeypatch):
    """Answering the card with "always allow" must persist the family `execute_code` gates on.

    The old call persisted `command_family(action)` instead, which for a code action is the junk
    first token "run" — asserted below so the two keys can be seen not to meet."""
    sid = "grant-code"
    de.forget_session(sid)
    work_state.clear(sid)
    saved = []

    async def persist(family):
        saved.append(family)

    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, on_persist=persist)
    ctx = _Ctx(gate, sid)
    monkeypatch.setattr(de, "request_approval", lambda s, a, **k: _co((True, True)))  # approved, "always allow" ticked

    async def _main():
        out = await ec.execute({"code": "print(sum(range(1,101)))"}, ctx)
        assert "permission" in out.lower()          # deferred: the card went up, nothing ran inline
        await _drain(sid)

    asyncio.run(_main())

    assert saved == ["execute_code"], saved
    assert gate.is_saved("execute_code")
    assert command_family("run Python: print(sum(range(1,101)))") == "run"
    assert "run" not in saved
    de.forget_session(sid)
    work_state.clear(sid)


def test_the_saved_grant_matches_on_the_second_attempt(tmp_path, monkeypatch):
    """What the grant is FOR: the next snippet runs with no card at all. Before the fix the saved
    pattern was "run", the gate looked up "execute_code", and the user was asked again forever."""
    sid = "grant-code-2"
    de.forget_session(sid)
    cards = []
    monkeypatch.setattr(de, "schedule", lambda *a, **k: cards.append(k) or None)

    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={"execute_code"})
    ctx = _Ctx(gate, sid)
    out = asyncio.run(ec.execute({"code": "print(6 * 7)"}, ctx))

    assert cards == []
    assert ctx._sb.code == ["print(6 * 7)"]
    assert "exit=0" in out

    # …and the old junk key still grants nothing, so a stale "run" row can't authorise code.
    stale = _Ctx(ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={"run"}), sid)
    asyncio.run(ec.execute({"code": "print(6 * 7)"}, stale))
    assert stale._sb.code == [] and len(cards) == 1
    de.forget_session(sid)


def test_a_dangerous_snippet_still_asks_under_the_saved_grant(tmp_path, monkeypatch):
    """The grant covers "running Python", not "running anything": destructive/exec/eval code re-prompts."""
    sid = "grant-code-3"
    de.forget_session(sid)
    cards = []
    monkeypatch.setattr(de, "schedule", lambda *a, **k: cards.append(k) or None)

    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={"execute_code"})
    ctx = _Ctx(gate, sid)
    asyncio.run(ec.execute({"code": "import shutil; shutil.rmtree('/tmp/x')"}, ctx))

    assert len(cards) == 1 and cards[0]["family"] == "execute_code"
    assert ctx._sb.code == []
    de.forget_session(sid)


def test_a_shell_card_still_saves_its_own_first_token(tmp_path, monkeypatch):
    """Regression: shell's action IS the command, so its family stays the command name — a grant for
    `npm` must not become a grant for everything."""
    sid = "grant-shell"
    de.forget_session(sid)
    work_state.clear(sid)
    saved = []

    async def persist(family):
        saved.append(family)

    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, on_persist=persist)
    ctx = _Ctx(gate, sid)
    monkeypatch.setattr(de, "request_approval", lambda s, a, **k: _co((True, True)))

    async def _main():
        await shell.execute({"command": "npm run build"}, ctx)
        await _drain(sid)

    asyncio.run(_main())
    assert saved == ["npm"]
    de.forget_session(sid)
    work_state.clear(sid)


def test_the_card_is_told_what_always_allow_would_really_grant():
    """The button used to read "Always allow this" beside one printed line — while saving a whole family.
    The frame now names the family so the UI can say it, and clears can_always when nothing persists."""
    async def go(action, **kw):
        q = events.register("card-scope")
        await interaction.request_approval("card-scope", action, timeout=0.05, **kw)
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("card-scope")
        return next(f for f in frames if f.get("kind") == "need_input" and f.get("mode") == "approval")

    code = asyncio.run(go("run Python: print(42)", family="execute_code"))
    assert code["family"] == "execute_code" and code["can_always"] is True

    cmd = asyncio.run(go("npm run build"))
    assert cmd["family"] == "npm" and cmd["can_always"] is True

    # A dangerous command is never persisted by the gate → don't offer a button that does nothing.
    danger = asyncio.run(go("rm -rf /"))
    assert danger["can_always"] is False
    # Nor do the MCP paths, which ignore `always` entirely.
    mcp = asyncio.run(go("Install the “notion” MCP server and connect it?", family=""))
    assert mcp["can_always"] is False
