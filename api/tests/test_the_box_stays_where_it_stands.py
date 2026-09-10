"""The frame is pinned to the foot of the window, so the only honest question is how many times it is
DRAWN and at what height.

prompt_toolkit asks the terminal how much room is under the cursor and draws at its preferred height
until the answer lands — a box a screen too high, then a second one in the right place. At the prompt
that is a lurch on the two most-pressed keys there are; on a resize it is a second frame per step of the
drag; and a frame that must grow at the foot of a window means newlines, which scrolls the header away.

Everything here counts frames in the render stream itself, never what a screen model makes of them.
"""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import needs_posix_terminal
from kotoba.cli.app import App, IDLE_TWINS, LEAVING
from kotoba.cli.input.prompt import Prompt
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

pytestmark = needs_posix_terminal

ENTER = "\r"
TOP = "┏"
ROWS = 40
TOP_ROW = 24            # where the frame's top border stands: 17 rows of window under it


def tty_caps(**kw) -> Caps:
    return Caps(color="none", background="dark", unicode=True, interactive=True,
                width=80, g=dict(GLYPHS_UNICODE), **kw)


class Recorder(DummyOutput):
    """DummyOutput that keeps what was written and can be resized, so a frame can be counted where it
    actually exists: in the bytes the renderer emitted."""

    def __init__(self, rows: int = ROWS, cols: int = 80) -> None:
        self.rows, self.cols = rows, cols
        self.written: list[str] = []

    def get_size(self) -> Size:
        return Size(rows=self.rows, columns=self.cols)

    def write(self, data: str) -> None:
        self.written.append(data)

    def write_raw(self, data: str) -> None:
        self.written.append(data)

    def text(self) -> str:
        return "".join(self.written)

    def frames(self) -> int:
        return self.text().count(TOP)


async def _settled(prompt: Prompt, heights: list[int]):
    """One prompt, up and pinned the way a real terminal pins it: the app asks where the cursor is and
    the answer arrives once."""
    app = prompt.session.app
    app.after_render += lambda a: heights.append(
        a.renderer._last_screen.height if a.renderer._last_screen else 0)
    await asyncio.sleep(0.05)
    app.renderer.report_absolute_cursor_row(TOP_ROW)
    app.invalidate()
    await asyncio.sleep(0.05)
    return app


def test_a_resize_draws_one_frame_and_never_a_short_one():
    """SIGWINCH used to cost two frames: one at the preferred height, drawn where the transcript ends,
    and one at the real height a round trip later. The second is the one that has to grow at the foot of
    the window, and growing there is what scrolled the header off the top.

    The window is dragged WIDER, and that is the whole of what the pin may still answer for: a narrower
    one may have been reflowed under the frame, so it goes back to asking and costs the second frame on
    purpose."""
    async def drive() -> tuple[int, list[int]]:
        with create_pipe_input() as pipe:
            out, heights = Recorder(), []
            prompt = Prompt(tty_caps(), complete_while_typing=False, input=pipe, output=out)
            asking = asyncio.create_task(prompt.ask_async())
            app = await _settled(prompt, heights)
            out.written.clear()
            heights.clear()
            out.cols = 96
            app._on_resize()
            await asyncio.sleep(0.05)
            app.renderer.report_absolute_cursor_row(TOP_ROW)
            app.invalidate()
            await asyncio.sleep(0.05)
            drawn, tall = out.frames(), list(heights)
            pipe.send_text("bye" + ENTER)
            await asyncio.wait_for(asking, 5)
            return drawn, tall

    drawn, heights = asyncio.run(drive())
    assert drawn == 1, f"one frame per resize, got {drawn}"
    assert heights and all(h == ROWS - TOP_ROW + 1 for h in heights), heights


def test_a_prompt_that_reopens_where_it_stood_draws_no_short_frame():
    """An empty `enter` ends one prompt and opens another, and nothing moved in between — so the room
    is the room the last frame already had. Unanswered, the box paints itself just under the transcript
    and drops back to the foot of the window a frame later, which is the jump."""
    async def drive() -> tuple[int, list[int]]:
        with create_pipe_input() as pipe:
            out, heights = Recorder(), []
            prompt = Prompt(tty_caps(), complete_while_typing=False, input=pipe, output=out)
            asking = asyncio.create_task(prompt.ask_async())
            await _settled(prompt, heights)
            pipe.send_text(ENTER)
            assert await asyncio.wait_for(asking, 5) == ""
            room = prompt.pinned_room()
            prompt.below = room
            out.written.clear()
            heights.clear()
            asking = asyncio.create_task(prompt.ask_async())
            await asyncio.sleep(0.05)
            tall = list(heights)
            pipe.send_text("bye" + ENTER)
            await asyncio.wait_for(asking, 5)
            return room, tall

    room, heights = asyncio.run(drive())
    assert room == ROWS - TOP_ROW + 1
    assert heights and all(h == room for h in heights), heights


def test_a_window_that_changed_height_goes_back_to_asking():
    """The one case this may not answer. Whether a shorter window dropped rows off the bottom or
    scrolled them off the top is the terminal's decision and only the terminal knows which."""
    async def drive() -> tuple[int, int]:
        with create_pipe_input() as pipe:
            out, heights = Recorder(), []
            prompt = Prompt(tty_caps(), complete_while_typing=False, input=pipe, output=out)
            asking = asyncio.create_task(prompt.ask_async())
            await _settled(prompt, heights)
            same = prompt.pinned_room()
            out.rows = ROWS - 8
            shorter = prompt.pinned_room()
            pipe.send_text("bye" + ENTER)
            await asyncio.wait_for(asking, 5)
            return same, shorter

    same, shorter = asyncio.run(drive())
    assert same == ROWS - TOP_ROW + 1
    assert shorter == 0


def _app_with_a_box(prompt: Prompt) -> App:
    caps = prompt.caps
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=prompt)
    app.session = SimpleNamespace(ask=None, events=SimpleNamespace(steps={}, settle=lambda: None))
    return app


def test_a_row_printed_between_two_prompts_gives_the_room_back():
    """The room may only be re-offered while the frame has not moved. A committed row moved it down by
    exactly as many rows as it took, and a room claimed too large is the one failure worth avoiding
    here: the terminal would have to scroll to honour it."""
    async def drive() -> tuple[int, int]:
        with create_pipe_input() as pipe:
            out, heights = Recorder(), []
            prompt = Prompt(tty_caps(), complete_while_typing=False, input=pipe, output=out)
            app = _app_with_a_box(prompt)
            asking = asyncio.create_task(prompt.ask_async())
            await _settled(prompt, heights)
            app.screen.printed = 0
            quiet = app._still_pinned()
            app.screen.printed = 3
            after_a_row = app._still_pinned()
            pipe.send_text("bye" + ENTER)
            await asyncio.wait_for(asking, 5)
            return quiet, after_a_row

    quiet, after_a_row = asyncio.run(drive())
    assert quiet == ROWS - TOP_ROW + 1
    assert after_a_row == 0


ABORTED = "<the prompt was aborted>"


def typed(keys: str, secs: float = 5.0) -> str | None:
    """One prompt with every key already in it, and two ceilings. A key that stops working here hangs
    the prompt rather than failing it, and a test that hangs says nothing; and prompt_toolkit's own
    ctrl-c aborts with KeyboardInterrupt, which pytest reads as somebody stopping the run rather than
    as a red test. Both come back as a value the assertions can name."""
    async def drive() -> str | None:
        with create_pipe_input() as pipe:
            pipe.send_text(keys)
            prompt = Prompt(tty_caps(), complete_while_typing=False,
                            input=pipe, output=DummyOutput())
            return await asyncio.wait_for(prompt.ask_async(), secs)

    try:
        return asyncio.run(drive())
    except (KeyboardInterrupt, asyncio.TimeoutError):
        return ABORTED


def test_one_ctrl_c_on_an_empty_box_neither_leaves_nor_eats_the_next_line():
    assert typed("\x03" + "hola" + ENTER) == "hola"


def test_two_ctrl_c_in_a_row_leave():
    assert typed("\x03\x03") is None


def test_a_ctrl_c_between_two_others_does_not_count_as_in_a_row():
    assert typed("\x03" + "hola" + "\x03" + "adios" + ENTER) == "adios"


def test_ctrl_c_with_a_line_in_the_box_clears_it_and_stays():
    assert typed("hola" + "\x03" + "adios" + ENTER) == "adios"


def test_the_band_offers_the_way_out_only_while_a_second_ctrl_c_would_take_it():
    async def drive() -> tuple[str, str]:
        with create_pipe_input() as pipe:
            prompt = Prompt(tty_caps(), complete_while_typing=False,
                            input=pipe, output=Recorder())
            app = _app_with_a_box(prompt)
            asking = asyncio.create_task(prompt.ask_async())
            await asyncio.sleep(0.05)
            prompt.leaving = True
            armed = "".join(t for _, t in app._toolbar())
            prompt.leaving = False
            at_rest = "".join(t for _, t in app._toolbar())
            pipe.send_text("bye" + ENTER)
            await asyncio.wait_for(asking, 5)
            return armed, at_rest

    armed, at_rest = asyncio.run(drive())
    assert any(twin in armed for twin in LEAVING)
    assert not any(twin in at_rest for twin in LEAVING)
    assert any(twin in at_rest for twin in IDLE_TWINS)


def test_the_loop_is_what_hands_the_box_its_room_and_the_room_is_spent_once():
    """Everything above sets `prompt.below` by hand, pinning the shape of the answer but not who gives
    it. The real loop is the only caller: it hands the box the rows the last frame stood in, then zeros
    its own copy in the same breath, because a room offered twice is a room already spent. Dropped,
    every assertion above still passes while every prompt goes back to asking the terminal — the lurch
    this whole file is about.

    RE-RECORDED: the released count is exact only at the moment the region stops. Text printed between
    that moment and the prompt moves the cursor down without moving the number, and the box was pinned
    two rows past the foot. So the count is handed over less whatever grew since it was taken: the same
    9 when nothing did, 7 when two rows did."""
    async def drive() -> tuple[list[int], int]:
        with create_pipe_input() as pipe:
            out, heights = Recorder(), []
            prompt = Prompt(tty_caps(), complete_while_typing=False, input=pipe, output=out)
            app = _app_with_a_box(prompt)
            asking = asyncio.create_task(prompt.ask_async())
            await _settled(prompt, heights)
            pipe.send_text("bye" + ENTER)
            await asyncio.wait_for(asking, 5)

            offered: list[int] = []

            async def leaves(pre_run=None) -> None:
                """ctrl-d on an empty line: the one answer that ends `_loop` on its first pass."""
                offered.append(prompt.below)
                return None

            prompt.ask_async = leaves
            for printed, released, at_release in ((0, 0, 0), (3, 0, 0), (3, 9, 3), (5, 9, 3)):
                prompt.below = -1
                app.screen.printed, app.room, app._room_at = printed, released, at_release
                assert await app._loop() == 0
            return offered, app.room

    offered, left = asyncio.run(drive())
    assert offered == [ROWS - TOP_ROW + 1, 0, 9, 7], offered
    assert left == 0, "the region's count is handed over once, not offered again next time round"
