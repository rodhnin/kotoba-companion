"""A printed view never emits more rows than the window has, and always keeps its heading.

Five commands print a listing into the transcript, and one taller than the window puts its FIRST
rows in the scrollback — exactly the half somebody came for. `/settings` lost every key `/set` can
change, and `/sessions` lost the four NEWEST conversations off a newest-first list.

FURNITURE — the three-row prompt box and its footer, redrawn under whatever landed — is what a
command cannot print; everything else is emitted and counted here. Counted off the console's own
bytes, not a row list, since a row wider than the window folds into two — which renderables alone
would miss."""
from __future__ import annotations

import re
import asyncio
import io
from types import SimpleNamespace

import pytest

from kotoba.cli import slash, state
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console
from kotoba.db.database import Database

FURNITURE = 4
#: (columns, rows). 80x24 is the classic default and the size the defect was measured at;
#: 14 rows and 44 columns are where a section stops fitting whole and a row starts wrapping.
SIZES = ((80, 24), (80, 30), (80, 14), (60, 24), (44, 24), (100, 40))


def _wired(height: int, width: int = 80) -> App:
    caps = Caps(color="none", background="dark", unicode=True, width=width, height=height,
                g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    console = build_console(caps, file=buf)
    console.width = caps.width
    screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1",
                                  engine=SimpleNamespace(db=None, mcp=None),
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app


def _printed(app, name: str, arg: str = "", *, seed=None) -> list[str]:
    """The rows the command wrote. The database is opened, filled and closed inside the one loop the
    command runs in: an aiosqlite connection outliving the loop that made it fails on the way out."""
    async def go():
        if seed is None:
            return await slash.run(app, commands.Command(name, arg))
        db = await seed()
        app.session.engine.db = db
        try:
            return await slash.run(app, commands.Command(name, arg))
        finally:
            await db.close()

    asyncio.run(go())
    return app.screen.console.file.getvalue().split("\n")[:-1]


def _fits(rows: list[str], height: int, heading: str) -> None:
    body = [r.rstrip() for r in rows]
    assert len(body) + FURNITURE <= height, (
        f"{len(body)} rows printed into a {height}-row window "
        f"({height - FURNITURE} is all there is): {body[:3]} … {body[-3:]}")
    assert any(heading in r for r in body), f"the {heading!r} heading is not on the screen"


def _seed(tmp_path, *, sessions: int = 0, grants: int = 0):
    async def go() -> Database:
        db = Database("sqlite:///" + str(tmp_path / "fits.db"))
        await db.connect()
        for i in range(sessions):
            await db.ensure_session(f"seed{i:03d}")
            await db.insert_turn(f"seed{i:03d}", "user",
                                 f"#{i + 1} the conversation we had about a thing")
        for i in range(grants):
            await db.save_approved_command(f"cmd{i:02d}", "command")
        return db

    return go


def _helpers(count: int, steps: int) -> list:
    return [state.Helper(f"h{i}", "researcher", f"goal number {i + 1}", state="ok",
                         steps=[(f"step {k + 1} of the {i + 1}th helper", True) for k in range(steps)],
                         summary="it went the way she said it would, at some length, twice over")
            for i in range(count)]


@pytest.mark.parametrize("width,height", SIZES)
def test_help_keeps_its_commands_on_the_screen(width, height):
    _fits(_printed(_wired(height, width), "/help"), height, "C O M M A N D S")


@pytest.mark.parametrize("width,height", SIZES)
def test_settings_keeps_the_keys_set_can_change(tmp_path, width, height):
    rows = _printed(_wired(height, width), "/settings", seed=_seed(tmp_path))
    _fits(rows, height, "B R A I N")
    assert any("provider" in r for r in rows), "the first thing /set changes is off the screen"


@pytest.mark.parametrize("width,height", SIZES)
def test_a_section_that_adds_a_sentence_is_measured_not_guessed(tmp_path, width, height):
    """The one row in a section that WRAPS, at every width the others are checked at.

    `/settings` budgeted a flat 3 for the line a section prints under its rows, and nothing here ever
    saw it: the seed had no grants and no pending MCP, so the branch that prints it never ran. Measured,
    the SECURITY sentence is 4 rows at 24 columns and 2 at 80 — a guess that is wrong at both ends, and
    the cheap end is the expensive one, because over-budgeting at the width most people run makes the
    listing give up a section it had the room to draw."""
    rows = _printed(_wired(height, width), "/settings", "security",
                    seed=_seed(tmp_path, grants=3))
    _fits(rows, height, "S E C U R I T Y")


def test_the_measured_note_keeps_a_section_the_window_holds(tmp_path):
    """The flat 3 was wrong in both directions and the cheap end is the expensive one: at 80 columns
    the SECURITY sentence wraps to 2, so guessing 3 planned that section one row bigger than the one
    it draws — and at 80x40 that row is exactly the section boundary, so /settings gave up SECURITY,
    the section the seeded grants live in, with eleven rows of window left blank under the note."""
    rows = _printed(_wired(40, 80), "/settings", seed=_seed(tmp_path, grants=3))
    _fits(rows, 40, "S E C U R I T Y")


#: Narrower than any window this file used to check. 27 is where `Screen.rw` stopped exceeding the
#: terminal, so 26 and everything under it drew every row folded in two.
NARROW = (16, 20, 24, 26, 27, 29, 33)


@pytest.mark.parametrize("width", NARROW)
def test_one_row_is_one_line(tmp_path, width):
    """The fold, named as what it is: a row that becomes two lines makes every height budget wrong.

    Rich does not CLIP a row wider than the window, it FOLDS it, so the harm never shows up as a line
    over the width — it shows up as a line that was not planned. `Screen.rw` took its floor of 24 from
    `w`, where it belongs to her prose staying readable, and every window under 27 columns therefore
    measured its rows at 27 and folded every one of them. Measured at 24 before the clamp: the SECURITY
    listing planned 11 rows and drew 14. Nothing above caught it because the narrowest window this file
    ever checked was 44.

    Counted off the console's own bytes, one row at a time, so a failure names the row that folded."""
    from kotoba.core import app_settings

    app, values = _wired(60, width), app_settings.runtime_all()
    for key in ("sandbox", "model"):
        app.screen.console.file.truncate(0)
        app.screen.console.file.seek(0)
        app.screen.row(slash._setting_row(app, key, values))
        lines = app.screen.console.file.getvalue().split("\n")[:-1]
        assert len(lines) == 1, (
            f"the {key!r} row folded into {len(lines)} lines in a {width}-column window: {lines}")


@pytest.mark.parametrize("width", NARROW)
def test_the_measure_a_row_is_fitted_to_is_one_the_console_can_print(width):
    """The property behind the test above, asked of `rw` directly so a regression names its own cause."""
    app = _wired(60, width)
    assert app.screen.rw <= width, (
        f"rows are measured at {app.screen.rw} cells for a {width}-column terminal")


def test_the_sentence_the_budget_is_about_does_print(tmp_path):
    """The guard on the guard: the case above is only worth its parametrization if the branch runs.

    It cannot be asserted at every size — at 80x14 the budget correctly gives the section up and draws
    the partial form, which prints no sentence at all — so it is asked for once, in a window with room
    to spare."""
    rows = _printed(_wired(40, 100), "/settings", "security", seed=_seed(tmp_path, grants=3))
    assert any("/approvals rm" in r for r in rows), "the row the budget is about never printed"


@pytest.mark.parametrize("width,height", SIZES)
def test_sessions_keeps_the_newest_ones(tmp_path, width, height):
    rows = _printed(_wired(height, width), "/sessions", seed=_seed(tmp_path, sessions=20))
    _fits(rows, height, "S E S S I O N S")
    assert any("#20" in r for r in rows), "the newest conversation is off the screen"


@pytest.mark.parametrize("width,height", SIZES)
def test_approvals_is_bounded_when_there_is_real_data(tmp_path, width, height):
    rows = _printed(_wired(height, width), "/approvals", seed=_seed(tmp_path, grants=25))
    _fits(rows, height, "A L W A Y S   A L L O W E D")
    # re-recorded: every grant draws as `rows.grant_rows` now — the ordinal at `GRANT_COL`, so
    # `1.  cmd00` — and what this asserts is the grant, never its spacing.
    assert any(re.match(r"^1\.\s+cmd00\b", r) for r in rows), (
        "the grant /approvals rm 1 names is off the screen")


@pytest.mark.parametrize("width,height", SIZES)
def test_helpers_is_bounded_by_the_line_up_and_by_one_helpers_steps(width, height):
    app = _wired(height, width)
    app.last_helpers = _helpers(12, 8)
    _fits(_printed(app, "/helpers"), height, "01")

    app = _wired(height, width)
    app.last_helpers = _helpers(1, 60)
    _fits(_printed(app, "/helpers", "1"), height, "01")
