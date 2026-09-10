"""Live QA: an approved command she meant to leave running died at its own deadline.

Measured on the voice channel: approval 13:50:22, "executed" 13:50:32, killed as "timed out after 10s",
for `sh -c 'sleep 400 >/dev/null 2>&1 & echo $!'` — a command that returns in milliseconds. The cause:
`LocalSandbox.run`'s `communicate()` waits for its pipes to reach EOF, not for the command to finish, so
a backgrounded process inherits those pipes, the wait times out, and SIGKILL takes the whole process
group, killing exactly the process she was told to leave alive.
2477 green tests missed it because the suite runs on asyncio while the server runs on uvloop, which hands
a backgrounded grandchild copies of the stdout/stderr pipes that no redirect can escape — these tests now
run every shape on both loops, since only uvloop reproduces what the user hit."""
from __future__ import annotations

import asyncio
import random
import subprocess
import time

import pytest
from conftest import posix_only

from kotoba.core.sandbox.local import LocalSandbox

pytestmark = posix_only("POSIX job control (&, nohup) and pgrep")

LOOPS = ["asyncio", "uvloop"]


def _run_on(loop_name: str, factory):
    """Run one coroutine on the named event loop, skipping when that loop is not installed."""
    if loop_name != "uvloop":
        return asyncio.run(factory())
    uvloop = pytest.importorskip("uvloop")
    if hasattr(uvloop, "run"):
        return uvloop.run(factory())
    loop = uvloop.new_event_loop()
    try:
        return loop.run_until_complete(factory())
    finally:
        loop.close()


def _marker() -> str:
    return f"4917.{random.randint(100000, 999999)}"


def _pgrep(marker: str) -> list[str]:
    r = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    return [p for p in r.stdout.split() if p]


def _reap(marker: str) -> None:
    for pid in _pgrep(marker):
        subprocess.run(["kill", "-9", pid], capture_output=True)


def _background(marker: str, shape: str) -> str:
    """The three ways to leave something running. The first two are hers, verbatim from the audit log."""
    return {
        "redirected": f"sh -c 'sleep {marker} >/dev/null 2>&1 & echo $!'",
        "nohup": f"sh -c 'nohup sleep {marker} >/dev/null 2>&1 </dev/null & echo started'",
        "bare": f"sleep {marker} &",
    }[shape]


def test_the_server_runs_on_a_different_event_loop_than_this_suite():
    """The blind spot itself, asserted so it cannot go quiet again: `kotoba serve` starts uvicorn with
    the default `loop="auto"`, which picks uvloop whenever it is importable — and `uvicorn[standard]`,
    the `server` extra everyone installs, brings it. A behaviour proven only under `asyncio.run` is not
    proven for the product."""
    uvloop = pytest.importorskip("uvloop")
    from uvicorn.loops.auto import auto_loop_factory

    loop = auto_loop_factory()()
    try:
        assert type(loop).__module__.startswith(uvloop.__name__)
    finally:
        loop.close()


@pytest.mark.parametrize("loop_name", LOOPS)
@pytest.mark.parametrize("shape", ["redirected", "nohup", "bare"])
def test_backgrounding_returns_at_once_instead_of_burning_the_budget(tmp_path, loop_name, shape):
    """Her exact command, her exact 10s budget. Before the fix this returned at second 10 with exit 124
    and `timed out after 10s and was killed`."""
    marker = _marker()

    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        t0 = time.monotonic()
        res = await sb.run(_background(marker, shape), timeout=10)
        return time.monotonic() - t0, res

    try:
        elapsed, res = _run_on(loop_name, go)
        assert res.exit_code == 0, res.stderr
        assert "timed out" not in res.stderr
        assert elapsed < 5, f"took {elapsed:.1f}s of a 10s budget"
    finally:
        _reap(marker)


@pytest.mark.parametrize("loop_name", LOOPS)
@pytest.mark.parametrize("shape", ["redirected", "nohup", "bare"])
def test_what_she_backgrounded_is_still_running_afterwards(tmp_path, loop_name, shape):
    """The user-visible half of the defect: the point of backgrounding is that it outlives the turn."""
    marker = _marker()

    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        await sb.run(_background(marker, shape), timeout=10)
        return _pgrep(marker)

    try:
        assert _run_on(loop_name, go), "the process she backgrounded was killed"
    finally:
        _reap(marker)


@pytest.mark.parametrize("loop_name", LOOPS)
def test_the_command_output_still_comes_back_whole(tmp_path, loop_name):
    """Stopping at the process instead of at EOF must not cost output: everything written before the
    command exited is already buffered, and the pumps are given _DRAIN_GRACE to collect it."""
    marker = _marker()
    cmd = f"sh -c 'sleep {marker} >/dev/null 2>&1 & yes kotoba | head -n 20000'"

    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        return await sb.run(cmd, timeout=20)

    try:
        res = _run_on(loop_name, go)
        assert res.exit_code == 0, res.stderr
        assert res.stdout.count("kotoba") == 20000
    finally:
        _reap(marker)


@pytest.mark.parametrize("loop_name", LOOPS)
def test_a_command_that_really_hangs_is_still_killed_at_its_deadline(tmp_path, loop_name):
    """The safeguard the fix must not spend: a genuinely blocking command still dies on time, with the
    whole tree, on both loops."""
    marker = _marker()

    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        t0 = time.monotonic()
        res = await sb.run(f"sleep {marker}", timeout=1)
        for _ in range(50):
            if not _pgrep(marker):
                break
            await asyncio.sleep(0.1)
        return time.monotonic() - t0, res, _pgrep(marker)

    try:
        elapsed, res, survivors = _run_on(loop_name, go)
        assert res.exit_code == 124 and "timed out after 1s and was killed" in res.stderr
        assert elapsed < 8
        assert survivors == []
    finally:
        _reap(marker)


@pytest.mark.parametrize("loop_name", LOOPS)
def test_the_whole_deferred_voice_path_leaves_it_running(tmp_path, loop_name, monkeypatch):
    """The failure at the level it was reported at: a live ElevenLabs turn, a real approval card, a real
    yes from the user, and the detached task that runs the command after the turn is over.

    Everything here is the production path — shell.execute deciding to defer because an ElevenLabs agent
    is holding the turn (the mark `/v1` sets, entered here the way the producer enters it),
    core.interaction opening the card on the SSE queue, core.deferred_exec waiting outside the turn and
    running it on the session sandbox. Only the human's click is simulated."""
    monkeypatch.setenv("KOTOBA_SANDBOX", "local")
    marker = _marker()
    command = _background(marker, "redirected")
    sid = f"voice-{marker}"

    import kotoba.tools.action.shell as shell
    from kotoba.core import deferred_exec, events, interaction, transport, work_state
    from kotoba.core.approval import ApprovalGate
    from kotoba.tools import ToolContext

    async def go():
        queue = events.register(sid)
        with transport.el_call_turn():
            ctx = ToolContext(db=None, session_id=sid, workdir=tmp_path, channel="voice",
                              approval=ApprovalGate(host_exec=True, workspace_root=tmp_path),
                              user_text="leave that running for me", call_id="call-1")
        spoken = await shell.execute({"command": command, "timeout": 10}, ctx)

        card = None
        while card is None:
            frame = await asyncio.wait_for(queue.get(), timeout=5)
            if frame.get("kind") == "need_input" and frame.get("mode") == "approval":
                card = frame

        t0 = time.monotonic()
        assert interaction.resolve(sid, {"approved": True, "always": False}, card["request_id"])
        pending = set(deferred_exec._tasks.get(sid) or ())
        await asyncio.wait(pending, timeout=30)
        return spoken, card, time.monotonic() - t0, work_state.get(sid), _pgrep(marker)

    try:
        spoken, card, elapsed, snap, survivors = _run_on(loop_name, go)
        assert "approve" in spoken.lower()
        assert card["label"] == command
        assert elapsed < 5, f"the approved command took {elapsed:.1f}s to come back"
        assert "exit code 0" in snap["summary"] and "timed out" not in snap["summary"]
        assert survivors, "she approved it, it ran, and it was killed anyway"
    finally:
        _reap(marker)
        events.unregister(sid)
        deferred_exec.forget_session(sid)
