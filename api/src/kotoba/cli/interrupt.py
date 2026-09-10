"""Ctrl+C before she is up.

The console script is the entry point's `main`, so a run's first seconds are spent inside an import
chain where every `except KeyboardInterrupt` there is still inside a function that has not begun to
run, and an impatient press printed forty lines of somebody else's stack. `import asyncio` catches one.

Its own module, importing nothing but `sys`, so it may sit above the entry point's own imports and
above load_dotenv() with no configuration to be wrong about. Not a SIGINT handler: `asyncio.Runner`
arms its own only while SIGINT is `default_int_handler`, so one left armed kills Ctrl+C on `setup`.
"""
from __future__ import annotations

import sys

#: Seven-bit on purpose: it can be printed before argv is parsed, so `--ascii` has not been read yet.
_STOPPED = "\nstopped before she was up.\n"


def stopped_starting() -> int:
    """Say she was stopped on the way up, and hand back the code to exit with.

    130 is what every other Ctrl+C in `__main__` returns, so the shell sees one answer wherever the
    press landed. The sentence is its own, though: `— interrupted —` is a claim about a turn, and
    during startup there is no turn to interrupt — nothing has been said, and nothing is lost.

    The newline comes first because the tty has just echoed `^C` and left the cursor beside it."""
    sys.stderr.write(_STOPPED)
    sys.stderr.flush()
    return 130
