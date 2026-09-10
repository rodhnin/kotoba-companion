"""You approved it, and then nothing happened — and every witness said it had run.

The deferred path reads one thing from the runner it calls: whether it raised. Both exec tools handed
it a runner that, when the sandbox never came up, RETURNED "I couldn't get the environment ready to run
it." Prose is not a channel, so the approval was filed as executed and the model was told it had
already run.

The audit trail is the only place that answers "did this reach my machine", so executed there has to
mean it did. The mark is `failed`, never `refused`: nobody refused it, and a row that reads as the
user's own No would hide a broken sandbox behind a decision.
"""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.deferred_exec as de
from kotoba.core import events, work_state
from kotoba.core.approval import ApprovalGate


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


def _gate(rows):
    async def audit(action, risk, ok, who, detail):
        rows.append({"action": action, "approved": ok, "approver": who, "detail": detail})

    return ApprovalGate(host_exec=True, audit=audit)


def _drive(sid, action, runner, monkeypatch, answer=(True, False)):
    """Approve the card and let the deferred task finish, collecting the audit rows and the step frames
    the surfaces would have drawn."""
    rows: list[dict] = []
    steps: list[dict] = []
    de.forget_session(sid)
    work_state.clear(sid)
    monkeypatch.setattr(de, "request_approval", lambda s, a, **k: _co(answer))

    async def spy(session_id, kind, **frame):
        if kind == "step" and frame.get("phase") == "done":
            steps.append(frame)

    monkeypatch.setattr(events, "emit_task", spy)
    q = events.register(sid)

    async def _main():
        de.schedule(_Ctx(_gate(rows), sid), action, runner, label=action, step_kind="shell")
        await asyncio.sleep(0)
        for task in list(de._tasks.get(sid, set())):
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(_main())
    finally:
        events.unregister(sid, q)
    return rows, steps


@pytest.fixture(autouse=True)
def _clean():
    yield
    for sid in ("never-ran", "ran-fine", "blew-up"):
        de.forget_session(sid)
        work_state.clear(sid)


def _nothing_ran():
    async def runner():
        raise de.NothingRan("I couldn't get the environment ready to run it.")
    return runner


# --- the audit trail ---------------------------------------------------------------------------------

def test_the_trail_keeps_the_approval_and_claims_no_execution(monkeypatch):
    rows, _ = _drive("never-ran", "pip install requests", _nothing_ran(), monkeypatch)

    assert [(r["approver"], r["approved"], r["detail"]) for r in rows] == [("user", True, "decision")], \
        "they did approve it, and nothing ran — one event, one row"
    assert not any(r["detail"].startswith("executed") for r in rows)


def test_a_command_that_really_ran_still_gets_its_executed_row(monkeypatch):
    """The guard has to leave the working case alone, or it is just a way of never recording anything."""
    rows, _ = _drive("ran-fine", "pip install requests", lambda: _co("ran it"), monkeypatch)
    assert [r["detail"] for r in rows] == ["decision", "executed"]


def test_a_run_that_started_and_blew_up_is_still_filed_as_executed(monkeypatch):
    """A process that spawned and died left traces on the host. Only the never-started case is silent."""
    async def boom():
        raise RuntimeError("the process died")

    rows, _ = _drive("blew-up", "pip install nope", boom, monkeypatch)
    assert rows[-1]["detail"] == "executed:failed"
    assert rows[-1]["approved"] is True


# --- the mark the surfaces draw ----------------------------------------------------------------------

def test_the_row_is_marked_failed_not_ok(monkeypatch):
    _, steps = _drive("never-ran", "pip install requests", _nothing_ran(), monkeypatch)
    assert steps, "the row this action opened was never completed"
    assert steps[-1]["outcome"] == "failed"
    assert steps[-1]["ok"] is False


def test_the_mark_is_one_the_surfaces_already_know(monkeypatch):
    """Both readers hold a closed list and draw anything else as `unknown`. A sixth word invented here
    would reach the screen as a mark nobody chose."""
    from kotoba.cli.events_bridge import OUTCOMES

    _, steps = _drive("never-ran", "pip install requests", _nothing_ran(), monkeypatch)
    assert steps[-1]["outcome"] in OUTCOMES


def test_it_is_not_dressed_as_the_users_refusal(monkeypatch):
    """`refused` means somebody said no. Wearing it here would file a broken sandbox as a decision."""
    _, steps = _drive("never-ran", "pip install requests", _nothing_ran(), monkeypatch)
    assert steps[-1]["outcome"] != "refused"
    assert steps[-1]["interrupted"] is False


# --- what she is left holding ------------------------------------------------------------------------

def test_the_model_is_told_nothing_ran_rather_than_handed_a_result(monkeypatch):
    """The settle note is what a re-emission reads. It used to open with "You ALREADY ran this"."""
    _drive("never-ran", "pip install requests", _nothing_ran(), monkeypatch)
    note = (de._settled.get("never-ran") or {}).get(
        next(iter(de._settled.get("never-ran", {})), ""), "")
    assert "NOTHING ran" in note
    assert "ALREADY ran this" not in note
    assert "environment ready" in note, "the reason has to survive into what she says"


def test_the_work_is_left_failed_with_a_reason(monkeypatch):
    _drive("never-ran", "pip install requests", _nothing_ran(), monkeypatch)
    state = work_state.get("never-ran")
    assert state["status"] == "failed"
    assert "environment ready" in state["reason"]


def test_a_runner_with_nothing_to_say_still_names_the_action(monkeypatch):
    async def mute():
        raise de.NothingRan("")

    _, steps = _drive("never-ran", "pip install requests", mute, monkeypatch)
    assert "pip install requests" in steps[-1]["result"]


# --- the two runners that raise it -------------------------------------------------------------------

def test_both_deferred_runners_raise_instead_of_returning_the_sentence():
    """The defect was one `return` in each file, and a third caller would reintroduce it. Reading the
    source is the only oracle here: the branch needs a sandbox that refuses to come up, which no
    fixture can produce without becoming a re-implementation of the tool."""
    import pathlib

    import kotoba.tools.action.execute_code as ec
    import kotoba.tools.action.shell as sh

    for mod in (sh, ec):
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        head, _, tail = src.partition("if sb is None:")
        assert tail, f"{mod.__name__} no longer guards a missing sandbox"
        first_line = tail.strip().splitlines()[0] if not tail.strip().startswith("#") else \
            [ln.strip() for ln in tail.strip().splitlines() if not ln.strip().startswith("#")][0]
        assert first_line.startswith("raise deferred_exec.NothingRan"), \
            f"{mod.__name__} reports a dead sandbox by returning prose again: {first_line!r}"
