"""An open database must never be able to stop the interpreter from exiting.

aiosqlite runs every statement on a worker thread that is NOT a daemon, and CPython joins
non-daemon threads before atexit. A Database that outlives its event loop without close()
parks that worker forever, so the process hangs at exit with no traceback and no output.

Measured before the fix: killed at 20s having printed nothing, on both asyncio and uvloop —
the axis is whether a reference to the Database survives, not the loop. The app never meets
this (engine.stop() closes), but a script written against this module can."""
from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

EXIT_BUDGET = 30.0        # generous: the fixed path exits in well under a second

_LEAKS_A_DATABASE = """
import asyncio, sys
from kotoba.db.database import Database

db = Database("sqlite:///" + sys.argv[1])          # module-level: still referenced at shutdown

async def main():
    await db.connect()
    await db.count_turns()                          # no close(), as a throwaway script writes it

asyncio.run(main())
print("ran")
"""


def _run(source: str, tmp_path, *args: str) -> subprocess.CompletedProcess:
    script = tmp_path / "leak.py"
    script.write_text(textwrap.dedent(source))
    try:
        return subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, timeout=EXIT_BUDGET,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"the process never exited within {EXIT_BUDGET}s: an unclosed Database left aiosqlite's "
            "non-daemon worker parked on its queue and CPython joins it before atexit"
        )


def test_a_database_left_open_still_lets_the_process_exit(tmp_path):
    done = _run(_LEAKS_A_DATABASE, tmp_path, str(tmp_path / "probe.db"))
    assert done.returncode == 0, done.stderr
    assert "ran" in done.stdout


def test_the_same_holds_under_uvloop(tmp_path):
    uvloop = pytest.importorskip("uvloop")
    source = _LEAKS_A_DATABASE.replace(
        "asyncio.run(main())", "import uvloop; uvloop.run(main())"
    )
    assert uvloop and "uvloop.run" in source
    done = _run(source, tmp_path, str(tmp_path / "probe.db"))
    assert done.returncode == 0, done.stderr
    assert "ran" in done.stdout


def test_the_worker_thread_stays_non_daemon(tmp_path):
    """Do NOT fix the hang by daemonising the worker: a leaked database is caught by looking for the
    non-daemon thread it left running, and a daemon one is invisible to that check."""
    source = """
    import asyncio, sys
    from kotoba.db.database import Database

    async def main():
        db = Database("sqlite:///" + sys.argv[1])
        await db.connect()
        print("daemon:", db.conn._thread.daemon, "alive:", db.conn._thread.is_alive())
        await db.close()

    asyncio.run(main())
    """
    done = _run(source, tmp_path, str(tmp_path / "probe.db"))
    assert done.returncode == 0, done.stderr
    assert "daemon: False alive: True" in done.stdout
