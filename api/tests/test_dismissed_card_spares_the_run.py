"""A dismissed approval card must never kill the run that is waiting on it.

The barge-in gesture used to dismiss a pending card by CANCELLING its Future; the error
unwound the whole run silently, with no audit row. The gesture is now retired, but this file
pins the PRIMITIVE's contract so reintroducing it anywhere stays safe.

Dismissal is now an ANSWER: `dismiss()` resolves each wait with a DISMISSED sentinel (neither
a No nor a timeout), and every waiter takes the ordinary not-approved path. Pinned: the run
survives and nothing runs; work_runner announces rather than dying as user-cancelled; a real
task cancellation still cancels THROUGH the gate; and a deferred card stays quiet, refused."""
from __future__ import annotations

import asyncio

import pytest

from kotoba.core import deferred_exec, events, interaction, work_runner, work_state
from kotoba.core.approval import ApprovalGate
from kotoba.tools.action import shell

COMMAND = "pip install requests"


def make_gate(sid: str, audit_rows: list | None = None) -> ApprovalGate:
    """The gate exactly as core.loop wires it: the asker calls request_approval and hands back the
    four-slot tuple whose last element is the card's own ending."""

    async def _ask(action, risk, family=None):
        card: dict = {}
        approved, always = await interaction.request_approval(
            sid, action, channel="text", family=family, card=card,
        )
        return (approved, always, bool(card.get("always_exact")),
                interaction.verdict_of(card, approved))

    async def _audit(action, risk, ok, who, detail):
        if audit_rows is not None:
            audit_rows.append({"action": action, "ok": ok, "who": who, "detail": detail})

    return ApprovalGate(ask=_ask, audit=_audit, host_exec=True)


class Ctx:
    def __init__(self, sid: str, gate: ApprovalGate | None) -> None:
        self.session_id = sid
        self.channel = "text"          # what work_runner passes to the loop
        self.approval = gate
        self.call_id = "call-1"
        self.user_text = "instala requests"
        self.run_id = ""
        self.ran: list[str] = []

    async def ensure_sandbox(self):
        self.ran.append("sandbox")
        return None


async def until(pred, limit: float = 5.0) -> bool:
    end = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.005)
    return False


def drain(q: asyncio.Queue) -> list[dict]:
    out: list[dict] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def named_clears(frames: list[dict]) -> list[dict]:
    return [f for f in frames
            if f.get("kind") == "need_input" and f.get("mode") == "clear" and f.get("request_id")]


def test_a_dismissed_inline_approval_spares_the_run_and_runs_nothing():
    """The reproduced composition, fixed. A work-mode shell blocked on its card gets a plain
    not-approved answer when the card is waved away: the coroutine returns shell's own refusal line,
    the sandbox is never touched, and the audit row names the dismissal — not the user."""
    sid = "dismiss-inline"
    rows: list[dict] = []

    async def go():
        q = events.register(sid)
        gate = make_gate(sid, rows)
        ctx = Ctx(sid, gate)
        job = asyncio.create_task(shell.execute({"command": COMMAND}, ctx))
        assert await until(lambda: interaction.has_pending(sid)), "no card opened"
        dropped = interaction.dismiss(sid)
        result = await asyncio.wait_for(job, 5)   # CancelledError here IS the old defect
        frames = drain(q)
        events.unregister(sid, q)
        return dropped, result, ctx.ran, frames

    dropped, result, ran, frames = asyncio.run(go())

    assert dropped == 1
    assert "held off" in result, f"expected the ordinary not-approved line, got {result!r}"
    assert ran == [], "a dismissed card still reached the sandbox — the negative failed"
    assert named_clears(frames), "the dismissed card was never cleared on screen"
    assert rows == [{"action": COMMAND, "ok": False, "who": "dismissed", "detail": "decision"}], (
        f"the trail must record the dismissal as its own authority, got {rows!r}"
    )


def test_the_work_runner_finishes_instead_of_dying_as_user_cancelled(monkeypatch):
    """Top of the chain. The real work_runner._run, its loop blocked on the real gate: dismissing the
    card must let the job finish and emit an announceable work_done — never `cancelled: true`, which
    the frontend deliberately keeps silent (it means the user asked for the stop)."""
    sid = "dismiss-job"

    async def go():
        q = events.register(sid)
        gate = make_gate(sid)

        async def fake_loop(input_items, session_id, db, queue, patterns, **kw):
            ok = await gate.confirm(COMMAND, "exec")
            return f"done; the install was {'approved' if ok else 'not approved'}"

        monkeypatch.setattr("kotoba.core.loop.agentic_loop", fake_loop)
        work_state.start(sid, "install deps")
        job = asyncio.create_task(work_runner._run(sid, "install deps", None, {}, None))
        work_state.register_task(sid, job)
        assert await until(lambda: interaction.has_pending(sid)), "no card opened"
        interaction.dismiss(sid)
        await asyncio.wait_for(job, 10)           # a cancelled task would raise here
        frames = drain(q)
        snap = work_state.get(sid)
        work_state.clear(sid)
        events.unregister(sid, q)
        return frames, snap

    frames, snap = asyncio.run(go())

    done = [f for f in frames if f.get("kind") == "work_done"]
    assert len(done) == 1, f"expected one work_done, got {done!r}"
    assert not done[0].get("cancelled"), "the job was buried as user-cancelled — the silent death"
    assert done[0].get("ok") is True and done[0].get("summary"), (
        f"the finish must be announceable, got {done[0]!r}"
    )
    assert snap["status"] == "done", f"work_state should hold a finished job, got {snap['status']!r}"


def test_a_real_task_cancellation_still_cancels_through_the_gate():
    """The negative that must survive the fix: supersede and the compute budget kill a run by
    cancelling its TASK, and that cancellation must keep unwinding straight through confirm() — a
    gate that swallowed it would resurrect superseded turns. The card still clears on the way out."""
    sid = "dismiss-real-cancel"

    async def go():
        q = events.register(sid)
        gate = make_gate(sid)
        ctx = Ctx(sid, gate)
        job = asyncio.create_task(shell.execute({"command": COMMAND}, ctx))
        assert await until(lambda: interaction.has_pending(sid)), "no card opened"
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        frames = drain(q)
        events.unregister(sid, q)
        return ctx.ran, frames

    ran, frames = asyncio.run(go())

    assert ran == [], "a cancelled run still reached the sandbox"
    assert named_clears(frames), "the cancelled wait left its card on screen"
    assert not interaction.has_pending(sid)


def test_a_dismissed_input_box_answers_none_and_clears_once():
    """ask_secret / request_credential block on request_input in work mode — the same composition one
    tool over. A dismissal must come back as 'no answer' (None), never as a raise and never as a
    typed value, and the card must be cleared exactly once (no duplicate clear frames)."""
    sid = "dismiss-input"

    async def go():
        q = events.register(sid)
        waiter = asyncio.create_task(
            interaction.request_input(sid, "type the password", "secret", timeout=5)
        )
        assert await until(lambda: interaction.has_pending(sid)), "no box opened"
        interaction.dismiss(sid)
        value = await asyncio.wait_for(waiter, 5)
        frames = drain(q)
        events.unregister(sid, q)
        return value, frames

    value, frames = asyncio.run(go())

    assert value is None, f"a dismissal must never become a typed value, got {value!r}"
    clears = [f for f in frames if f.get("kind") == "need_input" and f.get("mode") == "clear"]
    assert len(clears) == 1, f"expected exactly one clear, got {clears!r}"
    assert clears[0].get("request_id"), "an unnamed clear wipes every card on screen"


def test_a_dismissed_deferred_card_stays_quiet_and_its_yes_is_dead():
    """The path dismiss was BUILT for keeps today's behavior to the letter: nothing runs, a named
    clear leaves the server, the row closes as refused, nothing is announced (no work_done — the
    user is mid-sentence), a later Yes finds no live Future, and a re-emission is told it was
    dismissed instead of opening a second card."""
    sid = "dismiss-deferred"
    ran: list[str] = []

    async def runner() -> str:
        ran.append(COMMAND)
        return "I ran it."

    async def go():
        q = events.register(sid)
        ctx = Ctx(sid, None)
        deferred_exec.schedule(ctx, COMMAND, runner, label=COMMAND, step_kind="shell")
        assert await until(lambda: interaction.has_pending(sid)), "no card opened"
        detached = next(iter(deferred_exec._tasks.get(sid, set())))
        interaction.dismiss(sid)
        await asyncio.wait_for(detached, 5)       # completes normally now; it used to die cancelled
        yes_after = interaction.resolve(sid, {"approved": True, "always": False})
        await asyncio.sleep(0.05)
        frames = drain(q)
        settled = dict((deferred_exec._settled.get(sid) or {}))
        deferred_exec.forget_session(sid)
        events.unregister(sid, q)
        return yes_after, frames, settled

    yes_after, frames, settled = asyncio.run(go())

    assert ran == [], "a dismissed deferred card still ran its command"
    assert yes_after is False, "the dismissed card's Yes was still wired to a live Future"
    assert named_clears(frames), "the dismissed card was never cleared on screen"
    rows = [f for f in frames if f.get("kind") == "step" and f.get("phase") == "done"]
    assert rows and rows[0].get("outcome") == "refused", f"the row must close as refused: {rows!r}"
    assert not [f for f in frames if f.get("kind") == "work_done"], (
        "a wave-away must not be announced — the user is mid-sentence"
    )
    assert settled and "dismissed" in next(iter(settled.values())), (
        f"a re-emission must learn the card was dismissed: {settled!r}"
    )


def test_dismissed_is_a_word_every_derived_sentence_knows():
    """The vocabulary rule this module runs on: one word, every sentence derived from it. A word one
    consumer does not know brings the original defect back in miniature — a blank row, or an approver
    called 'user' over a card nobody answered."""
    assert interaction.approver_for(interaction.DISMISSED) == "dismissed"
    assert interaction.refusal_row(interaction.DISMISSED), "a blank row detail hides the ending"
    note = interaction.refusal_note(interaction.DISMISSED, "running 'x'")
    assert "NOT a refusal" in note and "did not happen" in note
    assert interaction.verdict_of({"verdict": "dismissed"}, False) == interaction.DISMISSED
