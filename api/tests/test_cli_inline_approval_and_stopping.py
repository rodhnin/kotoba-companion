"""The CLI's approval and cancellation paths, driven end to end: the real engine, event queue,
ApprovalGate and LocalSandbox, with only the Responses client faked — every card is a live Future
answered through the same chain a terminal uses.

A yes at the inline card runs the command in the SAME turn, never parked; a no is an honest
refusal (approver="user", no "executed" row, no fake success reported). `a` (always allow) is
offered only where the gate can keep the promise — `git status`, never `git status && rm foo` — and
a saved `sh` family still asks, since an interpreter grant grants anything. Ctrl+C mid-command kills
the child process tree; Ctrl+C on a waiting card clears only that card, leaving a background job's
card on the same session untouched."""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest
from conftest import posix_only, shell_that

from kotoba.core import interaction, session_sandbox, workspace
from kotoba.core import context as ctx_mod
from kotoba.core import turns as turns_mod


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
    """The fake Responses client, remembering every request: the honest-refusal assertion reads the
    function_call_output the model was actually fed back out of `calls`."""

    def __init__(self, turns):
        self.calls: list[dict] = []
        rest = list(turns)
        outer = self

        class _R:
            async def create(self, **kw):
                outer.calls.append(kw)
                return _Stream(rest.pop(0))

        self.responses = _R()


def _shell(command: str, call_id: str = "c1") -> list:
    return [_Event("response.output_item.done",
                   item=_Item(type="function_call", name="shell", call_id=call_id,
                              arguments=json.dumps({"command": command})))]


_SAID = [_Event("response.output_text.delta", delta="[warmly] Listo.")]


async def _open(monkeypatch, turns, ask):
    import kotoba.core.loop as loop
    from kotoba.cli.session import Session

    client = _Client(turns)
    monkeypatch.setattr(loop, "get_client", lambda: client)
    session = await Session.open(ask=ask)
    return session, client


def _script(answers: dict, cards: list, gates: dict | None = None):
    """A scripted terminal: records every card, waits at a gate if one is set for the label, then
    answers by label — (approved, always), like ask_at_terminal's tuple."""

    async def ask(card):
        cards.append(card)
        if gates and card.label in gates:
            await gates[card.label].wait()
        return answers.get(card.label, (False, False))

    return ask


async def _until(pred, limit: float = 10.0) -> bool:
    end = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return False


def _rows(audit: list[dict], command: str) -> list[dict]:
    """This command's trail: the gate's decision row names the command; the loop's executed row names
    the tool and its args (`shell {"command": …}`) under approver="agent-loop"."""
    return [r for r in audit if command in str(r.get("action"))]


def test_a_yes_at_the_inline_card_runs_the_command_in_the_same_turn(monkeypatch):
    cmd = shell_that("touches", marker="qa-yes-ran.txt")   # `touch` is not a PowerShell command
    cards: list = []

    async def go():
        session, client = await _open(monkeypatch, [_shell(cmd), _SAID], _script({cmd: (True, False)}, cards))
        try:
            reply = await session.ask("hazlo")
            ran = (workspace.resolve_workdir(session.session_id) / "qa-yes-ran.txt").exists()
            assert await _until(lambda: session.events.steps.get("c1") is not None
                                and session.events.steps["c1"].state != "running")
            step = session.events.steps["c1"]
            audit = await session.engine.db.fetch_audit_log()
            return reply, ran, step, audit, client.calls
        finally:
            await session.close()

    reply, ran, step, audit, calls = asyncio.run(go())
    assert ran, "the approved command never ran — or ran outside the turn's own workdir"
    assert reply.endswith("Listo."), reply  # the announce/confirm voice patterns ride in front
    assert step.state == "ok" and step.action == f"$ {cmd}"
    trail = [(r["detail"], r["approver"], r["approved"]) for r in _rows(audit, cmd)]
    assert sorted(trail) == [("decision", "user", 1), ("executed", "agent-loop", 0)], trail
    assert len(calls) == 2, "the run and the reply belong to ONE turn — no third call, nothing deferred"


def test_a_no_at_the_inline_card_is_an_honest_refusal(monkeypatch):
    """A no owes the trail one row — the user's own — and the model must be told it was a refusal.

    RE-RECORDED. "I held off … didn't get the go-ahead" was the canned line for every ending, so a
    plain no was relayed back to the user as "held back because it needs authorisation — authorise it
    again". The model is told what the audit row already knew: the USER said no, and it never ran."""
    cmd = "touch qa-refused.txt"
    cards: list = []

    async def go():
        session, client = await _open(monkeypatch, [_shell(cmd), _SAID], _script({}, cards))
        try:
            await session.ask("hazlo")
            ran = (workspace.resolve_workdir(session.session_id) / "qa-refused.txt").exists()
            assert await _until(lambda: session.events.steps.get("c1") is not None
                                and session.events.steps["c1"].state != "running")
            step = session.events.steps["c1"]
            audit = await session.engine.db.fetch_audit_log()
            return ran, step, audit, client.calls
        finally:
            await session.close()

    ran, step, audit, calls = asyncio.run(go())
    assert ran is False, "a refused command executed anyway"
    assert step.state == "refused", "the user's no must not be drawn as a failure or a success"
    mine = _rows(audit, cmd)
    assert [(r["detail"], r["approver"], r["approved"]) for r in mine] == [("decision", "user", 0)], (
        f"a refusal owes the trail ONE row, the user's own no: {mine}")
    fed_back = next(i for i in calls[1]["input"] if isinstance(i, dict)
                    and i.get("type") == "function_call_output")
    told = str(fed_back["output"])
    assert "said NO" in told and "did not happen" in told, (
        f"the model must be told the user refused, never that a go-ahead is still pending: {told!r}")
    assert "go-ahead" not in told and "held off" not in told, told


def test_always_offered_for_git_status_but_never_for_a_chained_command(monkeypatch):
    """Both cards are DECLINED — what is under test is the `a` key's availability, which the card
    carries as can_always, and that an emphatic always over a chain persists nothing."""
    plain, chained = "git status", "git status && rm foo"
    cards: list = []

    async def go():
        session, _ = await _open(
            monkeypatch, [_shell(plain), _SAID, _shell(chained, "c2"), _SAID], _script({}, cards))
        try:
            await session.ask("uno")
            await session.ask("dos")
            return await session.engine.db.list_approved_commands()
        finally:
            await session.close()

    saved = asyncio.run(go())
    assert [c.label for c in cards] == [plain, chained]
    assert cards[0].can_always is True, "a simple `git status` card must offer the family key"
    assert cards[1].can_always is False, "a chained command's first token stops naming what runs"
    assert cards[1].always_note, "a withheld key with no reason reads as a broken product"
    assert saved == [], "nothing was granted, so nothing may be stored"


def test_an_always_grant_sticks_and_the_family_stops_asking(monkeypatch):
    cards: list = []

    async def go():
        session, _ = await _open(
            monkeypatch, [_shell("sleep 0.1"), _SAID, _shell("sleep 0.2", "c2"), _SAID],
            _script({"sleep 0.1": (True, True)}, cards))
        try:
            await session.ask("uno")
            saved = await session.engine.db.list_approved_commands()
            await session.ask("dos")
            assert await _until(lambda: session.events.steps.get("c2") is not None
                                and session.events.steps["c2"].state != "running")
            return saved, session.events.steps["c2"].state
        finally:
            await session.close()

    saved, second = asyncio.run(go())
    assert {"pattern": "sleep", "scope": "command"} in [
        {"pattern": r["pattern"], "scope": r["scope"]} for r in saved]
    assert [c.label for c in cards] == ["sleep 0.1"], f"the granted family asked again: {cards}"
    assert second == "ok", "the covered command must still RUN, just without a card"


def test_a_saved_sh_family_still_asks_before_an_interpreter_runs(monkeypatch):
    cmd = "sh -c 'cat /etc/hostname'"
    cards: list = []

    async def go():
        session, _ = await _open(monkeypatch, [_shell(cmd), _SAID], _script({}, cards))
        try:
            await session.engine.db.save_approved_command("sh", "command")
            await session.ask("hazlo")
            assert await _until(lambda: session.events.steps.get("c1") is not None
                                and session.events.steps["c1"].state != "running")
            return session.events.steps["c1"].state
        finally:
            await session.close()

    state = asyncio.run(go())
    assert [c.label for c in cards] == [cmd], "a saved `sh` bought silent arbitrary execution"
    assert state == "refused"


def _orphans(token: str) -> bytes:
    return subprocess.run(["pgrep", "-f", token], capture_output=True).stdout


@posix_only("pgrep and a POSIX command line (touch ...; sleep)")
def test_ctrl_c_mid_command_kills_the_child_and_frees_the_session(monkeypatch):
    token = "sleep 86413"
    cmd = f"touch qa-cut-started; {token}"
    cards: list = []

    async def go():
        session, _ = await _open(monkeypatch, [_shell(cmd), _SAID], _script({cmd: (True, False)}, cards))
        sid = session.session_id
        marker = workspace.resolve_workdir(sid) / "qa-cut-started"
        try:
            task = asyncio.create_task(session.ask("hazlo"))
            assert await _until(marker.exists), "the approved command never started — nothing to cut"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            gone = await _until(lambda: not _orphans(token))
            cleared = await _until(lambda: not interaction.has_pending(sid))
            assert await _until(lambda: session.events.steps.get("c1") is not None
                                and session.events.steps["c1"].state == "interrupted")
            return sid, gone, cleared, turns_mod._active.get(sid)
        finally:
            await session.close()

    sid, gone, cleared, live_turn = asyncio.run(go())
    assert gone, f"an orphan survived the cut: {_orphans(token)!r}"
    assert cleared, "has_pending stayed true — the voice mic hold would jam on this session"
    assert live_turn is None, "a cancelled turn must not stay in the registry"
    assert sid not in getattr(session_sandbox, "_live", {}), "the session sandbox was never released"


def test_ctrl_c_at_the_inline_card_clears_it_and_spares_the_background_job(monkeypatch):
    """The turn is cut while ITS card waits unanswered, with a background job's card open on the same
    session (a work-mode shell blocks inline on the same asker). Cutting the turn must end only the
    turn's own card — dismissal-as-answer must not leak into the neighbour, which is answered normally
    afterwards — and the note the NEXT turn would carry names the survivor, in surface-neutral words."""
    bg_label, fg_cmd = "npm run build", "touch qa-never.txt"
    cards: list = []
    gates = {bg_label: asyncio.Event(), fg_cmd: asyncio.Event()}  # fg's gate never opens

    async def go():
        session, client = await _open(monkeypatch, [_shell(fg_cmd), _SAID],
                                      _script({bg_label: (True, False)}, cards, gates))
        sid = session.session_id
        try:
            bg = asyncio.create_task(
                interaction.request_approval(sid, bg_label, timeout=30.0, channel="text"))
            assert await _until(lambda: interaction.has_pending(sid))
            fg = asyncio.create_task(session.ask("hazlo"))
            assert await _until(lambda: len(interaction.pending_labels(sid)) == 2)
            fg.cancel()
            with pytest.raises(asyncio.CancelledError):
                await fg
            survived = await _until(lambda: interaction.pending_labels(sid) == [bg_label])
            note = ctx_mod._pending_card_note(sid)
            gates[bg_label].set()
            answer = await asyncio.wait_for(bg, 5.0)
            ran = (workspace.resolve_workdir(sid) / "qa-never.txt").exists()
            told = [i for i in client.calls[0]["input"] if isinstance(i, dict)
                    and i.get("role") == "developer" and bg_label in str(i.get("content"))]
            return survived, note, answer, ran, interaction.has_pending(sid), told
        finally:
            await session.close()

    survived, note, answer, ran, still_pending, told = asyncio.run(go())
    assert survived, "cutting the turn took the background job's card with it"
    assert answer == (True, False), "the surviving card was dismissed or refused by nobody"
    assert ran is False, "the cut turn's command ran without its approval"
    assert still_pending is False
    assert bg_label in note and "button" not in note.lower(), (
        f"the next turn's note must name the survivor in terminal-fit words: {note!r}")
    assert len(told) == 1, "a register=text turn built beside the card must carry the note — once"
    assert "button" not in str(told[0]).lower() and "screen" not in str(told[0]).lower(), told
