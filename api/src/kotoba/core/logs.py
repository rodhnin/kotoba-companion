"""Where this process's logs go, and where the processes it spawns write — anywhere but the screen.

Two different floods reach a terminal running the CLI and only one is ours. Ours is a `logging`
question: the root handler wrote to stderr, so an httpx INFO line landed inside her answer. The other
is not ours — an stdio MCP server is a separate process the SDK spawns with `stderr=sys.stderr`, so it
writes to fd 2 itself, and pinning its logger in THIS process addresses one living in another.

The SDK gives `stdio_client` an `errlog` parameter and then hides it, but `session_group` does
`import mcp` and resolves the name on the package every call — so binding errlog there catches every
child the group will spawn. Nothing happens until `to_file()`, and only the CLI calls it."""
from __future__ import annotations

import functools
import logging
import os
from pathlib import Path
from typing import TextIO

from kotoba.core import perms
from kotoba.paths import home_dir

_MAX_BYTES = 2 * 1024 * 1024

_sink: TextIO | None = None
_stdio_client = None


def path() -> Path:
    # `home_dir()`, asked each time: `HOME_DIR` is settled at import, so this went on writing to the
    # home the process STARTED in however the environment changed — which is how a suite reaches the
    # user's own files.
    return Path(os.getenv("KOTOBA_CLI_LOG", str(home_dir() / "cli.log"))).expanduser()


def to_file() -> Path:
    """Send this process's logging, and the output of the processes it spawns, to that file."""
    global _sink
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Started over rather than rolled to a second file, which a child's raw descriptor would write past.
    outgrown = target.is_file() and target.stat().st_size > _MAX_BYTES
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if outgrown else os.O_APPEND)
    # O_BINARY: the wrapper below already writes os.linesep, and a text-mode descriptor on Windows
    # translates the \n of that pair a second time, ending every log line with \r\r\n.
    fd = os.open(str(target), flags | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    perms.restrict_file(target)
    _sink = os.fdopen(fd, "w" if outgrown else "a", buffering=1, encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=os.getenv("KOTOBA_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(_sink)],
        force=True,     # whatever configured logging first must not keep a handler on the screen
    )
    _redirect_mcp_children()
    return target


def also_to_console() -> None:
    """Say it out loud as well, for a process with no screen of its own to protect.

    `to_file` still runs first, so a spawned MCP server keeps its chatter off the terminal — what
    lands here is only what this process chose to log."""
    handler = logging.StreamHandler()   # defaults to stderr
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


def child_streams() -> dict[str, TextIO]:
    """`subprocess` keywords that keep a spawned process off the terminal. Empty until `to_file` runs, so
    a server keeps handing its children its own streams."""
    return {"stdout": _sink, "stderr": _sink} if _sink is not None else {}


def _redirect_mcp_children() -> None:
    global _stdio_client
    try:
        import mcp
    except ImportError:
        return
    if _stdio_client is None:
        _stdio_client = mcp.stdio_client
    mcp.stdio_client = functools.partial(_stdio_client, errlog=_sink)
