"""The screen belongs to the UI: nothing this process spawns, and nothing it logs, may reach the terminal.

The MCP test spawns a real server through the real SDK, because that is the only way to prove the thing
that broke: the child writes to a file DESCRIPTOR, so no logger level set here could ever have stopped it
and no mock of ours would have caught it. `capfd` reads at the same level the child writes at.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys

import pytest

from kotoba.core import logs

_SERVER = '''
import sys
from mcp.server.fastmcp import FastMCP

print("MARKER cannot reach the thing it drives", file=sys.stderr, flush=True)
server = FastMCP("probe")

@server.tool()
def ping() -> str:
    return "pong"

server.run()
'''


@pytest.fixture(autouse=True)
def _restore_process_state():
    """`to_file` configures the whole process — the root logger, and a name on the mcp package."""
    import mcp

    handlers, level, sink = logging.root.handlers[:], logging.root.level, logs._sink
    stdio_client = mcp.stdio_client
    yield
    logging.root.handlers[:] = handlers
    logging.root.setLevel(level)
    logs._sink = sink
    mcp.stdio_client = stdio_client


def _connect(script) -> None:
    from mcp import StdioServerParameters
    from mcp.client.session_group import ClientSessionGroup

    async def run() -> None:
        async with ClientSessionGroup() as group:
            await group.connect_to_server(
                StdioServerParameters(command=sys.executable, args=[str(script)])
            )

    asyncio.run(run())


def test_an_mcp_server_spawned_by_the_sdk_writes_to_the_log_file_and_not_to_the_terminal(tmp_path, capfd):
    script = tmp_path / "probe_server.py"
    script.write_text(_SERVER, encoding="utf-8")
    log = logs.to_file()

    capfd.readouterr()
    _connect(script)
    seen = capfd.readouterr()

    assert "MARKER" not in seen.err and "MARKER" not in seen.out
    assert "ListToolsRequest" not in seen.err, "the child's own logging is not part of the conversation"
    assert "MARKER" in log.read_text(encoding="utf-8")


def test_a_server_whose_output_is_hidden_is_still_readable_afterwards(tmp_path, capfd):
    script = tmp_path / "probe_server.py"
    script.write_text(_SERVER, encoding="utf-8")
    log = logs.to_file()

    _connect(script)
    capfd.readouterr()

    written = log.read_text(encoding="utf-8")
    assert "cannot reach the thing it drives" in written, "hidden must not mean lost"


def test_our_own_libraries_log_to_the_file_and_never_to_the_screen(capfd):
    log = logs.to_file()

    capfd.readouterr()
    logging.getLogger("httpx").info("HTTP Request: POST https://example.invalid/v1 200 OK")
    logging.getLogger("kotoba.mcp").warning("MCP 'blender' failed to connect on boot")
    seen = capfd.readouterr()

    assert seen.err == "" and seen.out == ""
    written = log.read_text(encoding="utf-8")
    assert "HTTP Request" in written and "failed to connect on boot" in written


def test_a_handler_installed_before_us_does_not_keep_the_screen(capfd):
    logging.basicConfig(stream=sys.stderr, force=True)
    logs.to_file()

    capfd.readouterr()
    logging.getLogger("kotoba").error("something went wrong")

    assert capfd.readouterr().err == ""


def test_nothing_is_redirected_until_the_cli_asks_for_it(tmp_path):
    """The server wants these logs, and `kotoba.server` never calls `to_file`.

    Asked of a FRESH interpreter, because the claim is about a process that has never run the CLI and
    `to_file` wraps `mcp.stdio_client` for the whole of one with nothing anywhere to unwrap it. Driven
    in-process it passed or failed on whichever test file pytest collected first: any test that reached
    `cli/__main__.main` broke this one from a distance, and broke it with an `AttributeError` naming
    `functools.partial` rather than the run that had done the wrapping.
    """
    probe = (
        "import mcp, kotoba.server\n"
        "from kotoba.core import logs\n"
        "assert logs.child_streams() == {}, logs.child_streams()\n"
        "assert mcp.stdio_client.__name__ == 'stdio_client', mcp.stdio_client\n"
        "logs.to_file()\n"
        "assert set(logs.child_streams()) == {'stdout', 'stderr'}\n"
        "assert not hasattr(mcp.stdio_client, '__name__'), 'to_file did not wrap it'\n"
        "print('clean')\n"
    )
    env = dict(os.environ, KOTOBA_CLI_LOG=str(tmp_path / "cli.log"))
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert "clean" in out.stdout


def test_a_spawned_browser_is_handed_the_same_file():
    logs.to_file()
    streams = logs.child_streams()
    assert set(streams) == {"stdout", "stderr"}
    assert streams["stdout"] is streams["stderr"] is logs._sink


def test_the_log_does_not_grow_without_end(monkeypatch):
    monkeypatch.setattr(logs, "_MAX_BYTES", 64)
    log = logs.to_file()
    logging.getLogger("kotoba").error("x" * 200)
    logs._sink.flush()
    assert log.stat().st_size > 64

    logs.to_file()
    logging.getLogger("kotoba").error("the run after the one that filled it")
    logs._sink.flush()
    written = log.read_text(encoding="utf-8")
    assert "xxxx" not in written and "the run after" in written
