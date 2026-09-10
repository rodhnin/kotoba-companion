"""A plan handed to a background job belongs to that job, and nothing else may settle it.
Found live: asked who a character was, she wrote a 3-step plan and called start_work — seven seconds in,
the screen showed the plan closed 3 of 3 done, while the job ran on for another five minutes (355.1s
total). The renderer was faithful: the list really was closed.

The second, closing todo call came from the COMPANION turn, not the job: it ran its own independent web
search seven seconds after the job's, and `task_list` is keyed by session alone, so the fast turn settled
the slow job's plan — both had been handed the same "tick it now, close it when the job is over" note.
The list now names its owner: start_work claims the open plan for the job it launches, the job gives it
back when it ends, and a tick from anyone else moves nothing in between."""
from __future__ import annotations

import asyncio
import types

import pytest

import kotoba.core.task_list as tl
from kotoba.core import work_runner, work_state
from kotoba.tools.builtin import start_work as sw
from kotoba.tools.builtin import todo

STEPS = ["Buscar qué personaje de Barrio Sésamo es 'Pepe'",
         "Confirmar fuentes fiables",
         "Responder de forma breve con contexto"]
GOAL = "buscar por internet qué personaje de Barrio Sésamo es 'Pepe'"
SID = "pepe"


def setup_function():
    tl._state.clear()
    tl._job_run.clear()
    work_state.clear(SID)


def teardown_function():
    task = work_state.pop_task(SID)
    if task is not None:
        task.cancel()
    tl._state.clear()
    tl._job_run.clear()
    work_state.clear(SID)


def _ctx(run_id="", session_id=SID, announce=False):
    return types.SimpleNamespace(session_id=session_id, run_id=run_id, db=None,
                                 announce_turn=announce, soul_patterns={}, mcp=None, user_text="")


def _todo(run_id="", **args):
    return asyncio.run(todo.execute(args, _ctx(run_id)))


async def _launch(companion_run="companionA"):
    """Drive the real start_work, with the runner's body stubbed so no provider is called.

    The stub is what makes the race visible: _run is a detached task that does not begin until the next
    tick, so anything the launching turn does next happens first."""
    async def _never(*a, **kw):
        await asyncio.sleep(3600)

    real, work_runner._run = work_runner._run, _never
    try:
        await sw.execute({"goal": GOAL}, _ctx(companion_run))
    finally:
        work_runner._run = real
    return (tl.get(SID) or {}).get("owner_run", "")


def _statuses():
    return [t["status"] for t in tl.get(SID)["tasks"]]


def test_the_owner_is_the_run_id_the_job_actually_loops_under():
    """The whole guard rests on one identity: what start() binds is what reaches ctx.run_id.

    start() mints it, hands it to _run, _run hands it to agentic_loop, and agentic_loop stamps it on the
    context every todo call reads. Minted inside _run instead, the claim would land a tick late."""
    seen = {}

    async def _capture(session_id, goal, db, patterns, mcp, request="", el_call_bound=False, run_id="", **kw):
        seen["run_id"] = run_id

    async def _go():
        real, work_runner._run = work_runner._run, _capture
        try:
            work_runner.start(SID, GOAL, None, {}, None)
            await asyncio.sleep(0)
        finally:
            work_runner._run = real

    _todo("companionA", steps=STEPS)
    asyncio.run(_go())
    assert seen["run_id"] and seen["run_id"] == tl.get(SID)["owner_run"]


def test_start_work_claims_the_plan_the_turn_just_wrote():
    _todo("companionA", steps=STEPS, title="Pepe de Barrio Sésamo")
    owner = asyncio.run(_launch())
    assert owner and owner != "companionA"
    assert tl.held_from(SID, "companionA") is True
    assert tl.held_from(SID, owner) is False


def test_the_launching_turn_cannot_tick_or_close_the_jobs_plan():
    """The measured call, replayed: done=[1,2,3] + active=3 + close=True, from the companion turn."""
    _todo("companionA", steps=STEPS, title="Pepe de Barrio Sésamo")
    asyncio.run(_launch())

    out = _todo("companionA", steps=STEPS, title="Pepe de Barrio Sésamo",
                done=[1, 2, 3], active=3, close=True)

    assert "background job" in out
    assert tl.get(SID)["status"] == "open"
    assert _statuses() == ["pending", "pending", "pending"]
    frame = tl.frame(SID)
    assert sum(1 for t in frame["tasks"] if t["status"] == "done") == 0


def test_the_launching_turn_cannot_replace_the_jobs_plan_either():
    """Replacing is the destructive half of the same reach: a new list_id and every mark back to pending."""
    _todo("companionA", steps=STEPS)
    asyncio.run(_launch())
    before = tl.get(SID)["list_id"]

    out = _todo("companionA", steps=["something else entirely", "and another"])

    assert "background job" in out
    assert tl.get(SID)["list_id"] == before
    assert [t["text"] for t in tl.get(SID)["tasks"]] == STEPS


def test_the_job_that_owns_it_ticks_and_closes_it_normally():
    _todo("companionA", steps=STEPS)
    owner = asyncio.run(_launch())

    assert "Next: step 2" in _todo(owner, done=[1], active=2)
    assert _statuses() == ["done", "active", "pending"]
    out = _todo(owner, done=[2, 3], close=True)
    assert "Plan done. 3/3" in out
    assert tl.get(SID)["status"] == "done"


def test_a_helper_spawned_by_the_job_shares_the_jobs_run():
    """A helper runs under the parent's run_id, so the job's own run is what ticks the plan here."""
    _todo("companionA", steps=STEPS)
    owner = asyncio.run(_launch())
    assert tl.held_from(SID, owner) is False
    _todo(owner, done=[1])
    assert _statuses()[0] == "done"


def test_an_unclaimed_plan_is_still_anyones_to_tick():
    """The ordinary companion plan, with no background job behind it, is unchanged."""
    _todo("turn-1", steps=["a", "b"])
    assert "Next: step 2" in _todo("turn-2", done=[1])
    assert "Plan done. 2/2" in _todo("", done=[2], close=True)


def test_the_job_gives_the_plan_back_when_it_ends(monkeypatch):
    """Held past the end of the job, no later turn could ever tick or replace the list again.

    Drives the real _run — the release lives in its finally, so it has to survive every ending."""
    async def _emit(session_id, kind, **data):
        pass

    async def _loop(*a, **k):
        return "found it"

    monkeypatch.setattr(work_runner, "emit_task", _emit)
    monkeypatch.setattr("kotoba.core.loop.agentic_loop", _loop)

    _todo("companionA", steps=STEPS)
    run = "job-run"
    tl.bind_run(SID, run)
    work_state.start(SID, GOAL)
    assert tl.held_from(SID, "companionA") is True

    asyncio.run(work_runner._run(SID, GOAL, None, {}, None, run_id=run))

    assert tl.held_from(SID, "companionA") is False
    assert tl._job_run.get(SID) is None
    assert "Next: step 2" in _todo("companionA", done=[1])


def test_a_cancelled_job_gives_the_plan_back_too(monkeypatch):
    """Talking to her mid-work cancels it, and a cancel arrives as CancelledError, not a return.

    Re-recorded. The original drove the "cancel" by having the LOOP raise CancelledError
    itself, which pinned the premise that any CancelledError reaching the runner is a user stop.
    That premise is overturned — a stray, unrequested CancelledError is now handled as a failure —
    so the stop here arrives the way a user's actually does:
    by cancelling the runner's task. The plan is given back on either ending; the finally is one."""
    async def _emit(session_id, kind, **data):
        pass

    async def _loop(*a, **k):
        await asyncio.sleep(30)
        return "never"

    monkeypatch.setattr(work_runner, "emit_task", _emit)
    monkeypatch.setattr("kotoba.core.loop.agentic_loop", _loop)

    _todo("companionA", steps=STEPS)
    tl.bind_run(SID, "job-run")
    work_state.start(SID, GOAL)

    async def _drive():
        t = asyncio.create_task(work_runner._run(SID, GOAL, None, {}, None, run_id="job-run"))
        await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(_drive())
    assert tl.held_from(SID, "companionA") is False


def test_the_state_module_refuses_a_stranger_even_without_the_tool():
    """The guarantee is not the tool's early check — the mutation itself moves nothing and says so."""
    tl.bind_run(SID, "job-run")
    tl.open_list(SID, ["a", "b"], by_run="job-run")

    out = tl.update(SID, done=[1, 2], close=True, by_run="companionA")
    assert out["refused_run"] == "job-run"
    assert out["status"] == "open"
    assert [t["status"] for t in out["tasks"]] == ["pending", "pending"]

    same = tl.open_list(SID, ["x"], by_run="companionA")
    assert same["refused_run"] == "job-run"
    assert [t["text"] for t in same["tasks"]] == ["a", "b"]

    moved = tl.update(SID, done=[1], by_run="job-run")
    assert moved["refused_run"] == "" and moved["tasks"][0]["status"] == "done"


def test_release_ignores_a_run_that_is_not_the_owner():
    _todo("companionA", steps=STEPS)
    owner = asyncio.run(_launch())
    tl.release_run(SID, "some-other-run")
    assert tl.get(SID)["owner_run"] == owner


def test_a_plan_the_job_opens_mid_run_is_born_owned_by_it():
    """The other door in: there is nothing to claim at launch when she writes the plan once running."""
    owner = asyncio.run(_launch())
    assert owner == ""
    assert tl.get(SID) is None

    job = work_state.pop_task(SID)
    run = "job-run"
    tl.bind_run(SID, run)
    _todo(run, steps=["find it", "say it"])

    assert tl.get(SID)["owner_run"] == run
    assert "background job" in _todo("companionA", done=[1, 2], close=True)
    assert _statuses() == ["pending", "pending"]
    job.cancel()


def test_a_companion_plan_opened_beside_a_job_is_not_claimed_for_it():
    """Only the job's own run claims. A plan the LIVE turn writes while a job runs stays the turn's."""
    asyncio.run(_launch())
    tl.bind_run(SID, "job-run")
    _todo("companionA", steps=["jot this down"])
    assert tl.get(SID)["owner_run"] == ""
    assert "Plan done. 1/1" in _todo("companionA", done=[1], close=True)


def test_resent_steps_are_named_in_the_result():
    """She resent the whole list on all three of the measured calls, and the tool absorbed it in silence."""
    _todo("t", steps=["a", "b"])
    out = _todo("t", steps=["a", "b"], done=[1])
    assert "resent and ignored" in out
    assert "1 done" in out and "Next: step 2" in out
    assert "resent" not in _todo("t", done=[2])
