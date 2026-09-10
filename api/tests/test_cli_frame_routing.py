"""Frames route by ORIGIN, not by the clock: whose frame is this, never "am I waiting right now".

The live defect: a detached long job kept emitting while the user asked a new question, and the job's
`WEB` rows landed INSIDE her reply and split it in half — every handler read `self.turn_start` and a
truthy clock claimed every frame for the turn. `run_id` is the origin mark: the
work-runner stamps its bracket and its loop with one id, each CLI turn mints its own in
`Session.ask()`, and a frame naming the job's run banks on the job even mid-turn. A frame without the
key predates the field and keeps the old positional routing, so nothing old breaks.
"""
from __future__ import annotations

import asyncio
import io
import tempfile
import time
from types import SimpleNamespace

import pytest

from kotoba.cli import state
from kotoba.cli.app import App
from kotoba.cli.events_bridge import Step
from kotoba.cli.render import rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

JOB = "jobrun01"


def wired() -> tuple[App, io.StringIO]:
    caps = Caps(color="none", background="dark", unicode=True, interactive=False,
                width=96, g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1", run_id="turn0001",
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app, buf


def working(app, run_id: str = JOB) -> state.Work:
    app._event("work_started", {"kind": "work_started", "goal": "dig through Live2D history",
                                **({"run_id": run_id} if run_id else {})})
    return app.work


def a_step(app, sid: str, kind: str, action: str, phase: str, **frame) -> None:
    app.session.events.steps[sid] = Step(sid, kind, action,
                                         "running" if phase == "start" else "ok")
    app._event("step", {"id": sid, "phase": phase, **frame})


def test_the_jobs_step_mid_turn_banks_on_the_job_and_never_splits_her_reply():
    app, buf = wired()
    working(app)
    app.turn_start = time.monotonic()
    app.status = "thinking"
    a_step(app, "w1", "web", "live2d history", "start", run_id=JOB)
    assert rows.work_verb(app.work) == "looking that up…", \
        "the band reads the job's phrase off the rows the frame banked on it"
    a_step(app, "w1", "web", "live2d history", "done", ok=True, run_id=JOB)
    assert buf.getvalue() == "", "the job's row printed inside the turn"
    assert app.tools == [] and [t.state for t in app.work.tools] == ["ok"]
    assert app.status == "thinking", "the job's step must not move the turn's status"
    assert rows.work_verb(app.work) == "on it…", \
        "the phrase is the job's CURRENT step: a search that landed is not one she is still running"


def test_the_turns_own_step_mid_turn_draws_where_it_always_did():
    app, buf = wired()
    working(app)
    app.turn_start = time.monotonic()
    a_step(app, "t1", "shell", "pytest -q", "start", run_id="turn0001")
    a_step(app, "t1", "shell", "pytest -q", "done", ok=True, run_id="turn0001")
    assert [t.state for t in app.tools] == ["ok"] and app.work.tools == []
    assert "pytest -q" in buf.getvalue()


def test_an_unmarked_step_keeps_the_old_positional_routing():
    """A producer that predates the field sends no key at all, and the old am-I-waiting answer is the
    only one there is: the turn's while one runs, banked on the job at the prompt."""
    app, buf = wired()
    app.work = state.Work("an old producer's job")
    app.turn_start = time.monotonic()
    a_step(app, "u1", "shell", "make lint", "start")
    a_step(app, "u1", "shell", "make lint", "done", ok=True)
    assert [t.state for t in app.tools] == ["ok"] and app.work.tools == []
    app.turn_start = 0.0
    a_step(app, "u2", "web", "old search", "start")
    a_step(app, "u2", "web", "old search", "done", ok=True)
    assert [t.state for t in app.work.tools] == ["ok"]


def test_the_jobs_step_at_the_prompt_still_banks_as_it_always_did():
    app, buf = wired()
    working(app)
    a_step(app, "w1", "web", "live2d history", "start", run_id=JOB)
    a_step(app, "w1", "web", "live2d history", "done", ok=True, run_id=JOB)
    assert buf.getvalue() == "" and [t.state for t in app.work.tools] == ["ok"]


def test_the_jobs_helper_mid_turn_banks_on_the_jobs_line_up():
    app, buf = wired()
    working(app)
    app.turn_start = time.monotonic()
    app._event("subagent_spawned", {"kind": "subagent_spawned", "id": "h1", "goal": "look it up",
                                    "toolset": "web", "run_id": JOB})
    app._event("subagent_done", {"kind": "subagent_done", "id": "h1", "ok": True,
                                 "summary": "found it", "run_id": JOB})
    assert app.helpers == [] and [h.state for h in app.work.helpers] == ["ok"]
    app._event("subagent_spawned", {"kind": "subagent_spawned", "id": "h2", "goal": "mine",
                                    "toolset": "web", "run_id": "turn0001"})
    assert [h.sid for h in app.helpers] == ["h2"], "the turn's helper is still the turn's"


def test_the_jobs_plan_close_mid_turn_banks_with_the_receipt():
    app, buf = wired()
    working(app)
    app.turn_start = time.monotonic()
    frame = {"kind": "task_list", "list_id": "L1", "status": "done", "run_id": JOB,
             "tasks": [{"order": 1, "title": "read", "status": "done"}]}
    app._event("task_list", frame)
    assert buf.getvalue() == "" and len(app.work.plans) == 1
    assert app.plan is not None and app.plan.total == 1, "the band still updates from a banked frame"


def test_the_jobs_artifact_mid_turn_banks_and_lands_with_the_receipt():
    app, buf = wired()
    working(app)
    app.turn_start = time.monotonic()
    app._event("artifact", {"kind": "artifact", "path": "reports/live2d.md", "action": "created",
                            "run_id": JOB})
    assert buf.getvalue() == "" and app.work.gift_ns == [1]
    app.turn_start = 0.0
    app.work.state, app.work.stopped = "ok", time.monotonic()
    assert app._land_work() is True
    assert "reports/live2d.md" in buf.getvalue()


def test_work_started_reads_the_runs_id_off_the_frame():
    app, _ = wired()
    job = working(app)
    assert job.run_id == JOB
    app.work = None
    old = working(app, run_id="")
    assert old.run_id == ""


def test_a_deferred_work_done_cannot_close_a_marked_bracket():
    """`deferred_exec._announce` posts `work_done` with no run_id while the runner stamps the
    bracket's — so a finished shell must not read as "the long job landed"."""
    app, _ = wired()
    working(app)
    app._event("work_done", {"kind": "work_done", "ok": True, "summary": "a shell finished"})
    assert app.work.state == "running"
    app._event("work_done", {"kind": "work_done", "ok": True, "summary": "compiled.",
                             "run_id": JOB})
    assert app.work.state == "ok" and app.work.summary == "compiled."


def test_an_unmarked_bracket_still_closes_on_an_unmarked_work_done():
    app, _ = wired()
    working(app, run_id="")
    app._event("work_done", {"kind": "work_done", "ok": True, "summary": "done the old way"})
    assert app.work.state == "ok"


NEW = "jobrun02"


def stop_then_restart(app) -> None:
    """The wire order: 'stop that, do X instead' opens the successor's bracket BEFORE the superseded
    run's cancelled close lands."""
    working(app, run_id=JOB)
    app._event("work_started", {"kind": "work_started", "goal": "the NEW job", "run_id": NEW})


def test_a_new_bracket_over_a_running_one_closes_the_old_as_superseded():
    """start_work refuses while a job runs, so a second `work_started`
    over a bracket still marked running means the old job WAS stopped and its close is in flight —
    left alone it sat in self.works as running forever: a phantom in /work N with no receipt."""
    app, buf = wired()
    app.turn_start = time.monotonic()
    stop_then_restart(app)
    old, new = app.works
    assert new is app.work and new.state == "running" and new.run_id == NEW
    assert old.state == "interrupted" and old.landed, \
        "the superseded job may not stay 'running' in /work N forever"
    assert buf.getvalue().count("WORK") >= 2, "in a turn, the old bracket's closing row commits"


def test_a_new_bracket_at_the_prompt_still_closes_the_old_silently():
    app, buf = wired()
    stop_then_restart(app)
    old = app.works[0]
    assert old.state == "interrupted" and old.landed
    assert buf.getvalue() == "", "at the prompt a print would split the pinned frame"


def test_the_superseded_runs_late_close_cannot_touch_the_new_bracket():
    app, _ = wired()
    stop_then_restart(app)
    app._event("work_done", {"kind": "work_done", "ok": True, "summary": "", "cancelled": True,
                             "run_id": JOB})
    assert app.work.state == "running", "the old run's late close landed on the new job"
    assert app.works[0].state == "interrupted"


def test_the_superseded_runs_late_step_banks_on_it_and_not_on_the_new_job():
    app, buf = wired()
    working(app, run_id=JOB)
    a_step(app, "w9", "shell", "long build", "start", run_id=JOB)
    app._event("work_started", {"kind": "work_started", "goal": "the NEW job", "run_id": NEW})
    app.turn_start = time.monotonic()
    before = buf.getvalue()
    app.session.events.steps["w9"] = Step("w9", "shell", "long build", "interrupted")
    app._event("step", {"id": "w9", "phase": "done", "ok": False, "interrupted": True,
                        "run_id": JOB})
    assert [t.state for t in app.works[0].tools] == ["interrupted"]
    assert app.work.tools == [], "the old run's landing claimed the new job's surface"
    assert buf.getvalue() == before, "the old run's landing printed inside the turn"


def test_the_jobs_read_mid_turn_does_not_take_the_turns_status_line():
    """A job's peek names its run; mid-turn the bar belongs to the turn, so 'reading the file…' from
    the detached job (or a superseded run's clear) may not overwrite what the turn is doing."""
    app, _ = wired()
    working(app)
    app.turn_start = time.monotonic()
    app._event("peek", {"kind": "peek", "tool": "read_file", "run_id": JOB})
    assert app.peek == "", "the detached job's read took the turn's status line"
    app._event("peek", {"kind": "peek", "tool": "memory_recall", "run_id": "turn0001"})
    assert app.peek == "memory_recall", "the turn's own read still shows"


def test_a_superseded_runs_late_emotion_mid_turn_does_not_move_her_face():
    app, _ = wired()
    stop_then_restart(app)
    app.turn_start = time.monotonic()
    before = app.screen.face.emotion
    app._event("emotion", {"type": "emotion", "emotion": "excited", "run_id": JOB})
    app.screen.face.settle()
    assert app.screen.face.emotion == before


def test_the_jobs_emotion_mid_turn_does_not_move_her_face():
    """Mid-turn her face belongs to the turn: the detached job's mood swings must not flicker across
    the reply she is composing. The turn's own frames and unmarked ones move her as they always did,
    and out at the prompt the job may still move her."""
    app, _ = wired()
    working(app)
    app.turn_start = time.monotonic()
    before = app.screen.face.emotion
    app._event("emotion", {"type": "emotion", "emotion": "excited", "run_id": JOB})
    app.screen.face.settle()
    assert app.screen.face.emotion == before
    app._event("emotion", {"type": "emotion", "emotion": "determined", "run_id": "turn0001"})
    app.screen.face.settle()
    assert app.screen.face.emotion == "determined"
    app.turn_start = 0.0
    app.face_fixed = False
    app._event("emotion", {"type": "emotion", "emotion": "happy", "run_id": JOB})
    app.screen.face.settle()
    assert app.screen.face.emotion == "happy"


@pytest.fixture()
def own_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tempfile.mkdtemp() + "/kotoba.db"))


def test_each_turn_mints_its_own_run_id_and_the_loop_receives_it(monkeypatch, own_db):
    import kotoba.cli.session as cli_session
    from kotoba.cli.session import Session

    seen: list[dict] = []

    async def fake_loop(items, sid, db, stream, patterns, **kw):
        seen.append(dict(kw))
        await stream.put("dicho.")
        return "dicho."

    monkeypatch.setattr(cli_session, "agentic_loop", fake_loop)

    async def go():
        session = await Session.open()
        await session.ask("hola")
        first = session.run_id
        await session.ask("otra")
        second = session.run_id
        await session.close()
        return first, second

    first, second = asyncio.run(go())
    assert seen[0]["run_id"] == first and seen[1]["run_id"] == second
    assert first and second and first != second


def test_a_sentinel_turn_withholds_the_tools_that_could_relaunch_the_job(monkeypatch, own_db):
    """The web path withholds `start_work` and `delegate` on a trigger sentinel; unmirrored, the CLI's
    announce turn could relaunch the very job it is closing."""
    import kotoba.cli.session as cli_session
    from kotoba.cli.session import Session

    seen: list[dict] = []

    async def fake_loop(items, sid, db, stream, patterns, **kw):
        seen.append(dict(kw))
        await stream.put("dicho.")
        return "dicho."

    monkeypatch.setattr(cli_session, "agentic_loop", fake_loop)

    async def go():
        session = await Session.open()
        await session.ask(cli_session.WORK_DONE)
        await session.ask("hola")
        await session.close()

    asyncio.run(go())
    assert seen[0]["exclude_tools"] == frozenset({"start_work", "delegate"})
    assert seen[1]["exclude_tools"] is None
