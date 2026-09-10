"""`kotoba serve`'s log is the instrument live QA reads, so an absence in it must mean something.

Three ways it did not: a `work_runner START` vanished while its `DONE` survived, because uvicorn
leaves the root logger bare and INFO records fell to `logging.lastResort` until an unrelated
dependency installed a handler by accident; a non-UTF-8 byte raised inside the pump thread and killed
it, losing every line that child would write after; and the pumps are daemon threads, so whatever
they had not written when `run()` returned died with the process.

Real children throughout: the pump reads a file DESCRIPTOR, so no mock would catch this, and it is
loop-independent — logging configuration does not care about uvloop versus asyncio."""
from __future__ import annotations

import io
import os
import subprocess
import sys

from kotoba.cli import serve

_BAD_BYTE = r'''
import os
os.write(1, b"before the bad byte\n")
os.write(1, b"\xff\xfe not utf-8 \xc3\x28\n")
os.write(1, b"after the bad byte\n")
'''

_BURST = r'''
import os
os.write(1, b"".join(b"line %05d\n" % i for i in range(__N__)))
os._exit(0)
'''

_STREAM = r'''
import os, sys
tag, n = sys.argv[1], int(sys.argv[2])
os.write(1, b"".join(b"%s %05d %s\n" % (tag.encode(), i, b"x" * 300) for i in range(n)))
'''

_SERVER_LOGS = r'''
import logging
import kotoba.server            # noqa: F401  -- the only logging configuration in the child
logging.getLogger("kotoba").info("work_runner START session=abc")
'''

# The shutdown race only exists in a process that EXITS: the pumps are daemon threads, so an in-process
# assertion just waits for them and always passes. This supervisor is `run()`'s spawn/stop/drain order.
_SUPERVISOR = r'''
import os, sys, time
from kotoba.cli import serve
child = serve._spawn([sys.executable, "-c", sys.argv[1]], None, dict(os.environ))
pumps = [serve._pump("[api]", child, sys.stdout)]
while child.poll() is None:
    time.sleep(0.001)
serve.stop(child)
serve.drain(pumps)
'''


def _run(source: str, *args: str) -> subprocess.Popen:
    return serve._spawn([sys.executable, "-c", source, *args], None, dict(os.environ))


def _child_env() -> dict[str, str]:
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    return dict(os.environ, PYTHONPATH=src, PYTHONUNBUFFERED="1")


def test_undecodable_byte_does_not_kill_the_pump() -> None:
    sink = io.StringIO()
    child = _run(_BAD_BYTE)
    thread = serve._pump("[api]", child, sink)
    child.wait(timeout=30)
    thread.join(timeout=10)

    text = sink.getvalue()
    assert "[api] before the bad byte" in text
    assert "[api] after the bad byte" in text, "one bad byte silenced the rest of the child"
    assert not thread.is_alive()


def test_shutdown_drains_what_the_child_already_wrote(tmp_path) -> None:
    n = 20000
    log_file = tmp_path / "serve.log"
    with log_file.open("w") as fh:
        result = subprocess.run(
            [sys.executable, "-c", _SUPERVISOR, _BURST.replace("__N__", str(n))],
            stdout=fh, stderr=subprocess.PIPE, text=True, timeout=120, env=_child_env(),
        )
    assert result.returncode == 0, result.stderr

    kept = sum(1 for line in log_file.read_text(encoding="utf-8").splitlines() if line.startswith("[api] line "))
    assert kept == n, f"lost {n - kept} lines that were already in the pipe when we shut down"


def test_two_pumps_never_tear_a_line() -> None:
    n = 4000
    sink = io.StringIO()
    pumps, kids = [], []
    for tag in ("A", "B"):
        child = _run(_STREAM, tag, str(n))
        pumps.append(serve._pump(f"[{tag.lower()}]", child, sink))
        kids.append(child)
    for child in kids:
        child.wait(timeout=60)
    serve.drain(pumps)

    seen: dict[str, set[int]] = {"A": set(), "B": set()}
    for line in sink.getvalue().splitlines():
        parts = line.split()
        assert len(parts) == 4 and parts[3] == "x" * 300, f"torn line: {line[:80]!r}"
        assert parts[0] == f"[{parts[1].lower()}]", f"prefix does not match payload: {line[:80]!r}"
        seen[parts[1]].add(int(parts[2]))
    assert len(seen["A"]) == n and len(seen["B"]) == n


def test_importing_the_server_delivers_info_records() -> None:
    """Without a handler installed on purpose, this INFO record reaches lastResort and is discarded."""
    result = subprocess.run(
        [sys.executable, "-c", _SERVER_LOGS], capture_output=True, text=True, timeout=120,
        env=_child_env(),
    )
    assert result.returncode == 0, result.stderr
    assert "work_runner START session=abc" in result.stderr, (
        "an INFO line from the server was silently dropped:\n" + result.stderr
    )
