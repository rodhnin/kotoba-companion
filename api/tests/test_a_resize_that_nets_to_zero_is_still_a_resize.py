"""The two ways the prompt's painter reads a resize wrong.

`Menu._fit` answers prompt_toolkit's erase-down-and-repaint by comparing the SIZE against the last
one painted, where the thing that happened is an EVENT. POSIX coalesces signals, so a drag out and
back delivers one SIGWINCH at an unchanged size — the diff says nothing moved, and the band just
erased is never put back.

The other: `_fit` forgets `Screen.tail` on a width change, since old-width rows cannot be pasted
back; `frozen` then answers with an empty string, meaning "that row was blank" rather than
"unknown" — so closing the band ERASED a transcript row instead of leaving a gap for it."""
from __future__ import annotations

import io
import re
import time
from types import SimpleNamespace

import pytest
from conftest import needs_posix_terminal
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kotoba.cli import state
from kotoba.cli.app import App
from kotoba.cli.input import menu as panels
from kotoba.cli.input.prompt import Prompt
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import INDENT, Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

pytestmark = needs_posix_terminal

NOW = 10_000.0
TAIL = ["she wrote the file out", "› and then you asked her something", "one more transcript row"]
AT = re.compile(r"\x1b\[([0-9]+);1H")


class Live:
    def __init__(self, pipe, monkeypatch) -> None:
        self.caps = Caps(color="none", background="dark", unicode=True, interactive=True,
                         width=96, height=24, g=dict(GLYPHS_UNICODE))
        monkeypatch.setattr(self.caps, "sync_size", lambda: None)
        console = build_console(self.caps, file=io.StringIO())
        console.width = self.caps.width
        self.screen = Screen(self.caps, console=console,
                             portrait=Portrait(self.caps, wanted=False))
        self.prompt = Prompt(self.caps, complete_while_typing=False, input=pipe,
                             output=DummyOutput())
        monkeypatch.setattr(self.prompt, "geometry", lambda h: (20, 21))
        self.app = App(self.caps, self.screen, self.prompt)
        self.app.session = SimpleNamespace(session_id="s1",
                                           events=SimpleNamespace(steps={}, settle=lambda: None))
        self.written: list[str] = []
        monkeypatch.setattr(panels, "_emit", self.written.append)
        self.screen.kept(TAIL)

    def sync(self) -> str:
        before = len(self.written)
        self.app.menu.sync()
        return "".join(self.written[before:])

    def winch(self) -> None:
        """One SIGWINCH, as `Application._on_resize` delivers it: erase the glass, then repaint."""
        self.prompt._resized(lambda: None)

    def rows_touched(self, payload: str) -> set[int]:
        return {int(m) - 1 for m in AT.findall(payload)}


@pytest.fixture
def live(monkeypatch):
    with create_pipe_input() as pipe:
        yield Live(pipe, monkeypatch)


def running_job(at: float = NOW) -> state.Work:
    job = state.Work("get the real latency numbers", 1, t0=at - 72.0)
    job.tools.append(state.Tool("web", "web_search 'flash v2.5 first byte'", started=at - 4.0))
    return job


def test_a_drag_that_comes_back_to_the_same_size_puts_the_band_back(live, monkeypatch):
    """Two size changes, one delivered signal, a net difference of zero — and the glass is empty."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.app.work = running_job()
    first = live.sync()
    assert live.rows_touched(first) == {19}
    live.winch()
    repainted = live.sync()
    assert live.rows_touched(repainted) == {19}, \
        "prompt_toolkit erased on the way past — the band has to be written from nothing"
    assert "web_search" in repainted


def test_a_round_trip_keeps_the_bank_because_the_width_never_changed(live, monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.app.work = running_job()
    live.sync()
    live.winch()
    live.sync()
    assert live.screen.tail, "the rows are still the right width — forgetting them costs a transcript"
    assert live.app.menu.held[19][0] == live.screen.ansi(TAIL[1])


def test_a_width_change_still_forgets_the_diff_and_the_bank(live, monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.app.work = running_job()
    live.sync()
    live.caps.width = 90
    live.winch()
    repainted = live.sync()
    assert live.screen.tail == [], "rows banked at the old width restore somebody else's transcript"
    assert live.rows_touched(repainted) == {19}
    assert live.app.menu._size == (90, 24)


def test_a_row_the_bank_cannot_answer_for_is_never_erased(live, monkeypatch):
    """The gap above the box. With the bank forgotten, the painter banked `""` for a transcript row and
    the close wrote it out — erasing a line she had printed, one row above the frame."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.app.work = running_job()
    live.sync()
    live.caps.width = 90
    live.winch()
    live.sync()
    assert 19 not in live.app.menu.held, "an unknown row is not a blank one"
    live.app.work.state, live.app.work.stopped, live.app.work.landed = "ok", NOW, True
    closing = live.sync()
    assert "\x1b[K" not in closing, closing
    assert closing == "", "the band closed over a row it never banked — it may only let go of it"


def test_the_screen_says_it_does_not_know_rather_than_saying_the_row_was_blank(live):
    """Three answers, and the third is the one that was missing: a banked row comes back with its
    bytes, a row the app owns comes back blank, and a row below the bank comes back as `None` —
    unknown, which is what stops the band erasing a transcript row it never saw.

    Re-recorded: the bank now carries the face's height beside its column."""
    top = len(live.screen.tail)
    assert live.screen.frozen(top - 1, top) == (TAIL[-1], "", INDENT, 0)
    assert live.screen.frozen(top, top) == ("", "", INDENT, 0), \
        "rows from `top` down are the app's, and blank"
    assert live.screen.frozen(-1, top) is None, "below the bank is unknown, not blank"
