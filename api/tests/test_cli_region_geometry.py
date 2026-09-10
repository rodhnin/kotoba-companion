"""The three ways the moving picture used to lose the frame, pinned.

A) rich crops an oversized region at its LAST rows (input frame, bar); it now crops the OLDEST
rows, which the terminal scrolls away anyway.
B) `w` comes off `caps.width` while rich asks the terminal at every print; disagreement reduces
a too-wide grid on both columns, narrowing the gutter and misindenting the block in a transcript
that never repaints.
C) A frame that raises must not take the in-turn clock with it: rich only extends its buffer at
the end, so a throw ends the task silently, killing the spinner and every elapsed clock for the
turn."""
from __future__ import annotations

import asyncio
import io
import time

from rich.cells import cell_len
from rich.text import Text

import pytest
from conftest import needs_posix_terminal
from kotoba.cli import state
from kotoba.cli.render import rows
from kotoba.cli.app import App
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.kaomoji import Face
from kotoba.cli.render.markdown import prose
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.region import LiveView
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

WIDTH, HEIGHT = 90, 24


def a_caps(**over) -> Caps:
    base = dict(color="none", background="dark", unicode=True, interactive=True,
                width=WIDTH, height=HEIGHT, g=dict(GLYPHS_UNICODE))
    base.update(over)
    return Caps(**base)


def a_screen(caps: Caps, console_width: int | None = None) -> Screen:
    console = build_console(caps, file=io.StringIO(), force_terminal=True)
    console.width = console_width or caps.width
    console.height = caps.height
    return Screen(caps, console=console, portrait=Portrait(caps, wanted=False))


class P:
    """The row builders the region arranges but does not own."""

    def __init__(self, screen: Screen) -> None:
        self.screen, self.caps = screen, screen.caps
        self.spin = rows.Spin(screen.caps)

    def tool_text(self, tool):
        return rows.tool_text(self.caps, tool, self.screen.rw, spin=self.spin)

    def roster_rows(self):
        return []

    def approval_rows(self):
        return []

    def seated_rows(self):
        return []

    def confirm_rows(self):
        return []

    def head_plate(self, live: bool = False):
        return self.screen.plate(live=live)

    def at_gutter(self, row):
        return self.screen.at_gutter(row)

    def prose(self, block: str):
        return self.screen.at_gutter(prose(block, self.caps))

    def link_rows(self, block: str):
        return []


def region_lines(screen: Screen, st: footer.State) -> list[str]:
    parts = P(screen)
    view = LiveView(screen, parts.spin, lambda: st, parts)
    with screen.console.capture() as cap:
        screen.console.print(view)
    return cap.get().split("\n")


def a_tall_block() -> str:
    """A fence, which is what she actually writes that runs past the bottom of a window: `Blocks` holds
    one until it closes, so it reaches the region whole."""
    body = "\n".join(f"line_{i:02d} = compute({i})" for i in range(34))
    return f"```python\n{body}\n```"


def test_a_block_taller_than_the_window_never_costs_the_frame_or_the_bar():
    screen = a_screen(a_caps())
    screen.reserve = HEIGHT
    st = footer.State(model="m", turn_start=time.monotonic(), partial=a_tall_block())
    lines = region_lines(screen, st)
    assert len(lines) <= HEIGHT, "rich would crop the last rows, which are the frame and the bar"
    assert "..." not in lines[-1], "rich's own ellipsis means the pinned block went with the crop"
    frame = [i for i, line in enumerate(lines) if "┏" in line or "┗" in line]
    assert len(frame) == 2 and frame[-1] == len(lines) - 2, "the box sits on the last rows, whole"
    assert "LIVE" in lines[-1]


def test_the_tail_of_that_block_is_what_survives_the_crop_not_its_head():
    """The oldest rows go: the terminal scrolls them away when the block lands anyway, and the row she
    is typing on is the one worth looking at."""
    screen = a_screen(a_caps())
    screen.reserve = HEIGHT
    lines = region_lines(screen, footer.State(model="m", turn_start=time.monotonic(),
                                              partial=a_tall_block()))
    body = "\n".join(lines)
    assert "line_33" in body and "line_00" not in body


def test_the_region_is_held_to_the_window_so_one_tall_block_does_not_outlive_itself():
    screen = a_screen(a_caps())
    screen.reserve = HEIGHT
    region_lines(screen, footer.State(model="m", turn_start=time.monotonic(),
                                      partial=a_tall_block()))
    assert screen.reserve <= HEIGHT
    short = region_lines(screen, footer.State(model="m", turn_start=time.monotonic(),
                                              partial="ya esta."))
    assert len(short) <= HEIGHT


def test_a_card_under_a_window_too_short_for_it_keeps_the_box():
    caps = a_caps(height=8)
    screen = a_screen(caps)
    screen.reserve = 8
    parts = P(screen)
    parts.approval_rows = lambda: [Text(f"card row {i}") for i in range(12)]
    view = LiveView(screen, parts.spin, lambda: footer.State(
        model="m", turn_start=time.monotonic(), approval=object()), parts)
    with screen.console.capture() as cap:
        screen.console.print(view)
    lines = cap.get().split("\n")
    assert len(lines) <= 8 and "..." not in lines[-1]
    assert "LIVE" in lines[-1] and "┗" in lines[-2]


def test_her_gutter_survives_a_measure_the_console_has_already_outgrown():
    """caps still says 120 while the window is 44: rich reduces every column of an over-wide grid, and
    the gutter is a column."""
    caps = a_caps(width=120)
    screen = a_screen(caps, console_width=44)
    screen.say("Uno, lo que he encontrado hasta ahora en los papeles.", last=True)
    printed = [line for line in screen.console.file.getvalue().split("\n") if line.strip()]
    assert printed, "she said something"
    for line in printed:
        lead = len(line) - len(line.lstrip(" "))
        assert lead >= screen.gutter, (lead, line)
        assert cell_len(line) <= 44, (cell_len(line), line)


@needs_posix_terminal
def test_the_in_turn_clock_survives_a_frame_that_raises():
    caps = a_caps()
    app = App(caps, a_screen(caps), prompt=None)
    tries: list[int] = []

    class Boom:
        def refresh(self) -> None:
            tries.append(1)
            if len(tries) <= 3:
                raise RuntimeError("a frame that throws")

    async def go() -> None:
        app.region = Boom()
        clock = asyncio.create_task(app._clock())
        for _ in range(200):
            await asyncio.sleep(0.005)
            if len(tries) >= 6:
                break
        app.region = None
        clock.cancel()

    asyncio.run(go())
    assert len(tries) >= 6, "the clock stopped at the first throw and the turn stopped moving"


@needs_posix_terminal
def test_the_out_of_turn_beat_never_wakes_for_a_still():
    """Measured through a pty across every out-of-turn state: only the two that MOVE cost anything per
    second, and a still must not wake the prompt at all."""
    caps = a_caps()
    app = App(caps, a_screen(caps), prompt=None)
    assert app._wake() is None
    app.plan = state.Plan({"list_id": "L", "status": "open",
                           "tasks": [{"order": 1, "text": "one", "status": "active"}]})
    assert app._wake() is None, "an open plan is a still"
    app.due = [state.Due("d1", "call your mother", "")]
    assert app._wake() is None, "a reminder is a nudge, not a clock"
    app.work = state.Work("look it up", 1, run_id="R1")
    assert app._wake() == pytest.approx(1.0 / rows.BEAT_HZ), "the long job is the one that moves"


def a_sixel_screen(caps: Caps) -> Screen:
    box = Portrait(caps, wanted=False)
    box.mode, box.cols, box.rows, box.icols, box.irows = "sixel", 12, 5, 12, 5
    box._sixel = lambda emotion, scale: (f"<SIXEL {emotion}>", 12, 5)
    console = build_console(caps, file=io.StringIO(), force_terminal=True)
    console.width, console.height = caps.width, caps.height
    return Screen(caps, console=console, portrait=box)


def test_a_portrait_the_crop_reached_is_not_reported_as_painted():
    """`erase_span` is the committed block's licence to walk its cursor over her column instead of
    spacing across it. Claimed for a face the crop took, her block is composed of moves over an empty
    gutter and lands on nothing."""
    caps = a_caps(sixel=True)
    screen = a_sixel_screen(caps)
    screen.reserve = HEIGHT
    screen.face.set("happy", instant=True)
    st = footer.State(model="m", turn_start=time.monotonic(), partial="una linea corta.")
    assert "<SIXEL" in "".join(region_lines(screen, st)), "she is painted while the block fits"
    assert screen.erase_span and screen.face_art

    tall = footer.State(model="m", turn_start=time.monotonic(), partial=a_tall_block())
    lines = region_lines(screen, tall)
    assert len(lines) <= HEIGHT
    assert screen.erase_span is None and screen.face_art is None
    assert "<SIXEL" not in "".join(lines), "no second sixel goes out for a face nobody can see"


class ADue:
    """A reminder's message, which cron took from her and nobody here wrote."""

    def __init__(self, msg: str) -> None:
        self.msg, self.when, self.every, self.told = msg, "", "", False


EVIL = "call\x01\x02\x03\x04\x05\x06\x07\x08 your mother now"


def test_the_bar_measures_what_it_will_actually_draw_not_what_it_was_handed():
    """A C0 byte costs zero cells and the space `Safe` turns it into costs one. Fitted before the
    scrub, a reminder of 20 cells drew 28, and a bar row wider than the window is one physical row the
    region never counted: the erase leaves a ghost and the frame walks."""
    for width in range(30, 130):
        caps = a_caps(color="truecolor", width=width)
        st = footer.State(due=(ADue(EVIL),), at_rest=True)
        row = footer.bar_text(caps, Face(unicode=True), st)
        assert cell_len(row.plain) <= width, (width, cell_len(row.plain), row.plain)


@needs_posix_terminal
def test_prompt_toolkits_half_of_the_bar_is_scrubbed_the_same_way():
    """It draws a control byte as caret notation — two cells where `cell_len` measured none — so this
    half overflows further than ours, onto a second toolbar row that moves the frame."""
    from prompt_toolkit.layout.screen import Char

    for width in range(40, 130):
        caps = a_caps(color="truecolor", width=width)
        app = App(caps, a_screen(caps), prompt=None)
        app.model = "gpt-5.4-mini · OpenAI"
        app.due = [ADue(EVIL)]
        bar = app._toolbar()
        drawn = sum(Char(ch).width for _style, text in bar for ch in text)
        assert drawn <= width, (width, drawn, bar)
