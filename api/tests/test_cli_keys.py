"""The keyboard while she works: cbreak held on a real pty, and handed back whatever happens."""
from __future__ import annotations

import asyncio
import os

import pytest

pty = pytest.importorskip("pty", reason="the interactive TUI is POSIX and these drive a real pty")
termios = pytest.importorskip("termios", reason="the interactive TUI is POSIX and these drive a real pty")

from kotoba.cli.input import keys


class FakeStdin:
    def __init__(self, fd: int) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd


@pytest.fixture
def tty_pair(monkeypatch):
    master, slave = pty.openpty()
    monkeypatch.setattr("sys.stdin", FakeStdin(slave))
    yield master, slave
    os.close(master)
    os.close(slave)


def canonical(fd: int) -> bool:
    return bool(termios.tcgetattr(fd)[3] & (termios.ICANON | termios.ECHO))


def test_the_loop_reads_the_keyboard_for_the_length_of_a_turn_and_hands_it_back_after(tty_pair):
    master, slave = tty_pair
    seen: list[str] = []

    async def turn() -> None:
        with keys.Keys(seen.append):
            assert not canonical(slave), "the line editor's echo would cost a row of scroll per press"
            os.write(master, b"qzx!\x1b[A")
            await asyncio.sleep(0.15)

    asyncio.run(turn())
    assert seen == ["qzx!\x1b[A"], "a sequence split across reads is an esc nobody pressed"
    assert canonical(slave)


def test_a_turn_that_raises_still_gives_the_terminal_its_echo_back(tty_pair):
    _, slave = tty_pair

    async def turn() -> None:
        with keys.Keys(lambda chunk: None):
            assert not canonical(slave)
            raise RuntimeError("the turn blew up")

    with pytest.raises(RuntimeError):
        asyncio.run(turn())
    assert canonical(slave), "she left the terminal raw and every later keystroke with it"


def test_a_card_takes_the_keyboard_off_the_turn_and_gives_it_straight_back(tty_pair):
    """Two readers on one fd split a keystroke between them, and the half that went to the typeahead
    is a `y` nobody pressed."""
    master, slave = tty_pair
    seen: list[str] = []

    async def turn() -> None:
        with keys.Keys(seen.append) as reader:
            reader.pause()
            os.write(master, b"y")
            await asyncio.sleep(0.1)
            assert seen == [] and os.read(slave, 8) == b"y"
            reader.resume()
            os.write(master, b"later")
            await asyncio.sleep(0.1)

    asyncio.run(turn())
    assert seen == ["later"]


def test_a_terminal_with_no_keyboard_reads_nothing_rather_than_refusing_to_run(monkeypatch):
    monkeypatch.setattr("sys.stdin", FakeStdin(-1))

    async def turn() -> bool:
        with keys.Keys(lambda chunk: None) as reader:
            return reader.enabled

    assert asyncio.run(turn()) is False
    assert asyncio.run(_disabled()) is False


async def _disabled() -> bool:
    with keys.Keys(lambda chunk: None, enabled=False) as reader:
        return reader.enabled


def test_a_word_somebody_is_typing_is_never_one_press_at_a_safety_gate():
    """A card opens under a message in progress and the keyboard is silently its own from that moment.
    Taking the first byte of the read made `ahora lo veo` an `a` — always allow this command family,
    forever — and threw the rest of the sentence away."""
    assert keys.one_press("ahora lo veo") == ""
    assert keys.one_press("ya voy") == ""
    assert keys.one_press("y") == "y"
    assert keys.one_press("a") == "a"


def test_an_arrow_key_is_not_an_escape_and_a_lone_escape_still_is():
    """↑ arrives as three bytes whose first is `esc`, and `esc` at a card is a no — the most-pressed key
    in a REPL, answering a gate nobody meant to answer."""
    assert keys.one_press("\x1b[A") == ""
    assert keys.one_press("\x1bOB") == ""
    assert keys.one_press("\x1b") == "\x1b"
    assert keys.one_press("\x03") == "\x03"


def test_the_gate_reads_what_the_terminal_had_rather_than_its_first_byte(tty_pair):
    master, _ = tty_pair
    os.write(master, b"ahora")
    assert keys.read_input(0.3) == "ahora"
