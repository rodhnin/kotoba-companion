"""A cancelled turn left the OS process running on the host.

`run()` killed the process group only on a timeout, and every real turn cancellation (a work cancel, a
barge-in over a long shell call, an exhausted budget) arrives as `CancelledError` instead, which propagated
out without touching the child. The loop's cleanup cancels the tool task, but cancelling a coroutine never
kills the process it spawned — a background command kept running orphaned after "stop". These tests assert
against the real OS, no mocks: the whole tree dies on cancellation AND the CancelledError still propagates,
since swallowing it would break the barge-in teardown contract; the same hole existed for the docker CLI
client. GUI windows opened on purpose are not at risk: the launch patterns that work return at once and
leave no process group for a cancel to find, since `run()` now ends with the process, not its pipes."""
from __future__ import annotations

import asyncio
import random
import subprocess

import pytest
from conftest import posix_only

from kotoba.core.sandbox.local import LocalSandbox

pytestmark = posix_only("POSIX process groups and pgrep")


def _pgrep(marker: str) -> list[str]:
    r = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    return [p for p in r.stdout.split() if p]


def _marker() -> str:
    return f"3917.{random.randint(100000, 999999)}"


def _reap(pids: list[str]) -> None:
    for p in pids:
        subprocess.run(["kill", "-9", p], capture_output=True)


async def _wait_spawn(marker: str, n: int = 1) -> None:
    for _ in range(60):
        if len(_pgrep(marker)) >= n:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"process for {marker} never spawned")


async def _wait_gone(marker: str) -> list[str]:
    for _ in range(60):
        survivors = _pgrep(marker)
        if not survivors:
            return []
        await asyncio.sleep(0.1)
    return _pgrep(marker)


def test_cancellation_kills_the_whole_process_tree(tmp_path):
    marker = _marker()

    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        task = asyncio.create_task(sb.run(f"sleep {marker} & sleep {marker}", timeout=300))
        await _wait_spawn(marker, n=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await _wait_gone(marker)

    survivors = asyncio.run(go())
    _reap(survivors)
    assert survivors == []


def test_cancelled_turn_reaps_the_child_through_the_full_harness(tmp_path, monkeypatch):
    """The path a barge-in actually takes: execute_with_heartbeat shields the tool task and its
    `finally` cancels it fire-and-forget — the sandbox's CancelledError branch is the only thing that
    can reap the child from there."""
    from kotoba.core.loop import execute_with_heartbeat
    from kotoba.tools import ToolContext

    monkeypatch.setenv("KOTOBA_SANDBOX", "local")
    marker = _marker()

    async def go():
        ctx = ToolContext(db=None, session_id=None, workdir=tmp_path, channel="text")
        q: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(
            execute_with_heartbeat("shell", {"command": f"sleep {marker}", "timeout": 300}, q, {}, ctx)
        )
        await _wait_spawn(marker)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await _wait_gone(marker)

    survivors = asyncio.run(go())
    _reap(survivors)
    assert survivors == []


def test_docker_cli_client_dies_on_cancellation():
    """`_run_cli` shells out on the HOST (no daemon needed here) and had the identical hole: only the
    timeout branch killed the client process."""
    from kotoba.core.sandbox.docker import _run_cli

    marker = _marker()

    async def go():
        task = asyncio.create_task(_run_cli(["sleep", marker], timeout=300))
        await _wait_spawn(marker)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await _wait_gone(marker)

    survivors = asyncio.run(go())
    _reap(survivors)
    assert survivors == []
