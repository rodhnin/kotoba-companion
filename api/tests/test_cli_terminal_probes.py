"""What the terminal is asked at launch, and what the answers may never cost.

Every one of these is driven over a real pty with a thread playing the terminal, because the failures
are in the protocol and not in the arithmetic: which file descriptor the colour question is asked of,
what a late reply does to the input box, what is left when nobody answers at all. A stub that returns
the right tuple would have passed while every one of them was live.
"""
from __future__ import annotations

import io
import os
import select
import threading
import time

import pytest

pty = pytest.importorskip("pty", reason="the interactive TUI is POSIX and these drive a real pty")

from kotoba.cli.render import art
from kotoba.cli.render.caps import detect
from kotoba.cli.render.portrait import Portrait

OSC11 = b"\x1b]11;?\x07"
DA1 = b"\x1b[c"
CPR = b"\x1b[6n"
CELL = b"\x1b[16t"

DARK = b"\x1b]11;rgb:2121/1a1a/2e2e\x07"
SIXEL_DA1 = b"\x1b[?62;1;4;6;9;15;22c"


class _Tty:
    """One end of a pty, wearing enough of a file to be stdin and stdout."""

    encoding = "utf-8"

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd

    def isatty(self) -> bool:
        return True

    def write(self, s: str) -> int:
        return os.write(self._fd, s.encode("utf-8", "replace"))

    def flush(self) -> None:
        pass


class Terminal:
    """The thing on the other side: it reads the questions and answers the ones it knows."""

    def __init__(self, answers: dict[bytes, bytes], lag: float = 0.0) -> None:
        self.answers, self.lag = answers, lag
        self.master, self.slave = pty.openpty()
        self.asked: list[bytes] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def type(self, text: bytes) -> None:
        os.write(self.master, text)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        os.close(self.master)
        os.close(self.slave)

    def _serve(self) -> None:
        while not self._stop.is_set():
            if not select.select([self.master], [], [], 0.02)[0]:
                continue
            try:
                data = os.read(self.master, 4096)
            except OSError:
                return
            for question in (OSC11, DA1, CPR, CELL):
                if question not in data:
                    continue
                self.asked.append(question)
                if question in self.answers:
                    if self.lag:
                        time.sleep(self.lag)
                    os.write(self.master, self.answers[question])


@pytest.fixture
def terminal(monkeypatch):
    """A pty on stdin and stdout, and a stderr that is explicitly NOT one."""
    made: list[Terminal] = []

    def build(answers: dict[bytes, bytes], lag: float = 0.0) -> Terminal:
        term = Terminal(answers, lag)
        made.append(term)
        tty = _Tty(term.slave)
        monkeypatch.setattr("sys.stdin", tty)
        monkeypatch.setattr("sys.stdout", tty)
        monkeypatch.setattr("sys.stderr", io.StringIO())
        return term

    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("KOTOBA_NO_GRAPHICS", raising=False)
    yield build
    for term in made:
        term.close()


def test_the_colour_question_is_asked_of_the_screen_she_prints_on(terminal):
    """`kotoba 2>notes.log` came back colourless — and portraitless, because the tier ladder needs a
    colour terminal to start on — for no reason but that the palette was measured on stderr."""
    terminal({OSC11: DARK, CPR: b"\x1b[1;6R", DA1: SIXEL_DA1})
    assert detect().color == "truecolor"


def test_an_answer_that_arrives_late_is_never_mistaken_for_typing(terminal):
    """130 ms of SSH is enough to miss a probe's window. The reply is still in the buffer when the next
    question reads, its ESC is stripped on the way in, and `]11;rgb:...` was landing in her input box."""
    term = terminal({OSC11: DARK, CPR: b"\x1b[1;6R", DA1: SIXEL_DA1}, lag=0.2)
    term.type(b"hola")
    caps = detect()
    assert caps.leftover == "hola", caps.leftover
    assert "rgb:" not in caps.leftover and "]11" not in caps.leftover


def test_what_the_person_typed_through_the_probes_survives_them(terminal):
    term = terminal({OSC11: DARK, CPR: b"\x1b[1;6R", DA1: SIXEL_DA1})
    term.type(b"puedes delegar un agente")
    assert detect().leftover == "puedes delegar un agente"


def test_her_sigil_survives_the_terminal_that_draws_omega_two_cells_wide(terminal):
    """Her kaomoji is ambiguous-width and goes ASCII on that terminal. 言 is unambiguously Wide, so it
    is the one glyph both sides still agree about, and it stays."""
    terminal({OSC11: DARK, CPR: b"\x1b[1;10R", DA1: SIXEL_DA1})
    caps = detect()
    assert caps.unicode is False
    assert caps.g["prompt"] == ">" and caps.g["sigil"] == "言"
    # `g` alone never reached the terminal: every printed row folds again at `screen.Trim`, which reads
    # this flag and nothing else. Without it the whole override was a no-op.
    assert caps.encodes_unicode is True
    assert caps.t("言 — ω") == "言 -- w", "the sigil stays and the ambiguous glyphs still go"


def test_ascii_takes_the_sigil_with_it_because_that_flag_means_seven_bits(terminal):
    terminal({OSC11: DARK, CPR: b"\x1b[1;6R", DA1: SIXEL_DA1})
    caps = detect(ascii_only=True)
    assert caps.g["sigil"] == "K"
    assert caps.encodes_unicode is False and caps.t("言") == "K"


def test_no_color_is_honoured_even_on_a_terminal_that_can_paint(terminal, monkeypatch):
    """NO_COLOR is a promise to a person who asked for no colour, on a terminal perfectly able to give
    it — a truecolor pty, answering every probe. It had no test at all: `_pin_the_terminal`
    deletes the variable for every test in the suite so the render comparisons stay stable, and nothing
    set it back, so `caps.detect`'s whole `color = "none"` branch was never once executed. The pin is
    the variable set INSIDE the test, which wins over the fixture and leaves it pinned for everyone else."""
    terminal({OSC11: DARK, CPR: b"\x1b[1;6R", DA1: SIXEL_DA1})
    assert detect().color == "truecolor", "this terminal was never able to paint"
    monkeypatch.setenv("NO_COLOR", "1")
    assert detect().color == "none"


def test_no_color_takes_the_colour_and_leaves_everything_else(terminal, monkeypatch):
    """It is a colour switch, not `--plain`: the glyphs, the background and the sixel tier are answers
    to different questions and none of them is about colour."""
    terminal({OSC11: DARK, CPR: b"\x1b[1;6R", DA1: SIXEL_DA1})
    monkeypatch.setenv("NO_COLOR", "1")
    caps = detect()
    assert caps.color == "none"
    assert caps.background == "dark" and caps.unicode is True and caps.interactive is True


def test_plain_reaches_the_same_branch_without_the_variable(terminal):
    terminal({OSC11: DARK, CPR: b"\x1b[1;6R", DA1: SIXEL_DA1})
    assert detect(plain=True).color == "none"


def test_a_terminal_with_no_pixel_size_is_asked_for_its_cell_instead_of_guessed_at(terminal):
    """tmux reports no pixel geometry at all, and it is the one place the sixel tier is claimed with
    nothing to size it: a cell 10 wide where the real one is 7 draws her over the text beside her."""
    terminal({OSC11: DARK, CPR: b"\x1b[1;6R", DA1: SIXEL_DA1, CELL: b"\x1b[6;15;7t"})
    assert detect().cell == (7, 15)


def test_a_terminal_that_answers_neither_keeps_the_old_guess(terminal):
    term = terminal({OSC11: DARK, CPR: b"\x1b[1;6R", DA1: SIXEL_DA1})
    caps = detect()
    assert caps.cell == (10, 21) and CELL in term.asked
    assert caps.leftover == ""


def test_a_terminal_that_can_draw_no_portrait_at_all_is_never_asked_about_its_cell(terminal,
                                                                                   monkeypatch):
    """The cell is only ever spent on her art. A sixteen-colour terminal with no sixel draws the
    kaomoji and nothing else, so it must not pay a timeout to measure pixels nobody will use."""
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("COLORTERM", raising=False)
    term = terminal({OSC11: DARK, CPR: b"\x1b[1;6R", DA1: b"\x1b[?62;1;6c"})
    caps = detect()
    assert (caps.color, caps.sixel) == ("16", False)
    assert CELL not in term.asked


def test_nothing_hangs_when_the_terminal_answers_nothing_at_all(terminal):
    terminal({})
    started = time.monotonic()
    caps = detect()
    assert time.monotonic() - started < 2.0
    assert (caps.background, caps.sixel, caps.cell) == ("mid", False, (10, 21))


class _Caps:
    color, background, unicode, interactive = "truecolor", "dark", True, True
    width, height, cell = 40, 24, (10, 21)


def test_a_portrait_that_earned_no_tier_composes_text_and_not_eleven_kilobytes_of_sixel():
    """The ladder refuses 40 columns. Rendering the face anyway puts DCS somewhere nobody can scroll
    it back out of."""
    box = Portrait(_Caps(), wanted=True)
    assert box.mode == "none"
    out = box.header(["MODEL gpt-5.4-mini", "WORK 214 files"], "happy")
    assert "\x1bP" not in out and "\x1b\\" not in out
    assert "MODEL gpt-5.4-mini" in out and "WORK 214 files" in out


def test_a_chafa_that_never_answers_cannot_hold_the_launch(tmp_path, monkeypatch):
    """Reading a pty to EOF waits for the child to close it, so a wedged chafa held the launch forever
    with nothing on the screen — and the wait() meant to bound it was never reached."""
    fake = tmp_path / "chafa"
    fake.write_text("#!/bin/sh\ncat > /dev/null\nsleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(art, "BUDGET", 0.4)
    monkeypatch.setattr(art, "_sixel_cache", {})

    started = time.monotonic()
    with pytest.raises(RuntimeError):
        art.sixel("neutral", 2, (11, 23))
    assert time.monotonic() - started < 3.0


def test_the_facts_in_the_header_are_drawn_through_the_same_gate_as_the_cards():
    """A model name and a path are strings this process was handed, and `rich.Text` carries an ESC in
    one of them to a terminal that obeys it."""
    from rich.text import Text

    from kotoba.cli.render.caps import Caps
    from kotoba.cli.render.header import header_rows
    from kotoba.cli.render.theme import GLYPHS_UNICODE

    caps = Caps(color="none", background="dark", unicode=True, interactive=False, width=100,
                g=dict(GLYPHS_UNICODE))
    stats = [("MODEL", "gpt-\x1b[2K\r5.4-mini", 2), ("WORK", "/files‮ desrever", 3)]
    drawn = "".join(row.plain for row in header_rows(caps, Text("KOTOBA"), 100, stats,
                                                    "local\x1b[1G sandbox"))
    for hostile in ("\x1b", "\r", "‮"):
        assert hostile not in drawn
    assert "5.4-mini" in drawn and "desrever" in drawn
