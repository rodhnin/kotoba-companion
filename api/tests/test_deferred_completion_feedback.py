"""Live QA found one silence with two halves: you approved a deferred command and NOTHING happened — no
line, no sound, no panel change; the result only surfaced when you next spoke. And the terminal row it
left behind showed a ✓ next to "I asked for your permission on screen", never updated with the real run
or its output (the 168 that Python actually computed never appeared anywhere).

So a finished deferred action must now do two things the work-runner already did:
  • emit `work_done` — the frame the frontend turns into the __work_done__ sentinel, so she SAYS it;
  • re-emit its `step` row (same call_id) with pending cleared and the REAL result.
"""
from __future__ import annotations

import asyncio

import kotoba.core.deferred_exec as de
from kotoba.core import work_state


class _Ctx:
    """Its OWN session per test. Sharing one id leaked deferred-action state between tests: they only
    stayed green because the re-emission guard fails open when a ctx carries no user_text."""

    def __init__(self, session_id, mode="companion"):
        self.approval = None
        self.mode = mode
        self.session_id = session_id
        self.call_id = "call_abc"
        self.user_text = "do the thing"
        self._open_steps = {"call_abc": "run: python primes.py"}


def _co(value):
    async def _c(*a, **k):
        return value
    return _c()


def _capture(monkeypatch):
    """Collect every frame emitted through core.events.emit_task on this path."""
    frames: list[tuple[str, dict]] = []

    async def fake_emit(sid, kind, **data):
        frames.append((kind, data))

    monkeypatch.setattr("kotoba.core.events.emit_task", fake_emit)
    return frames


def _schedule_and_wait(ctx, action, runner, **kw):
    """Drive schedule() the way the loop does and let the detached task finish."""
    async def _main():
        de.schedule(ctx, action, runner, **kw)
        await asyncio.sleep(0)
        for task in list(de._tasks.get(ctx.session_id, set())):
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_main())


def test_approved_deferred_command_announces_and_completes_its_row(monkeypatch):
    sid = "dfs-approved"
    work_state.clear(sid)
    frames = _capture(monkeypatch)
    monkeypatch.setattr(de, "request_approval", lambda sid, action, timeout=180.0, **k: _co((True, False)))

    async def runner():
        return "I ran your code (exit code 0). Output: 168"

    _schedule_and_wait(_Ctx(sid), "run Python: primes", runner, label="your code", step_kind="code")

    # She is told at the moment it happens — the same frame the work-runner emits.
    done = [d for k, d in frames if k == "work_done"]
    assert done and done[0]["ok"] is True
    assert "168" in done[0]["summary"]

    # The terminal row is completed with the REAL output, on the original call_id, no longer pending.
    steps = [d for k, d in frames if k == "step"]
    assert steps, "the deferred action must complete its own terminal row"
    row = steps[-1]
    assert row["id"] == "call_abc" and row["phase"] == "done"
    assert row["pending"] is False and row["ok"] is True
    assert "168" in row["result"]
    assert row["step_kind"] == "code" and row["action"] == "run: python primes.py"


def test_failed_deferred_command_announces_failure_and_marks_the_row(monkeypatch):
    sid = "dfs-failed"
    work_state.clear(sid)
    frames = _capture(monkeypatch)
    monkeypatch.setattr(de, "request_approval", lambda sid, action, timeout=180.0, **k: _co((True, False)))

    async def boom():
        raise RuntimeError("nope")

    _schedule_and_wait(_Ctx(sid), "rm -rf junk", boom, label="rm -rf junk", step_kind="shell")

    assert [d for k, d in frames if k == "work_done"][0]["ok"] is False
    row = [d for k, d in frames if k == "step"][-1]
    assert row["ok"] is False and row["pending"] is False


def test_declined_deferred_command_still_says_something(monkeypatch):
    """Tapping No used to be silent too — work_state kept the note for the NEXT turn. Say it now."""
    sid = "dfs-declined"
    work_state.clear(sid)
    frames = _capture(monkeypatch)
    monkeypatch.setattr(de, "request_approval", lambda sid, action, timeout=180.0, **k: _co((False, False)))

    _schedule_and_wait(_Ctx(sid), "rm -rf /", lambda: _co("never"), label="rm -rf /", step_kind="shell")

    assert [k for k, _ in frames if k == "work_done"], "a declined action must be acknowledged out loud"
    row = [d for k, d in frames if k == "step"][-1]
    assert row["pending"] is False and row["ok"] is False


def test_no_announcement_while_a_background_job_owns_work_state(monkeypatch):
    """work_done also stops the keepalive, clears the working chip and dismisses the chibis. If a real
    background job is running, that frame is ITS to send — a deferred shell must not close it early."""
    sid = "dfs-busy"
    work_state.start(sid, "build the site")
    frames = _capture(monkeypatch)
    monkeypatch.setattr(de, "request_approval", lambda sid, action, timeout=180.0, **k: _co((True, False)))

    _schedule_and_wait(_Ctx(sid), "ls", lambda: _co("ok"), label="ls", step_kind="shell")

    assert not [k for k, _ in frames if k == "work_done"]
    assert [d for k, d in frames if k == "step"], "the row is still completed either way"
    work_state.clear(sid)


def test_busy_is_sampled_when_the_result_lands_not_before_the_wait(monkeypatch):
    """The approval card can sit on screen for minutes; start_work may fire in that gap. Sampling
    is_running() up front would miss it and fire work_done over the live job."""
    sid = "dfs-late-job"
    work_state.clear(sid)
    frames = _capture(monkeypatch)

    def _approve_then_start_a_job(sid, action, timeout=180.0, **k):
        work_state.start(sid, "a job that began while the card was up")
        return _co((True, False))

    monkeypatch.setattr(de, "request_approval", _approve_then_start_a_job)
    _schedule_and_wait(_Ctx(sid), "ls", lambda: _co("ok"), label="ls", step_kind="shell")

    assert not [k for k, _ in frames if k == "work_done"]
    work_state.clear(sid)


def test_is_pending_marks_the_call_until_it_actually_runs(monkeypatch):
    """The loop asks this to decide whether the row it closes gets a ✓ or a 'waiting' marker."""
    sid = "dfs-pending"
    ctx = _Ctx(sid)
    assert de.is_pending(ctx, "call_abc") is False
    monkeypatch.setattr(de, "request_approval", lambda sid, action, timeout=180.0, **k: _co((True, False)))

    async def _main():
        de.schedule(ctx, "ls", lambda: _co("ok"), label="ls", step_kind="shell")
        # marked synchronously — the loop closes the row in the same turn, before the task can run
        assert de.is_pending(ctx, "call_abc") is True
        assert de.is_pending(ctx, "some_other_call") is False
        for task in list(de._tasks.get(ctx.session_id, set())):
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_main())
    work_state.clear(sid)
