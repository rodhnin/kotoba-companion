"""Two columns must never touch, and no row may be wider than the window it was drawn for.

Two defects are the same arithmetic mistake: a column padded with a subtraction that could reach
zero let the separator vanish once content filled the field — `researcher` (ten chars) drew
`researchergoal number 1`, one word on screen and two on the wire, and a `/sessions` row measured 39
cells in a 36-column terminal folded onto a line of its own — one table row eating two screen rows.

The third has no arithmetic at all: the helper nameplate is thirty cells before its goal starts, and
four surfaces printed it at any width with no gate; `helper_rows`/`helper_height` are that gate now.
Everything is measured in CELLS, since a kaomoji or CJK filename silently doubles a character count."""
from __future__ import annotations

import asyncio
import io
import re
from types import SimpleNamespace

import pytest
from rich.cells import cell_len

from kotoba.cli import slash, state
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render import rows
from kotoba.cli.render.cards import EDGE, info_row, setting_row
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console
from kotoba.db.database import Database

WIDTHS = range(30, 121)
SGR = re.compile(r"\x1b\[[0-9;]*m")

#: Every role `subagent_spawned` can name — `delegate`'s toolset enum and the `helper` App falls back
#: to — then the ones that broke it: ten characters exactly, eleven, a runaway, a wide script, a face.
ROLES = ("research", "web", "file", "code", "browser", "helper",
         "researcher", "orchestrator", "a-toolset-name-that-is-far-too-long-to-be-a-rank",
         "日本語の役割", "( ^ω^ )ノ")
GOALS = ("goal number 1", "写真フォルダを整理して", "( ^ω^ )ノ look this one up")

#: (name, value, metric) in the shapes the four callers of these rows actually produce, then the wide
#: ones. `2 turns · this one` is the /sessions tail that stayed over the window up to 51 columns.
CELLS = (("today 10:19", '"#6 the conversation we had"', "2 turns · this one"),
         ("today 10:19", '"#6 the …"', "2 turns"),
         ("1. cmd00", "runs without asking", ""),
         ("model", "gpt-5.4-mini", "any name"),
         ("reasoning_effort", "off", "off · minimal · low · medium · high · xhigh · max"),
         ("elevenlabs_agent_id", "", "any name"),
         ("写真フォルダの整理", "日本語の値", "日本語のしっぽ"),
         ("( ^ω^ )ノ", "( ^ω^ )ノ", "( ^ω^ )ノ"),
         ("a-name-that-is-far-longer-than-the-column-it-was-given", "v", "t"))


def _caps(width: int) -> Caps:
    return Caps(color="none", background="dark", unicode=True, encodes_unicode=True,
                width=width, height=40, g=dict(GLYPHS_UNICODE))


def _helper(role: str, goal: str) -> state.Helper:
    return state.Helper("h1", role, goal, state="ok", steps=[("looked it up", True)],
                        started=1.0, stopped=4.2)


def _gap_after(plain: str, first: str, second: str) -> int:
    """Cells between the end of `first` and the start of `second` on the drawn row."""
    at = plain.index(first) + len(first)
    return plain.index(second, at) - at


# ── the role and the goal ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("goal", GOALS)
def test_a_role_never_welds_itself_to_the_goal(role, goal):
    plain = rows.helper_text(_caps(96), _helper(role, goal), 1, 94).plain
    assert _gap_after(plain, "( ^ω^ )", goal) >= 1, plain
    assert role[:4] + goal[:4] not in plain, f"the rank and the goal are one word: {plain!r}"


def test_every_role_the_wire_can_name_starts_the_goal_on_one_column():
    """The column is kept, not widened: `ROLE_COL` holds every toolset `delegate`'s enum names, so a
    line-up of real helpers reads as a table."""
    at = {rows.helper_text(_caps(96), _helper(r, "goal number 1"), 1, 94).plain.index("goal number 1")
          for r in ROLES[:6]}
    assert len(at) == 1, f"the goal starts on {len(at)} different columns: {sorted(at)}"


@pytest.mark.parametrize("width", [w for w in WIDTHS if w >= rows.ROSTER_MIN_W])
@pytest.mark.parametrize("role", ROLES)
def test_a_runaway_role_cannot_push_the_row_off_the_window(role, width):
    """`ROSTER_MIN_W` is where the line-up is drawn at all — under it the nameplate alone is wider than
    the window and `roster_rows` folds to one row instead."""
    row = rows.helper_text(_caps(width + 2), _helper(role, GOALS[0]), 1, width)
    assert row.cell_len <= width, f"{row.cell_len} cells at {width}: {row.plain!r}"


# ── the listing rows ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("width", WIDTHS)
def test_no_listing_row_outgrows_the_window(width):
    for left, right, tail in CELLS:
        for row in (info_row(_caps(width), left, right, tail, width),
                    setting_row(_caps(width), left, right, "", tail, width)):
            assert row.cell_len <= width - EDGE, (
                f"{row.cell_len} cells in a {width}-column window: {row.plain!r}")


@pytest.mark.parametrize("name", ("k", "a-name-of-19-chars.", "a-name-of-exactly-20",
                                  "a-21-cell-name-here.", "写真フォルダの整理", "( ^ω^ )ノ",
                                  "a-name-that-is-far-longer-than-the-column-it-was-given"))
def test_a_listing_row_never_welds_its_name_to_its_value(name):
    """Whatever the name does to the column, the cell before the value is a space. `f"{name:<20}"` had
    none left to give the moment the name reached twenty."""
    for width in (34, 40, 64, 96):
        for row in (info_row(_caps(width), name, "VALUE", "2 turns", width),
                    setting_row(_caps(width), name, "VALUE", "", "2 turns", width)):
            at = row.plain.index("VALUE")
            assert at and row.plain[at - 1] == " ", f"welded at {width}: {row.plain!r}"


def test_the_value_column_is_measured_in_cells_not_characters():
    """A nine-character CJK name is eighteen cells. Padded by CHARACTER it filled the field twice over
    and started the value column nine cells right of every other row's."""
    starts = {cell_len(info_row(_caps(96), name, "VALUE", "", 94).plain.split("VALUE")[0])
              for name in ("session", "写真フォルダの整理", "( ^ω^ )ノ")}
    assert starts == {20}, f"the value column starts on {sorted(starts)}, not one column"


# ── a real command, wired to a real screen ───────────────────────────────────────────────────────

def _wired(width: int, height: int = 40) -> App:
    caps = _caps(width)
    caps.height = height
    console = build_console(caps, file=io.StringIO())
    console.width = caps.width
    screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1",
                                  engine=SimpleNamespace(db=None, mcp=None),
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app


PLATE_SITES = ("/work", "/helpers", "commit_roster", "land_work")
FURNITURE = 4   # the prompt box and its footer, redrawn under whatever a printed view landed


def _drive(app, site: str) -> None:
    """One line-up, through each of the four entry points that print a plate."""
    job = state.Work("look into the price of a GPU", n=1, state="ok")
    job.helpers = [_helper(role, goal) for role, goal in zip(ROLES, GOALS)]
    job.summary = "two shops, one price"
    if site == "/work":
        app.work, app.works = job, [job]
        slash._work(app, "")
    elif site == "/helpers":
        app.last_helpers = job.helpers
        asyncio.run(slash.run(app, commands.Command("/helpers", "")))
    elif site == "commit_roster":
        app.helpers = list(job.helpers)
        app._commit_roster()
    else:
        job.helpers[1].state = "failed"
        app.work = job
        app._land_work(parting=True)


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("role", ROLES)
def test_a_plate_fits_the_window_at_every_width(role, width):
    """The other half of `test_a_runaway_role_…`: under `ROSTER_MIN_W` the plate does not fit on one
    row at all, and `helper_rows` is where that is decided instead of at four call sites."""
    for goal in GOALS:
        for row in rows.helper_rows(_caps(width + 2), _helper(role, goal), 1, width):
            assert row.cell_len <= width, f"{row.cell_len} cells at {width}: {row.plain!r}"


def test_a_folded_plate_keeps_the_rank_the_state_and_the_goal():
    """Two rows, and nothing that names the helper is what the fold spends: the number `/helpers 2`
    opens it by, the rank off the wire, and the goal it was sent out on."""
    narrow = rows.helper_rows(_caps(32), _helper("research", "goal number 1"), 2, 30)
    assert (len(narrow), rows.helper_height(30)) == (2, 2)
    assert "02" in narrow[0].plain and "research" in narrow[0].plain
    assert narrow[1].plain.strip() == "goal number 1"
    assert len(rows.helper_rows(_caps(96), _helper("research", "goal number 1"), 2, 94)) == 1
    assert rows.helper_height(94) == 1


@pytest.mark.parametrize("width", (30, 36, 44, 51, 61, 62, 63, 64, 80, 120))
@pytest.mark.parametrize("site", PLATE_SITES)
def test_every_surface_that_prints_a_plate_goes_through_the_fold(monkeypatch, site, width):
    """A renderable wider than its measure is not clipped by rich, it is FOLDED — so the plate is caught
    here while it is still one object, and again on the screen, where it must still be one line."""
    app = _wired(width)
    seen: list = []
    real = app.screen.row
    monkeypatch.setattr(app.screen, "row", lambda r: (seen.append(r), real(r))[1])
    _drive(app, site)
    assert seen, f"{site} printed nothing at {width}"
    for row in seen:
        assert row.cell_len <= app.screen.rw, (
            f"{site}: {row.cell_len} cells into a {app.screen.rw}-cell measure "
            f"at {width} columns: {row.plain!r}")
        assert app.screen.rows_of(row) == 1, (
            f"{site}: one row landed on {app.screen.rows_of(row)} lines at {width}: {row.plain!r}")


@pytest.mark.parametrize("width,height,count,steps",
                         ((30, 24, 4, 0), (30, 28, 3, 2), (36, 24, 4, 0), (40, 20, 3, 0),
                          (44, 28, 3, 3), (50, 28, 3, 3)))
def test_the_helpers_budget_counts_a_folded_plate_as_two_rows(width, height, count, steps):
    """`listing.plan` budgets in rows and a folded plate is two of them. Counted as one — which is what
    both the unfolded plate and a fold nobody told the budget about come to — `/helpers` drew
    twenty-two rows into the twenty a 24-row window has."""
    app = _wired(width, height)
    app.last_helpers = [
        state.Helper(f"h{i}", "researcher", f"goal number {i + 1} in a few more words", state="ok",
                     steps=[(f"step {k + 1} of the {i + 1}th helper", True) for k in range(steps)],
                     summary="it went the way she said it would, at some length, twice over",
                     started=1.0, stopped=9.5)
        for i in range(count)]
    asyncio.run(slash.run(app, commands.Command("/helpers", "")))
    drawn = app.screen.console.file.getvalue().split("\n")[:-1]
    assert len(drawn) + FURNITURE <= height, (
        f"{len(drawn)} rows into the {height - FURNITURE} a {height}-row window has: {drawn}")


# ── the whole view, through the real command ─────────────────────────────────────────────────────

@pytest.mark.parametrize("width", (30, 36, 44, 51, 80))
def test_sessions_prints_one_screen_row_per_conversation(tmp_path, width):
    """The symptom, end to end: over the window the row is not clipped, it is FOLDED, and a listing
    planned in renderables then draws twice the rows it budgeted."""
    app = _wired(width)

    async def go():
        db = Database("sqlite:///" + str(tmp_path / f"rows{width}.db"))
        await db.connect()
        for i in range(4):
            await db.ensure_session(f"seed{i:03d}")
            await db.insert_turn(f"seed{i:03d}", "user", f"#{i + 1} the conversation about a thing")
            await db.insert_turn(f"seed{i:03d}", "assistant", "sure")
        app.session.engine.db = db
        try:
            await slash.run(app, commands.Command("/sessions", ""))
        finally:
            await db.close()

    asyncio.run(go())
    drawn = [SGR.sub("", r).rstrip() for r in app.screen.console.file.getvalue().split("\n")[:-1]]
    assert sum(1 for r in drawn if "today" in r or " 20" in r) >= 1, drawn
    for r in drawn:
        assert cell_len(r) <= width, f"{cell_len(r)} cells at {width}: {r!r}"
    assert not [r for r in drawn if r.strip() in ("turns", "turn")], (
        f"a row's metric folded onto a line of its own: {drawn}")
