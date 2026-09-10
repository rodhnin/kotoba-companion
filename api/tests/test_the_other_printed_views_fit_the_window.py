"""Four printed views write straight into the transcript with no height budget, and none was measured.

/work draws 69 rows into a 20-row window at 80x24 (40 tools, 12 helpers; 83 at 62x24) — the worst of
them, since it was deliberately slimmed on the promise that `/work {n}` opens the whole of it, and that
command had no budget at all. /plan draws 42 rows for a 40-step list, 82 once steps wrap at 44 columns.
/open draws 61 rows when the ordinal misses, falling back to 30 gifts. /emotions draws three per row
regardless of width, so at 30 columns five intended rows became fifteen. Rows are counted off the
console's own bytes, widths swept 30 to 120: the sizes a listing changes shape at are not round numbers.
FURNITURE (the prompt box and footer) is what these commands cannot reclaim."""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from rich.cells import cell_len

from kotoba.cli import slash, state
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.kaomoji import EMOTIONS
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

FURNITURE = 4
WIDTHS = tuple(range(30, 121))
HEIGHTS = (14, 24, 40)


def _wired(height: int, width: int) -> App:
    caps = Caps(color="none", background="dark", unicode=True, width=width, height=height,
                g=dict(GLYPHS_UNICODE))
    console = build_console(caps, file=io.StringIO())
    console.width = caps.width
    screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1",
                                  engine=SimpleNamespace(db=None, mcp=None),
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app


def _printed(app, name: str, arg: str = "") -> list[str]:
    asyncio.run(slash.run(app, commands.Command(name, arg)))
    return [r.rstrip() for r in app.screen.console.file.getvalue().split("\n")[:-1]]


def _job(tools: int, helpers: int, gifts: int = 0, *, state_: str = "ok") -> state.Work:
    job = state.Work("look into the thing properly and write up what you find", n=1, state=state_,
                     summary="she found it, wrote it down and put the file where you can open it")
    for i in range(tools):
        tool = state.Tool("read", f"/home/jordan/work/notes/file-{i:02d}.md", state="ok",
                          detail="12 lines")
        tool.started, tool.stopped = 0.0, 1.4
        job.tools.append(tool)
    job.helpers = [state.Helper(f"h{i}", "researcher", f"goal number {i + 1}", state="ok",
                                steps=[(f"step {k + 1}", True) for k in range(3)],
                                summary="it went the way she said it would, at some length")
                   for i in range(helpers)]
    job.gift_ns = list(range(1, gifts + 1))
    return job


def _gifts(n: int) -> list[state.Gift]:
    return [state.Gift("report", f"/home/jordan/.kotoba/files/reports/report-{i:02d}.md",
                       "what she found out about the thing, at some length") for i in range(1, n + 1)]


def _plan(steps: int, active: int = 0) -> state.Plan:
    return state.Plan({"list_id": "L1", "status": "open", "title": "look into the thing properly",
                       "tasks": [{"order": i + 1,
                                  "text": f"step number {i + 1}, which has a name of its own",
                                  "status": "active" if i + 1 == active else "pending"}
                                 for i in range(steps)]})


def _overflows(build, name: str, arg: str = "") -> list[str]:
    """Every (width, height) the view came back taller than the window at, with its row count."""
    over = []
    for width in WIDTHS:
        for height in HEIGHTS:
            app = _wired(height, width)
            build(app)
            rows = _printed(app, name, arg)
            if len(rows) + FURNITURE > height:
                over.append(f"{width}x{height}: {len(rows)} rows into {height - FURNITURE}")
    return over


def _seen(rows: list[str], want: str) -> bool:
    return any(want in row for row in rows)


# --- the long job's receipt --------------------------------------------------------------------------

def test_work_fits_every_window_it_is_opened_in():
    over = _overflows(lambda app: setattr(app, "works", [_job(40, 12)]), "/work")
    assert not over, f"/work overflowed at {len(over)} sizes: {over[:6]}"


def test_work_fits_when_the_job_handed_over_a_pile_of_files():
    def build(app):
        app.works, app.gifts = [_job(8, 2, gifts=30)], _gifts(30)
    over = _overflows(build, "/work")
    assert not over, f"/work with 30 gifts overflowed at {len(over)} sizes: {over[:6]}"


@pytest.mark.parametrize("width,height", ((80, 24), (62, 24), (44, 24), (30, 24), (100, 40)))
def test_work_keeps_both_brackets_round_what_it_drew(width, height):
    """A job that ended draws its closing bracket whatever the budget did to the middle. The opening
    mark on its own is the one thing this vocabulary may never say of a finished job — a bracket that
    never closes reads as still running (`render/rows.work_row`)."""
    app = _wired(height, width)
    app.works = [_job(40, 12)]
    rows = _printed(app, "/work")
    assert sum(1 for row in rows if "WORK" in row) >= 2, (
        f"a bracket is missing at {width}x{height}: {rows}")


@pytest.mark.parametrize("width,height", ((80, 24), (62, 24), (30, 24)))
def test_work_says_how_much_of_the_job_it_could_not_draw(width, height):
    app = _wired(height, width)
    app.works = [_job(40, 12)]
    rows = _printed(app, "/work")
    assert _seen(rows, "not drawn") or _seen(rows, "more"), (
        f"/work dropped 40 tools and 12 helpers without saying so at {width}x{height}: {rows}")


def test_work_that_fits_is_drawn_whole_and_says_nothing():
    app = _wired(40, 80)
    app.works = [_job(3, 1)]
    rows = _printed(app, "/work")
    assert not _seen(rows, "not drawn"), f"a job that fits was told it was cut: {rows}"
    assert sum(1 for row in rows if "file-0" in row) == 3, f"a tool went missing: {rows}"


# --- the task list on demand ------------------------------------------------------------------------

def test_plan_fits_every_window_it_is_opened_in():
    over = _overflows(lambda app: setattr(app, "plan", _plan(40, active=25)), "/plan")
    assert not over, f"/plan overflowed at {len(over)} sizes: {over[:6]}"


@pytest.mark.parametrize("width,height", ((80, 24), (62, 24), (44, 24)))
def test_plan_keeps_its_head_and_says_where_she_is(width, height):
    """The head carries `done of total`, and the note carries the step she is on — which is the row a
    40-step list at 24 rows has just put out of reach."""
    app = _wired(height, width)
    app.plan = _plan(40, active=25)
    rows = _printed(app, "/plan")
    assert _seen(rows, "PLAN"), f"the plan's head is off the screen at {width}x{height}: {rows}"
    assert _seen(rows, "step 25"), f"the step she is on is not named at {width}x{height}: {rows}"


# --- what she handed you ----------------------------------------------------------------------------

def test_open_fits_every_window_it_falls_back_to_listing_in():
    def build(app):
        app.gifts = _gifts(30)
    over = _overflows(build, "/open", "99")
    assert not over, f"/open's listing overflowed at {len(over)} sizes: {over[:6]}"


@pytest.mark.parametrize("width,height", ((80, 24), (62, 24), (30, 24)))
def test_open_keeps_the_numbers_it_prints_pointing_at_the_rows_beside_them(width, height):
    app = _wired(height, width)
    app.gifts = _gifts(30)
    rows = _printed(app, "/open", "99")
    assert _seen(rows, "/open 1"), f"gift 1 is off the screen at {width}x{height}: {rows}"
    assert _seen(rows, "not drawn") or _seen(rows, "more"), (
        f"/open dropped gifts without saying so at {width}x{height}: {rows}")


# --- the faces ---------------------------------------------------------------------------------------

def test_emotions_fits_every_window_it_is_opened_in():
    over = _overflows(lambda app: None, "/emotions")
    assert not over, f"/emotions overflowed at {len(over)} sizes: {over[:6]}"


def _faces(app) -> list[str]:
    out = []
    for name in EMOTIONS:
        app.screen.face.set(name, instant=True)
        out.append(app.screen.face.still())
    app.screen.face.set("neutral", instant=True)
    return out


def test_no_face_is_ever_broken_across_two_rows():
    """The column count is MEASURED, the way the two column widths already are. `if i % 3 == 0` drew
    three cells of 24 into a 28-cell window, and the terminal folded the third one wherever it liked —
    `( ^ω^` on one row and `)    happy` on the next, from the command whose whole job is to show the
    faces.

    A folded row is never WIDER than the window, which is exactly why measuring the printed line's
    width proves nothing: the fold has already happened by the time the bytes exist. What it leaves
    behind is a row that does not begin on a face, and that is what is asked here."""
    broken = []
    for width in WIDTHS:
        app = _wired(40, width)
        faces = _faces(app)
        for row in _printed(app, "/emotions"):
            if row.strip() and not row.startswith(tuple(faces)) and "not drawn" not in row:
                broken.append(f"{width}: {row!r} does not start on a face")
    assert not broken, f"{len(broken)} rows were folded mid-face: {broken[:6]}"


def test_the_faces_and_their_names_line_up_in_columns():
    """Both columns were already measured and never counted (`( ￣_￣ )?` draws ten cells, `( ･︵･ )`
    is seven characters that draw eight). Cells here too, for the same reason: a name column that
    starts at a different x on each row is the one thing this grid exists to avoid."""
    ragged = []
    for width in WIDTHS:
        app = _wired(40, width)
        lines = [row for row in _printed(app, "/emotions") if row.strip() and "not drawn" not in row]
        stops = [[cell_len(row[:row.index(name)]) for name in EMOTIONS if name in row]
                 for row in lines]
        full = max(stops, key=len) if stops else []
        for row, at in zip(lines, stops):
            if at != full[:len(at)]:
                ragged.append(f"{width}: {row!r} starts its names at {at}, not {full[:len(at)]}")
    assert not ragged, f"{len(ragged)} rows did not square up: {ragged[:6]}"


@pytest.mark.parametrize("width", (30, 44, 62, 80, 120))
def test_emotions_shows_every_face_when_the_window_can_hold_them(width):
    app = _wired(40, width)
    rows = _printed(app, "/emotions")
    missing = [name for name in EMOTIONS if not _seen(rows, name)]
    assert not missing, f"at {width}x40 these faces were never drawn: {missing}"


def test_emotions_says_what_a_short_window_held_back():
    app = _wired(12, 30)
    rows = _printed(app, "/emotions")
    assert _seen(rows, "not drawn") or _seen(rows, "more"), (
        f"faces went missing with nothing said: {rows}")
