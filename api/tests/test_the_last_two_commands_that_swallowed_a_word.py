"""Two commands in the no-argument family were never wired to reject a stray argument: /stop and
/clear. Both name their parameter `_`, so a sweep looking for a dropped `arg` found nothing.

/stop is the one that bites: jobs are numbered, so `/stop 2` reads as "stop job 2", but it opened the
confirm rail for whatever was running at that moment — not necessarily job 2 — silently ignoring the
number. A rail that kills something is the last place a misread number may go unanswered.

/clear refuses BEFORE the glass is wiped, deliberately, since it promises only the boot header and a
refusal under a freshly cleared screen would contradict that. The census below guards against a third
one; /quit is the only bare command left out on purpose."""
from __future__ import annotations

import asyncio
import io
import re
from types import SimpleNamespace

import pytest
from rich.text import Text

from kotoba.cli import slash, state
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

BARE = {"/last", "/emotions", "/calm", "/plate", "/stop", "/clear"}


def wired(width: int = 96, height: int = 40):
    caps = Caps(color="none", background="dark", unicode=True, width=width, height=height,
                g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1",
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app, buf


def said(app, name: str, arg: str = "") -> str:
    app.screen.console.file.truncate(0)
    app.screen.console.file.seek(0)
    asyncio.run(slash.run(app, commands.Command(name, arg)))
    return app.screen.console.file.getvalue()


def a_running_job(app) -> state.Work:
    """Two jobs, the SECOND still running — so `/stop 2` names the job that is actually going and the
    command still may not read the number as having chosen it."""
    done = state.Work("lo de antes", n=1)
    done.state, done.stopped = "ok", done.t0 + 1.0
    job = state.Work("una tarea de fondo", n=2)
    app.works = [done, job]
    app.work = job
    return job


# --- /stop -----------------------------------------------------------------------------------------

def test_stop_handed_a_job_number_answers_the_number_instead_of_opening_a_rail_over_it():
    app, _ = wired()
    job = a_running_job(app)
    out = said(app, "/stop", "2")

    assert "/stop takes nothing" in out
    assert "stop the long job?" not in out
    assert app.confirm is None
    assert job.state == "running"


def test_stop_says_which_job_it_would_have_reached_so_the_number_is_not_merely_refused():
    app, _ = wired()
    a_running_job(app)
    out = said(app, "/stop", "2")

    assert "the one that's running" in out


def test_stop_handed_nothing_still_opens_the_rail_it_always_did():
    app, _ = wired()
    a_running_job(app)
    out = said(app, "/stop")

    assert "takes nothing" not in out
    assert "stop the long job?" in out


def test_stop_handed_nothing_with_no_job_still_says_there_is_none():
    app, _ = wired()
    out = said(app, "/stop")

    assert "takes nothing" not in out
    assert "nothing of hers is running out here" in out


# --- /clear ----------------------------------------------------------------------------------------

def _watched(app, monkeypatch) -> tuple[list, list]:
    wiped, printed = [], []
    monkeypatch.setattr(app.screen.console, "clear", lambda *a, **k: wiped.append(True))
    monkeypatch.setattr(app.screen, "header", lambda *a, **k: printed.append(True))
    return wiped, printed


def test_clear_handed_a_word_refuses_it_without_wiping_the_screen_it_would_print_under(monkeypatch):
    """The decision: refuse first. `/clear history` reads as "clear the history" and cleared only the
    glass, in silence — so the sentence has to be legible, and it cannot be legible under a wipe."""
    app, _ = wired()
    wiped, printed = _watched(app, monkeypatch)
    app.screen.row(Text("a row already on the glass"))
    out = said(app, "/clear", "history")

    assert "/clear takes nothing" in out
    assert (wiped, printed) == ([], [])


def test_clear_says_the_session_is_untouched_because_that_is_what_the_word_was_asking():
    app, _ = wired()
    out = " ".join(said(app, "/clear", "history").split())   # the window wraps; the sentence is one

    assert "the session" in out


def test_clear_handed_nothing_still_wipes_the_glass_and_reprints_the_header(monkeypatch):
    app, _ = wired()
    wiped, printed = _watched(app, monkeypatch)
    out = said(app, "/clear")

    assert "takes nothing" not in out
    assert (wiped, printed) == ([True], [True])


# --- the census ------------------------------------------------------------------------------------

def test_bare_is_wired_to_every_command_that_takes_nothing_and_to_no_other():
    """Counted off the source rather than off behaviour, so a seventh handler added without `_bare` —
    or a `_bare` put on a command that has since gained a real argument — fails here rather than at a
    prompt. `/help <part>` and `/helpers <n>` are why this is a census and not a list: an earlier audit
    called them swallowers after they had already grown meanings."""
    source = __import__("inspect").getsource(slash)
    assert set(re.findall(r'_bare\(app, "(/\w+)"', source)) == BARE


@pytest.mark.parametrize("name", sorted(BARE))
def test_each_of_them_answers_a_word_and_changes_nothing(name):
    app, _ = wired()
    a_running_job(app)
    before = (app.caps.reduced_motion, app.screen.plate_mode, app.work.state)
    out = said(app, name, "bogus")

    assert f"{name} takes nothing" in out
    assert (app.caps.reduced_motion, app.screen.plate_mode, app.work.state) == before


@pytest.mark.parametrize("name", ["/help", "/plan", "/face", "/open", "/attach", "/work", "/helpers"])
def test_every_command_outside_the_census_still_uses_the_word_it_is_handed(name):
    """The other half of the census: these take an argument, so none of them may start refusing one."""
    app, _ = wired()
    out = said(app, name, "bogus")

    assert "takes nothing" not in out
