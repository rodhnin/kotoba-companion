"""`/helpers` answers about the line-up that is OUT, or the one that came back last.

Typed three times over a job with three helpers running, it answered `no helpers yet — they only
run inside a work turn` under a bar that said WORKING. The command read `app.last_helpers`, which only
`_commit_roster` ever wrote, from the TURN's line-up; a job's helpers bank on `app.work.helpers`
(`app._helper`) and never reached it — so the same answer came back after the job too, while `/work 1`
drew all three. The two banks have always been separate; the command simply read only one of them.
"""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

from kotoba.cli import slash, state
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

GOALS = ("the ElevenLabs prices", "the OpenAI prices", "the Railway prices")


def wired(height: int = 30) -> tuple[App, io.StringIO]:
    caps = Caps(color="none", background="dark", unicode=True, width=96, height=height,
                g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1", events=SimpleNamespace(steps={}, settle=lambda: None))
    return app, buf


def said(app, arg: str = "") -> str:
    app.screen.console.file.truncate(0), app.screen.console.file.seek(0)
    asyncio.run(slash.run(app, commands.Command("/helpers", arg)))
    return app.screen.console.file.getvalue()


def a_job(*states: str) -> state.Work:
    job = state.Work("look into the prices", n=1, run_id="r1")
    for goal, st in zip(GOALS, states):
        job.helpers.append(state.Helper(f"h{len(job.helpers) + 1}", "research", goal, state=st,
                                        started=1.0, stopped=0.0 if st in ("running", "queued") else 9.0,
                                        summary="" if st in ("running", "queued") else "came back"))
    return job


def test_helpers_names_the_line_up_the_long_job_has_out_right_now():
    app, _ = wired()
    app.work = a_job("running", "queued", "ok")
    app.works = [app.work]
    out = said(app)
    assert "no helpers" not in out, out
    assert "2 helpers out right now, 1 back" in out, out
    for goal in GOALS:
        assert goal in out, f"{goal!r} missing from {out!r}"
    one = said(app, "2")
    assert GOALS[1] in one and GOALS[0] not in one and GOALS[2] not in one, one


def test_helpers_on_a_live_job_with_nothing_out_yet_says_exactly_that():
    app, _ = wired()
    app.last_helpers = [state.Helper("old", "research", "last week's goal", state="ok")]
    app.work = state.Work("a job that has not split yet", n=1)
    app.works = [app.work]
    out = said(app)
    assert "none out on this job yet" in out, out
    assert "last week's goal" not in out and "work turn" not in out, out


def test_helpers_reads_the_long_jobs_line_up_back_once_it_landed():
    app, _ = wired()
    app.work = a_job("ok", "ok", "failed")
    app.work.state, app.work.said = "ok", "done~"
    app.works = [app.work]
    assert app._land_work() is True
    out = said(app)
    assert "3 helpers last time" in out, out
    for goal in GOALS:
        assert goal in out, f"{goal!r} missing from {out!r}"


def test_helpers_reads_a_stopped_jobs_line_up_back_too():
    app, _ = wired()
    app.work = a_job("running", "running", "ok")
    app.works = [app.work]
    slash._cut(app.work)
    assert app._land_work() is True
    out = said(app)
    assert "no helpers" not in out and all(goal in out for goal in GOALS), out


def test_the_no_helpers_message_never_claims_it_is_said_outside_a_work_turn():
    app, _ = wired()
    out = said(app)
    assert "no helpers yet" in out and "work turn" not in out, out
