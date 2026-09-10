"""The escalation to SIGKILL keys off the group, not the direct child.

A prior test asserts that a cancel kills the tree at all; this covers two ways the escalation itself
gave up early, reproduced against the real thing before being fixed: the direct child exiting proves
nothing about the tree — a wrapped process can die on the group's SIGTERM the moment its own child
does, so the wait call returns and the loop took that as success, leaving a grandchild that trapped
SIGTERM running forever. And a second cancel arriving during the grace window aborted the escalation
and left the same orphan — the likeliest such moment being a reload or a Ctrl+C on top of a cancel.
The probe's marker never appears in a shell argv anywhere here: written into a heredoc instead, since
a process-name match once caught the shell that launched the probe and killed the terminal itself."""
from __future__ import annotations

import asyncio
import os
import subprocess

import pytest
from conftest import posix_only

from kotoba.core.sandbox.local import LocalSandbox

pytestmark = posix_only("POSIX process groups, a bash trap on SIGTERM and pgrep")

# A grandchild that ignores SIGTERM and keeps going. Only a group-wide SIGKILL ends it.
_STUBBORN = "bash -c 'trap \"\" TERM; while :; do sleep 1; done' & sleep 100"


def _survivors(mark: str) -> list[str]:
    out = subprocess.run(["pgrep", "-f", mark], capture_output=True, text=True).stdout.split()
    return [p for p in out if p != str(os.getpid())]


def _reap(pids) -> None:
    for pid in pids:
        subprocess.run(["kill", "-9", pid], capture_output=True)


async def _cancel_a_stubborn_tree(tmp_path, mark: str, twice: bool):
    sb = LocalSandbox(tmp_path)
    await sb.start()
    task = asyncio.create_task(sb.run(f"{_STUBBORN} # {mark}", timeout=300))
    await asyncio.sleep(0.8)
    assert _survivors(mark), "the probe never started — nothing was proved"
    task.cancel()
    if twice:
        await asyncio.sleep(0.3)
        task.cancel()
    raised = False
    try:
        await task
    except asyncio.CancelledError:
        raised = True
    await asyncio.sleep(0.5)
    return raised, _survivors(mark)


@pytest.mark.parametrize("twice", [False, True], ids=["one cancel", "a second during the grace"])
def test_a_grandchild_that_traps_sigterm_still_dies(tmp_path, twice):
    mark = f"kotoba_escalation_test_{os.getpid()}_{int(twice)}"
    raised, left = asyncio.run(_cancel_a_stubborn_tree(tmp_path, mark, twice))
    _reap(left)
    assert raised, "CancelledError must still reach the caller — teardown may not swallow it"
    assert not left, f"orphaned after cancel: {left}"


def test_the_timeout_branch_escalates_too(tmp_path):
    """The cancel tests above pass even with `_group_alive` neutered, because a cancel reaches SIGKILL by
    another route — so only this one actually exercises the group probe end to end. The timeout branch
    carries the same escalation and had no stubborn-grandchild coverage at all, which is how the bug got
    in: it was never a cancellation bug, it was `_terminate` believing the direct child spoke for the tree.
    """
    mark = f"kotoba_escalation_timeout_{os.getpid()}"

    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        result = await sb.run(f"{_STUBBORN} # {mark}", timeout=1)
        await asyncio.sleep(0.5)
        return result, _survivors(mark)

    result, left = asyncio.run(go())
    _reap(left)
    assert result.exit_code == 124, f"expected the timeout verdict, got {result.exit_code}"
    assert not left, f"orphaned after timeout: {left}"


def test_the_group_probe_reports_emptiness_not_reachability(tmp_path):
    """`_group_alive` is what lets the fast path skip SIGKILL, so a wrong answer either leaks a process
    or costs every clean kill an extra 5 seconds."""
    from kotoba.core.sandbox.local import _group_alive

    assert _group_alive(os.getpgrp())
    assert not _group_alive(999_999)
