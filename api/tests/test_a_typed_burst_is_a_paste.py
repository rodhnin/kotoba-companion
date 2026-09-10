"""A program that TYPES at her sends no paste markers, and every `\\n` in it is a real enter.

`tmux send-keys`, `xdotool type`, and ssh/serial clients do exactly that, and a whole paragraph used
to arrive as one turn per line — every line a turn somebody paid for. Bracketed paste must keep
working, and the unbracketed path is only correct when it behaves the same way.

The rule must hold on every surface a paragraph can be typed at — `Prompt` between turns and
`app._typeahead` while she works. Neither sees the layer UNDER the policy: `input/keys.Keys` decides
how much of the arrival is handed over at once, and delivering one line at a time would put the bug
back with both policies intact and every test green — so the last two tests go through a real pty."""
from __future__ import annotations

import asyncio
import io
import os
import sys

import pytest

pty = pytest.importorskip("pty", reason="the interactive TUI is POSIX and these drive a real pty")

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kotoba.cli.app import App
from kotoba.cli.input import keys
from kotoba.cli.input.prompt import Prompt
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

ENTER = "\r"
BURST = "/help\nsecond pasted line"


def tty_caps() -> Caps:
    return Caps(color="none", background="dark", unicode=True, interactive=True,
                width=80, g=dict(GLYPHS_UNICODE))


def burst(chunk: str, beat: float = 0.25) -> tuple[bool, str, str | None]:
    """Write `chunk` into the prompt in ONE go, the way a pty write arrives.

    Returns (did it send, what is in the box, what the following enter returned)."""
    async def drive():
        with create_pipe_input() as pipe:
            prompt = Prompt(tty_caps(), complete_while_typing=False,
                            input=pipe, output=DummyOutput())
            asking = asyncio.create_task(prompt.ask_async())
            await asyncio.sleep(0.1)
            pipe.send_text(chunk)
            await asyncio.sleep(beat)
            sent, text = asking.done(), prompt.session.default_buffer.text
            if not sent:
                pipe.send_text(ENTER)
            return sent, text, await asyncio.wait_for(asking, 5)

    return asyncio.run(drive())


def test_a_multi_line_burst_with_no_paste_markers_sends_nothing():
    sent, text, line = burst(BURST)
    assert not sent, "the first line was sent on its own — one turn per line, and every one costs"
    assert text == "/help\nsecond pasted line"
    assert line == "/help\nsecond pasted line"


def test_a_burst_that_ends_on_a_newline_still_sends_nothing():
    """Where a paste ends is the clipboard's business, not a decision to send."""
    sent, text, _ = burst("one\ntwo\n")
    assert not sent
    assert text == "one\ntwo\n"


def test_a_burst_written_with_crlf_keeps_one_line_break_per_line():
    """The ssh and serial clients that hit this at all are the ones most likely to write CRLF, and
    `\\r` then `\\n` is two enters unless the pair is read as the one line ending it is."""
    sent, text, _ = burst("one\r\ntwo\r\nthree")
    assert not sent
    assert text == "one\ntwo\nthree"


def test_one_line_and_an_enter_in_the_same_write_still_sends():
    """`tmux send-keys "hola" Enter` is one write, and it is somebody driving her on purpose."""
    sent, _, line = burst("hola" + ENTER)
    assert sent
    assert line == "hola"


def test_the_terminals_own_cursor_reply_behind_an_enter_is_not_a_burst():
    """CSI 6n is asked on every render (`Prompt.room`) and the answer comes back on stdin, so it can
    land in the same read as the Enter somebody just pressed. It is the terminal answering, not
    somebody typing, and counting it would stop an ordinary line from ever going."""
    sent, _, line = burst("hola" + ENTER + "\x1b[12;1R")
    assert sent
    assert line == "hola"


def test_a_bracketed_paste_is_left_exactly_as_it_was():
    sent, text, _ = burst("\x1b[200~one\ntwo\nthree\x1b[201~")
    assert not sent
    assert text == "one\ntwo\nthree"


def test_a_piped_script_still_gets_one_line_at_a_time(monkeypatch):
    """No tty means no line editor and no key bindings — `_piped` is plain readline, and a script
    feeding her on stdin means one instruction per line on purpose. Nothing here reaches it."""
    caps = Caps(color="none", background="dark", unicode=True, interactive=False,
                width=80, g=dict(GLYPHS_UNICODE))
    monkeypatch.setattr(sys, "stdin", io.StringIO("first\nsecond\n"))
    prompt = Prompt(caps)
    assert asyncio.run(prompt.ask_async()) == "first"
    assert asyncio.run(prompt.ask_async()) == "second"


class _Keyboard:
    """A file object that is only ever asked for its descriptor, which is all `Keys` asks stdin for."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd


def through_a_real_keyboard(writes: list[str], beat: float = 0.25) -> tuple[tuple, str]:
    """Each string in `writes` put on a real pty in ONE write, read by the real reader and handed to the
    real policy. Returns (what was queued to send, what is left half-typed in the line).

    A pty, not a pipe: `Keys` puts the terminal into cbreak and a pipe has no terminal to put anywhere,
    so on a pipe it disables itself and the arrival never reaches `_typeahead` at all."""
    caps = tty_caps()
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    master, slave = pty.openpty()
    stdin, sys.stdin = sys.stdin, _Keyboard(slave)

    async def turn() -> None:
        with keys.Keys(app._typeahead) as reader:
            assert reader.enabled, "the pty was not put into cbreak, so nothing here was read"
            for chunk in writes:
                os.write(master, chunk.encode())
                await asyncio.sleep(beat)

    try:
        asyncio.run(turn())
    finally:
        sys.stdin = stdin
        os.close(master)
        os.close(slave)
    return app.queued, app.typing


def test_a_paragraph_typed_at_the_real_keyboard_costs_one_turn_and_not_one_per_line():
    """The whole chain, end to end: pty → `Keys` → `_typeahead`. Both policies can be perfect and this
    can still be one turn per line, because how much of the arrival reaches the policy is decided a
    layer below either of them — `Keys._ready` reads up to `CHUNK` and hands it over WHOLE. Split there,
    every line is its own read, every read ends on its own Enter, and each one is a turn somebody pays
    for; the two policy files would not notice, since both are handed their chunk by hand."""
    queued, typing = through_a_real_keyboard(["/help\rsecond pasted line\r"])
    assert queued == ("/help\nsecond pasted line",), "one write, one message"
    assert typing == ""


def test_two_lines_really_typed_are_still_two_messages():
    """The other half of the same fact, and what stops the repair from becoming "never send": the
    question is about ONE read and not about time, so a person who types a line, presses Enter and then
    types another has said two things — and each arrives in its own read."""
    queued, typing = through_a_real_keyboard(["hola\r", "adios\r"])
    assert queued == ("hola", "adios")
    assert typing == ""
