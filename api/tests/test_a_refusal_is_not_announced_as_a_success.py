"""A refusal announced with `ok=True` looks like a defect — "the work succeeded" over a command that
never ran — but the reading is wrong once traced to its one live consumer: the web client relays
`summary` when `ok` is True and replaces it with "(Background work failed.)" when False, dropping the
summary entirely. "Did it run" never travels on this flag; it travels on the step row's `outcome` and
in the sentence itself.

Flipping the flag would voice the user's own No back as a failure and throw away the sentence that
says what happened — the exact lie the outcome vocabulary forbids (declining is not the action going
wrong). These tests pin the honest contract: the sentence must say nothing ran, and the flag must
stay True so the web client actually relays that sentence."""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.deferred_exec as de
from kotoba.core import work_state
from kotoba.core.interaction import UNANSWERED, UNREACHABLE


class _Ctx:
    def __init__(self, session_id):
        self.approval = None
        self.mode = "companion"
        self.session_id = session_id
        self.call_id = "call_ref"
        self.user_text = "run the sleep command"
        self._open_steps = {"call_ref": "run: sleep 16"}


def _co(value):
    async def _c(*a, **k):
        return value
    return _c()


def _capture(monkeypatch):
    frames: list[tuple[str, dict]] = []

    async def fake_emit(sid, kind, **data):
        frames.append((kind, data))

    monkeypatch.setattr("kotoba.core.events.emit_task", fake_emit)
    return frames


def _refuse(monkeypatch, verdict=None):
    """The card ends without a yes; `verdict` fills the card the way request_approval does (None = a
    plain No, which verdict_of derives from the tuple alone)."""
    async def _fake(sid, action, timeout=180.0, family=None, card=None):
        if card is not None and verdict is not None:
            card["verdict"] = verdict
        return (False, False)

    monkeypatch.setattr(de, "request_approval", _fake)


def _schedule_and_wait(ctx, action, **kw):
    async def _main():
        de.schedule(ctx, action, lambda: _co("never"), **kw)
        await asyncio.sleep(0)
        for task in list(de._tasks.get(ctx.session_id, set())):
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_main())


@pytest.mark.parametrize("verdict,ending", [
    (None, "you said no"),
    (UNANSWERED, "timed out"),
    (UNREACHABLE, "couldn't get the approval card"),
])
def test_every_refusal_is_announced_as_its_own_ending_never_as_a_failure(monkeypatch, verdict, ending):
    """The work_done frame must carry the sentence that says the command never ran — and carry it where
    the web client will actually relay it (`ok` True). False here is not honesty, it is a second lie:
    announceWork would tell the model the work FAILED and drop the sentence on the floor."""
    sid = f"refusal-{ending.split()[0]}"
    de.forget_session(sid)
    work_state.clear(sid)
    frames = _capture(monkeypatch)
    _refuse(monkeypatch, verdict)

    _schedule_and_wait(_Ctx(sid), "sleep 16", label="sleep 16", step_kind="shell")

    done = [d for k, d in frames if k == "work_done"]
    assert done, "a refusal must be acknowledged out loud"
    assert "didn't run" in done[0]["summary"] and ending in done[0]["summary"]
    assert done[0]["ok"] is True, "False makes announceWork drop the sentence and report a failure"

    work_state.clear(sid)
    de.forget_session(sid)


def test_did_it_run_travels_on_the_step_row_not_on_the_announcement_flag(monkeypatch):
    """The word that answers "did it run" is the terminal row's `outcome` — the refusal's row must say
    `refused`, never ✓ and never ×, exactly core.loop._step_outcome's vocabulary."""
    sid = "refusal-row"
    de.forget_session(sid)
    work_state.clear(sid)
    frames = _capture(monkeypatch)
    _refuse(monkeypatch)

    _schedule_and_wait(_Ctx(sid), "sleep 16", label="sleep 16", step_kind="shell")

    row = [d for k, d in frames if k == "step"][-1]
    assert row["outcome"] == "refused"
    assert row["ok"] is False and row["pending"] is False

    work_state.clear(sid)
    de.forget_session(sid)


def test_the_local_register_gets_the_same_ending_not_a_failed_job(monkeypatch):
    """In voice_mode=local the frame's flag is ignored and she voices work_state.prompt_note. A refusal
    must land there as a finished item carrying the honest sentence — `fail()` would make the note open
    with "[BACKGROUND WORK just failed]", voicing the user's own decision as something going wrong."""
    sid = "refusal-local"
    de.forget_session(sid)
    work_state.clear(sid)
    _capture(monkeypatch)
    _refuse(monkeypatch)

    _schedule_and_wait(_Ctx(sid), "sleep 16", label="sleep 16", step_kind="shell")

    state = work_state.get(sid)
    assert state["status"] == "done"
    assert "didn't run" in state["summary"] and "you said no" in state["summary"]
    assert "just failed" not in work_state.prompt_note(sid)

    work_state.clear(sid)
    de.forget_session(sid)
