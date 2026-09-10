"""Reading a key at a terminal, and claiming only what this terminal can actually do.

`getpass` turns the echo off on `/dev/tty`, else stdin; where NEITHER is a terminal it gives up, prints
two lines of CPython warning and reads stdin anyway — both landed mid-sentence in her voice, on the row
that had just promised the key would not show. A CI runner and `docker run` without `-t` are that case.

So the read is ours: stdin directly, `EOFError` at end of input as getpass does. And the PROMISE is
withdrawn — nothing here can turn off an echo happening somewhere else. `can_hide` asks getpass's own
two questions in its own order, so it answers exactly "getpass will not fall back". Never logged.
"""
from __future__ import annotations

import getpass
import os
import sys

try:
    import termios
except ImportError:      # no POSIX terminal layer; there getpass hides through the console API
    termios = None


def _controllable(fd: int) -> bool:
    try:
        termios.tcgetattr(fd)
    except (termios.error, OSError, ValueError):
        return False
    return True


def can_hide() -> bool:
    """Whether a typed key can really be kept off the screen from here."""
    if termios is None:
        # Windows hides through the console API, and `msvcrt` reads the CONSOLE rather than stdin —
        # so with a pipe there it waits for a keypress nobody is going to make, for ever. Measured:
        # `kotoba setup` under a script never returned, which is the exact case this module is for.
        try:
            return sys.stdin.isatty()
        except (AttributeError, ValueError, OSError):
            return False
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        pass
    else:
        try:
            if _controllable(fd):
                return True
        finally:
            os.close(fd)
    try:
        return _controllable(sys.stdin.fileno())
    except (AttributeError, ValueError, OSError):
        return False


def read(prompt: str = "") -> str:
    """One secret, off the screen wherever the echo is ours to turn off. `EOFError` at end of input,
    which is what every caller here already treats as "they left"."""
    if can_hide():
        return getpass.getpass(prompt)
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.rstrip("\r\n")
