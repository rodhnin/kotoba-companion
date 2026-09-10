"""Her prose has a ceiling the window does not lift; the machine's rows have none.

A maximised terminal is 200 columns and a paragraph laid out across all of them is a paragraph the
eye loses on the way back to the next line. So `screen.MEASURE` caps what she SAYS and nothing else:
rows, cards and command output keep `rw`, the terminal's own right edge. The suite never opened a
window wide enough for the cap to bite, which is how the cap came to be 0 for a while without one
test noticing.
"""
from __future__ import annotations

import io

import pytest

from kotoba.cli.render.caps import Caps
from kotoba.cli.render.screen import MEASURE, Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

WIDE = 200
NARROW = 80


def a_screen(width: int) -> tuple[Screen, io.StringIO]:
    caps = Caps(color="none", background="dark", unicode=True, interactive=False,
                width=width, height=40, g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    console = build_console(caps, file=buf)
    console.width = width
    screen = Screen(caps, console=console)
    screen.said_plate = True     # past the nameplate: the next block commits straight out
    return screen, buf


def longest_said(buf: io.StringIO) -> int:
    return max((len(line.rstrip()) for line in buf.getvalue().splitlines() if line.strip()), default=0)


def test_a_maximised_window_does_not_stretch_her_line():
    screen, buf = a_screen(WIDE)
    screen.say("palabra " * 200, last=True)
    assert screen.w == screen.gutter + MEASURE
    assert longest_said(buf) <= screen.gutter + MEASURE


def test_the_machines_rows_still_reach_the_edge_of_that_window():
    """The cap is about reading, not about fitting — a wide window should get a column of timings."""
    screen, _ = a_screen(WIDE)
    assert screen.rw > screen.w


def test_a_window_narrower_than_the_cap_is_left_alone():
    screen, buf = a_screen(NARROW)
    screen.say("palabra " * 200, last=True)
    assert screen.w == NARROW - 2
    assert longest_said(buf) > MEASURE - 40, "an 80-column window must still fill its own width"


@pytest.mark.parametrize("raw", ["0", "60"])
def test_the_knob_moves_the_cap_and_zero_removes_it(monkeypatch, raw):
    monkeypatch.setenv("KOTOBA_MEASURE", raw)
    screen, _ = a_screen(WIDE)
    room = WIDE - screen.gutter - 2
    assert screen.w == screen.gutter + (int(raw) if int(raw) else room)
