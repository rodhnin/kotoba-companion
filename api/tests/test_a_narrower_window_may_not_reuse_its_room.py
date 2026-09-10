"""A room measured on a wider window may not be offered back on a narrower one.

What is kept between frames is `_min_available_height`, a number about where the CURSOR sits, not
about window height. A terminal that rewraps its scrollback on a narrower window pushes the cursor
down, leaving fewer rows under it than the last frame measured — so the frame is drawn to a row
that is not there: her header scrolling away.

The defect is that the offer does not DEPEND on an actual rewrap, so the terminal's own answer is
handed in directly. Bytes must come off a real vt100 output — `DummyOutput`'s cursor moves are
no-ops, so a depth read off one only counts newlines down with no move back up, too deep."""
from __future__ import annotations

import asyncio
import io
import re

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from conftest import needs_posix_terminal
from kotoba.cli.input.prompt import Prompt
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.theme import GLYPHS_UNICODE

pytestmark = needs_posix_terminal

ENTER = "\r"
TOP = "┏"
ROWS = 40
COLS = 80
TOP_ROW = 24               # the frame's top border, so 17 rows of window stand under it
ROOM = ROWS - TOP_ROW + 1
REWRAPPED = 30             # where a rewrap on a narrower window left the same cursor
MOVE = re.compile(r"\x1b\[(\d*)([ABCD])")


def tty_caps() -> Caps:
    return Caps(color="none", background="dark", unicode=True, interactive=True,
                width=COLS, g=dict(GLYPHS_UNICODE))


class Vt100Recorder(Vt100_Output):
    """A real vt100 output into a StringIO, resizable — every escape the renderer emits, kept."""

    def __init__(self, rows: int = ROWS, cols: int = COLS) -> None:
        self.rows, self.cols = rows, cols
        self.sink = io.StringIO()
        super().__init__(self.sink, lambda: Size(rows=self.rows, columns=self.cols),
                         term="xterm-256color", enable_cpr=False)

    def text(self) -> str:
        return self.sink.getvalue()

    def clear_log(self) -> None:
        self.sink = self.stdout = io.StringIO()

    def frames(self) -> int:
        return self.text().count(TOP)


def depth(stream: str) -> int:
    """The deepest row below the layout origin the stream writes to."""
    y = deepest = i = 0
    while i < len(stream):
        if stream.startswith("\r\n", i):
            y += 1
            deepest = max(deepest, y)
            i += 2
            continue
        m = MOVE.match(stream, i)
        if m:
            n = int(m.group(1) or 1)
            if m.group(2) == "A":
                y = max(0, y - n)
            elif m.group(2) == "B":
                y += n
                deepest = max(deepest, y)
            i = m.end()
            continue
        i += 1
    return deepest


async def _settled(prompt: Prompt):
    app = prompt.session.app
    await asyncio.sleep(0.05)
    app.renderer.report_absolute_cursor_row(TOP_ROW)
    app.invalidate()
    await asyncio.sleep(0.05)
    return app


async def _after_a_width_change(cols: int, answer: int | None = None):
    """One width-only resize on a settled prompt, with the answer the terminal would have given if it
    had been asked. -> (what pinned_room offered, frames drawn, deepest row written)."""
    with create_pipe_input() as pipe:
        out = Vt100Recorder()
        prompt = Prompt(tty_caps(), complete_while_typing=False, input=pipe, output=out)
        asking = asyncio.create_task(prompt.ask_async())
        app = await _settled(prompt)
        out.clear_log()
        out.cols = cols
        offered = prompt.pinned_room()
        app._on_resize()
        await asyncio.sleep(0.05)
        if answer is not None:
            app.renderer.report_absolute_cursor_row(answer)
            app.invalidate()
            await asyncio.sleep(0.05)
        got = (offered, out.frames(), depth(out.text()))
        pipe.send_text("bye" + ENTER)
        await asyncio.wait_for(asking, 5)
        return got


def test_a_narrower_window_goes_back_to_asking():
    """The window lost columns, so the transcript above the cursor may have gained rows. How many is
    the terminal's business and only the terminal's, exactly as with a window that changed height."""
    offered, _, _ = asyncio.run(_after_a_width_change(COLS - 12))
    assert offered == 0, f"a narrower window may not re-offer {offered} rows"


def test_a_wider_window_keeps_the_room_it_stood_in():
    """Kept, and it has to be: rejoined lines pull the cursor UP, so the remembered room can only be
    too SMALL there, and too small never writes past the last row. This is the resize the pin was
    written for and it still costs one frame."""
    offered, frames, _ = asyncio.run(_after_a_width_change(COLS + 16))
    assert offered == ROOM
    assert frames == 1, f"one frame per resize, got {frames}"


def test_a_narrowing_resize_writes_no_row_below_the_last_one():
    """The whole point, in bytes. The terminal rewrapped and the cursor now stands at row 30 of 40, so
    11 rows are left under it. Anything written below row 10 is a newline at the bottom of the window,
    which is her header going up the chimney."""
    left = ROWS - REWRAPPED + 1
    _, _, wrote = asyncio.run(_after_a_width_change(COLS - 12, answer=REWRAPPED))
    assert wrote + 1 <= left, f"wrote down to row {wrote} where only {left} rows were left"
