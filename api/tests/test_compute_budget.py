"""The work timeout must measure COMPUTE time, not human-wait time.

A login with ask_secret/ask_user (and 2FA) blocks the work loop while the human types into the secure
box. That wait must NOT count against the work budget (otherwise a slow login fails as "took too long").
`_run_with_compute_budget` subtracts the time spent in interactive waits before comparing to the budget;
`interaction._await_response` accumulates that interactive time.

Per RUN, not per session — a background job shares its parent's session_id, so accumulating per session
refunded the job for time the user spent on an approval card the job had nothing to do with, and the
work timeout stopped meaning what it says."""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.interaction as interaction
import kotoba.core.work_runner as wr


def test_compute_budget_excludes_interactive_wait():
    """0.3s of wall time against a 0.1s budget, with the (stubbed) accounting reporting 10s of human
    wait: compute_elapsed stays under the budget, so the work is not cancelled and completes."""
    async def work():
        await asyncio.sleep(0.3)
        return "done"

    res = asyncio.run(
        wr._run_with_compute_budget(work(), "s-excl", 0.1, lambda _s: 10.0)
    )
    assert res == "done"


def test_compute_budget_cancels_pure_compute():
    """With no interactive time, compute past the budget IS cancelled, as a TimeoutError."""
    async def work():
        await asyncio.sleep(5)
        return "x"

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(wr._run_with_compute_budget(work(), "s-pure", 0.1, lambda _s: 0.0))


def test_cancelling_wrapper_cancels_inner_loop():
    """If the work-runner task is cancelled (the user hung up, or ran `/leave`), the INNER agentic-loop
    coroutine must be cancelled too, not left running orphaned. Reported live: the call was hung up and
    the log showed the job still working."""
    started = asyncio.Event()
    inner_cancelled = {"v": False}

    async def work():
        started.set()
        try:
            await asyncio.sleep(30)  # stands in for a long agentic loop
        except asyncio.CancelledError:
            inner_cancelled["v"] = True
            raise

    async def go():
        wrapper = asyncio.ensure_future(
            wr._run_with_compute_budget(work(), "s-cancel", 60.0, lambda _s: 0.0)
        )
        await started.wait()
        await asyncio.sleep(0.05)
        wrapper.cancel()              # stands in for /leave cancelling the work-runner
        try:
            await wrapper
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.05)     # let the inner cancellation propagate

    asyncio.run(go())
    assert inner_cancelled["v"] is True, "inner agentic loop kept running after the work-runner was cancelled"


def test_await_response_tracks_interactive_time():
    """The wait is charged to the scope that is blocked on the human, not to the session it happens on,
    and the ~0.05s spent waiting is what lands in the accumulator."""
    async def go():
        async def resolver():
            await asyncio.sleep(0.05)
            interaction.resolve("s-track", "value")

        with interaction.interactive_scope("run-track"):
            assert interaction.interactive_seconds("run-track") == 0.0
            asyncio.create_task(resolver())
            request_id, fut = interaction._open_request("s-track")
            val = await interaction._await_response("s-track", request_id, fut, 2.0)
            return val, interaction.interactive_seconds("run-track")

    val, charged = asyncio.run(go())
    assert val == "value"
    assert charged >= 0.04


# --- the wait has to belong to the job before it may spend the job's clock ---------------------------

def test_a_wait_that_belongs_to_no_run_is_charged_to_nobody():
    """A companion turn's card is a human wait too, and it is on the job's session_id — but no running
    job is blocked on it, so it must buy no budget anywhere."""
    sid = "s-d9-unowned"
    interaction.reset_interactive(sid)

    async def go():
        request_id, fut = interaction._open_request(sid)
        await interaction._await_response(sid, request_id, fut, 0.05)  # nobody answers

    asyncio.run(go())
    assert interaction.interactive_seconds(sid) == 0.0


def test_an_unrelated_card_does_not_extend_a_running_job():
    """The same defect where it costs something: a background job SHARES its parent's session_id, so an
    approval card the user reads during a companion turn used to hand the JOB that reading time back.

    The job below wants 1.5s of compute on a 1.2s budget, and the 0.7s the human spends on somebody
    else's card must not save it."""
    sid = "s-d9-job"
    interaction.reset_interactive(sid)

    async def job():
        await asyncio.sleep(1.5)
        return "unreached"

    async def companion_card():
        request_id, fut = interaction._open_request(sid)
        await interaction._await_response(sid, request_id, fut, 0.7)

    async def go():
        card = asyncio.create_task(companion_card())
        try:
            with pytest.raises(asyncio.TimeoutError):
                await wr._run_with_compute_budget(job(), sid, 1.2, interaction.interactive_seconds)
        finally:
            card.cancel()
            try:
                await card
            except asyncio.CancelledError:
                pass

    asyncio.run(go())


def test_the_jobs_own_wait_still_buys_it_time():
    """The mechanism the accumulator exists for, unchanged: what the job's own loop blocks on (a
    password plus a 2FA code typed into the secure box) is not compute and must not fail as "took too
    long". 1.5s of wall time on a 1.2s budget, surviving only because half of it was the human."""
    sid = "s-d9-own"

    async def job():
        request_id, fut = interaction._open_request(sid)
        await interaction._await_response(sid, request_id, fut, 0.75)  # the human takes their time
        await asyncio.sleep(0.75)                                      # then 0.75s of real compute
        return "finished"

    async def go():
        with interaction.interactive_scope("run-own"):
            return await wr._run_with_compute_budget(job(), "run-own", 1.2, interaction.interactive_seconds)

    assert asyncio.run(go()) == "finished"


def test_the_job_scopes_its_human_waits_to_its_own_run(monkeypatch):
    """End to end: _run opens the scope, so a wait made inside the loop it launched is charged to that
    run's id — and the session, which the job shares with every companion turn, is charged nothing."""
    import kotoba.core.work_state as ws

    sid = "s-d9-run"
    seen: dict = {}
    emitted: list = []

    class _DB:
        async def ensure_session(self, *a, **k): pass
        async def insert_turn(self, *a, **k): pass

    async def fake_emit(session_id, kind, **data):
        emitted.append((kind, data))

    async def loop_that_waits(*a, **k):
        seen["scope"] = interaction._wait_scope.get()
        request_id, fut = interaction._open_request(sid)
        await interaction._await_response(sid, request_id, fut, 0.05)
        seen["charged"] = interaction.interactive_seconds(seen["scope"])
        return "done"

    monkeypatch.setattr(wr, "emit_task", fake_emit)
    monkeypatch.setattr("kotoba.core.loop.agentic_loop", loop_that_waits)
    ws.start(sid, "goal")
    try:
        asyncio.run(wr._run(sid, "goal", _DB(), {}, mcp=None))
    finally:
        ws._state.clear(); ws._tasks.clear()

    run_id = next(d["run_id"] for k, d in emitted if k == "work_started")
    assert seen["scope"] == run_id
    assert seen["charged"] >= 0.04
    assert interaction.interactive_seconds(sid) == 0.0
