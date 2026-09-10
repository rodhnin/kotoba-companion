"""Raw keys with the terminal's own echo off: one for a card, and the whole keyboard for a turn.

A card is answered inside the live region, padded down to the last row of the window, where a line
editor echoing what you type costs a row of scroll per press and takes the header up with it — the
whole thing the pinned frame exists to stop. So a card is answered by one key, raw and never echoed.

`cbreak`, never `raw`: stdin and stdout are one terminal, and `setraw` clears `OPOST` and with it
`ONLCR`, so every row the region draws starts where the last one ended and the card walks off the
right of the screen a rail at a time. cbreak leaves ISIG, so Ctrl+C at a card still cuts the turn.
"""
from __future__ import annotations

import asyncio
import os
import select
import sys
from collections.abc import Callable

from kotoba.cli.render.text import CSI

try:
    import termios
    import tty
except ModuleNotFoundError:     # Windows has no POSIX terminal layer to put into cbreak
    termios = tty = None

POLL = 0.25
CHUNK = 1024

# What a terminal that will not go into cbreak raises, resolved at import so the two handlers below
# stay valid where there is no `termios` to read `termios.error` off. Both already answered "no
# keyboard, carry on" — the module simply has one more way to have none.
_NO_TERMINAL: tuple[type[BaseException], ...] = (
    (ValueError, OSError, AttributeError) if termios is None
    else (termios.error, ValueError, OSError, AttributeError)
)


class Interrupted(Exception):
    """`esc`, meant. Raised by the typeahead policy and caught by whoever is driving the turn."""


def read_input(timeout: float = POLL) -> str:
    """Whatever the terminal had inside `timeout`, decoded whole, or "" when nothing arrived.

    `TCSANOW`, never `setcbreak`'s `TCSAFLUSH` default: that one DISCARDS whatever the person typed
    while she was working, and a keystroke you pressed and never saw is worse than one that arrives
    late. It polls rather than blocking because a thread parked in `os.read` cannot be cancelled — a
    card cleared under it would hold stdin for the rest of the session. The restore puts back what was
    FOUND and not a canonical terminal: a card opening mid-turn borrows the keyboard from a cbreak
    that is already on, and has to hand it back that way."""
    try:
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
    except _NO_TERMINAL:
        return ""
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        if not select.select([fd], [], [], timeout)[0]:
            return ""
        return os.read(fd, CHUNK).decode("utf-8", "replace")
    except OSError:
        return ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def one_press(chunk: str) -> str:
    """The single key `chunk` carries, or "" when it carries something that is not one press.

    A read with a word in it is a message being typed, not an answer: taking its first byte instead let
    `ahora lo veo`, typed while she worked, reach a safety card as an `a` — always allow this command
    family, forever — and threw the rest of the sentence away. A complete escape sequence is not one
    press either: ↑ arrives as three bytes whose first is `esc`, and `esc` at a card is a no."""
    rest = CSI.sub("", chunk)
    return rest if len(rest) == 1 else ""


class Keys:
    """cbreak on stdin for as long as this is open, with the running loop doing the reading.

    It owns the keyboard for the length of a turn: with the line editor gone nothing else reads stdin,
    so keys typed while she works would sit in the tty buffer and arrive unqueued afterwards. Two
    readers on one fd split a keystroke between them, so a card opening mid-turn takes the keyboard
    back with `pause` and hands it over again after. The restore hangs off the context manager — a turn
    that raises may not leave the terminal without its echo.

    A terminal that cannot be put into cbreak — piped, or stdin gone — simply reads nothing and says so
    in `enabled`, because a CLI with no keyboard is still a CLI."""

    def __init__(self, on_chunk: Callable[[str], None], *, enabled: bool = True) -> None:
        self.on_chunk = on_chunk
        self.enabled = enabled
        self.fd = -1
        self.saved = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def __enter__(self) -> "Keys":
        if not self.enabled:
            return self
        try:
            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd, termios.TCSANOW)
        except _NO_TERMINAL:
            self.enabled, self.saved = False, None
            return self
        self.loop = asyncio.get_running_loop()
        self.resume()
        return self

    def __exit__(self, *exc) -> None:
        self.pause()
        self.loop = None
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            self.saved = None

    def resume(self) -> None:
        if self.loop is not None:
            self.loop.add_reader(self.fd, self._ready)

    def pause(self) -> None:
        if self.loop is not None:
            self.loop.remove_reader(self.fd)

    def _ready(self) -> None:
        """Whatever the terminal had, decoded and handed over whole. A terminal writes an escape
        sequence in one write, so a read big enough to take the whole write can never halve one — and
        half of an arrow key is a bare `\\x1b`, which is the key that throws her work away."""
        try:
            data = os.read(self.fd, CHUNK)
        except (OSError, ValueError):
            data = b""
        if not data:
            # stdin is gone: a reader that never reads again would spin the loop for the rest of the turn.
            self.pause()
            return
        self.on_chunk(data.decode("utf-8", "replace"))
