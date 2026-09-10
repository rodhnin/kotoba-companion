"""The terminal row's status mark, and the two live-QA findings that re-cut it.

Measured live: a command that exited non-zero drew a green check, a timed-out killed command also drew
a green check beside "timed out after 10s and was killed", and a refused action drew the same cross as
a genuine failure. One signal was to blame: the done frame carried `ok`, which answers "did the tool
hand back usable text for the model", and a failing shell's exit code and stderr count as text. Four
endings arrived as two marks, and the mark swallowing three of them claimed success.
The frame now carries `outcome`, sourced from whatever witnessed each ending: the process's own exit
code, the deferred-exec record, or the loop's own teardown for a turn cut mid-command. These tests drive
the real agentic loop over a fake model with production shell tool, approval gate, sandbox and SSE frame."""
from __future__ import annotations

import asyncio
import json

import pytest
from conftest import shell_that

from kotoba.core import events, interaction, sandbox, workspace
from kotoba.core.loop import _step_outcome
from kotoba.core.sandbox.base import note_exit, record_exits
from kotoba.tools import ToolContext

KNOWN = {"ok", "failed", "refused", "interrupted", "pending"}


class _Item:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Event:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for event in self._evs:
                yield event
        return gen()


class _Client:
    def __init__(self, turns):
        rest = list(turns)
        self.responses = type("R", (), {"create": lambda _s, **kw: _made(_Stream(rest.pop(0)))})()


def _made(value):
    async def _c():
        return value
    return _c()


class _DB:
    async def list_approved_commands(self):
        return []

    async def insert_audit_log(self, **kw):
        return None


def _shell_call(**args):
    return [_Event("response.output_item.done",
                   item=_Item(type="function_call", name="shell", call_id="c1",
                              arguments=json.dumps(args)))]


_SAID = [_Event("response.output_text.delta", delta="[happily] Ya está.")]


def _turn(monkeypatch, tmp_path, sid: str, *, approved: bool = True, **args) -> dict:
    """One real turn: the model calls `shell`, the human answers the card, she talks.

    Returns the done frame."""
    import kotoba.core.loop as loop

    async def _answer(*a, **k):
        return (approved, False)

    monkeypatch.setattr(sandbox, "backend_name", lambda: "local")
    monkeypatch.setattr(workspace, "resolve_workdir", lambda _sid: tmp_path)
    monkeypatch.setattr(interaction, "request_approval", _answer)
    monkeypatch.setattr(loop, "get_client", lambda: _Client([_shell_call(**args), _SAID]))

    async def go():
        queue = events.register(sid)
        try:
            await loop.agentic_loop([{"role": "user", "content": "hazlo"}], sid, _DB(),
                                    asyncio.Queue(), {}, mode="companion", channel="text")
        finally:
            events.unregister(sid, queue)
        return [queue.get_nowait() for _ in range(queue.qsize())]

    frames = asyncio.run(go())
    done = [f for f in frames if f.get("kind") == "step" and f.get("phase") == "done"]
    assert len(done) == 1, done
    return done[0]


def _deferred_turn(monkeypatch, tmp_path, sid: str, *, approved: bool = True, **args) -> dict:
    """The other half of the same row, on the voice path: the tool cannot hold a live turn open waiting for
    a card, so it DEFERS. The turn ends with the row parked `pending`, and core.deferred_exec answers the
    card, runs the command and completes that same id from OUTSIDE the turn — which is where the exit code
    now has to survive to, because nothing above it is listening any more. Returns the completing frame.

    The two frames are told apart by the `pending` flag and never by arrival order: a card answered as
    fast as this one lands before the turn that opened it has finished emitting its own row, which no
    human ever does and no test should be built on."""
    import kotoba.core.loop as loop
    from kotoba.core import deferred_exec, transport

    async def _answer(*a, **k):
        return (approved, False)

    monkeypatch.setattr(sandbox, "backend_name", lambda: "local")
    monkeypatch.setattr(workspace, "resolve_workdir", lambda _sid: tmp_path)
    monkeypatch.setattr(deferred_exec, "request_approval", _answer)
    monkeypatch.setattr(loop, "get_client", lambda: _Client([_shell_call(**args), _SAID]))

    async def go():
        queue = events.register(sid)
        try:
            # Deferring is the ElevenLabs turn's branch — the mark, not the channel, is what picks it.
            with transport.el_call_turn():
                await loop.agentic_loop([{"role": "user", "content": "hazlo"}], sid, _DB(),
                                        asyncio.Queue(), {}, mode="companion", channel="voice")
            await asyncio.gather(*list(deferred_exec._tasks.get(sid, ())))
        finally:
            events.unregister(sid, queue)
            deferred_exec.forget_session(sid)
        return [queue.get_nowait() for _ in range(queue.qsize())]

    done = [f for f in asyncio.run(go())
            if f.get("kind") == "step" and f.get("phase") == "done"]
    parked = [f for f in done if f.get("pending")]
    landed = [f for f in done if not f.get("pending")]
    assert len(parked) == 1 and len(landed) == 1, done
    assert parked[0]["outcome"] == "pending"
    return landed[0]


def _cancelled_mid_run(monkeypatch, tmp_path, sid: str, loop_name: str) -> tuple[list[dict], list]:
    """The fifth way a deferred row can end, and the one nothing was closing it for: the command was
    approved and is RUNNING when the user says stop. Every stop path arrives here as a cancel of the
    detached task — and a cancel landing mid-run is not an `Exception`, so it walked out past the step
    finisher and the row stayed parked on the card for the rest of the session.

    The command waits on a file it creates itself, so "it is running" is measured against the real
    process, not a sleep. Returns the frames and the task: closing the row must not turn cancellation
    into a normal return.
    Run on BOTH event loops: this path is the voice path, whose loop is uvloop — cancelling a live
    process is exactly where the two have already differed once."""
    import kotoba.core.loop as loop
    from kotoba.core import deferred_exec, transport

    async def _answer(*a, **k):
        return (True, False)

    monkeypatch.setattr(sandbox, "backend_name", lambda: "local")
    monkeypatch.setattr(workspace, "resolve_workdir", lambda _sid: tmp_path)
    monkeypatch.setattr(deferred_exec, "request_approval", _answer)
    monkeypatch.setattr(loop, "get_client", lambda: _Client(
        [_shell_call(command=shell_that("touches_then_sleeps", marker="running")), _SAID]))

    async def go():
        queue = events.register(sid)
        try:
            with transport.el_call_turn():
                await loop.agentic_loop([{"role": "user", "content": "hazlo"}], sid, _DB(),
                                        asyncio.Queue(), {}, mode="companion", channel="voice")
            for _ in range(500):
                if (tmp_path / "running").exists():
                    break
                await asyncio.sleep(0.02)
            assert (tmp_path / "running").exists(), "the approved command never started — nothing to cancel"
            tasks = list(deferred_exec._tasks.get(sid, ()))
            assert deferred_exec.cancel(sid) == 1
            await asyncio.wait(tasks, timeout=20)
        finally:
            events.unregister(sid, queue)
            deferred_exec.forget_session(sid)
        return [queue.get_nowait() for _ in range(queue.qsize())], tasks

    if loop_name != "uvloop":
        return asyncio.run(go())
    uvloop = pytest.importorskip("uvloop")
    return uvloop.run(go()) if hasattr(uvloop, "run") else uvloop.new_event_loop().run_until_complete(go())


@pytest.mark.parametrize("loop_name", ["asyncio", "uvloop"])
def test_a_deferred_command_cancelled_while_it_runs_closes_its_row(monkeypatch, tmp_path, loop_name):
    """`interrupted` is this event's word and the row already draws it — what was missing is the frame."""
    frames, _ = _cancelled_mid_run(monkeypatch, tmp_path, f"def-cut-{loop_name}", loop_name)

    done = [f for f in frames if f.get("kind") == "step" and f.get("phase") == "done"]
    landed = [f for f in done if not f.get("pending")]
    assert len(landed) == 1, f"the row was never closed — it is still parked on the card: {done}"
    assert landed[0]["outcome"] == "interrupted"
    assert landed[0]["id"] == "c1", "the row it closes is the one the turn opened"
    assert landed[0]["interrupted"] is True, "the older flag travels beside the word, as the loop's does"


@pytest.mark.parametrize("loop_name", ["asyncio", "uvloop"])
def test_closing_the_row_never_swallows_the_cancellation(monkeypatch, tmp_path, loop_name):
    """The other half, and the worse bug of the two: a teardown that ends the task normally would leave
    `/leave` and `cancel_work` believing they stopped something they did not."""
    _, tasks = _cancelled_mid_run(monkeypatch, tmp_path, f"def-raise-{loop_name}", loop_name)

    assert [t.cancelled() for t in tasks] == [True]


def test_a_deferred_command_that_failed_is_not_reported_as_done(monkeypatch, tmp_path):
    """The deferred twin of the green-mark-over-a-failure finding.

    This path never had an `outcome` at all: it sent `ok=True` for anything that ran, so an approved
    command that exited 3 completed its row with the same ✓ a clean run gets."""
    frame = _deferred_turn(monkeypatch, tmp_path, "def-failed", command=shell_that("fails"))

    assert frame["outcome"] == "failed"
    assert frame["pending"] is False


def test_a_deferred_command_killed_at_its_timeout_is_not_reported_as_done(monkeypatch, tmp_path):
    """And the deferred timeout, which is why the code cannot be read back out of the sentence: inline the
    row says `exit=124`, here it says "(exit code 124)" — two wordings of one event, and a reader that
    parses either is a reader that breaks when she rephrases it."""
    frame = _deferred_turn(monkeypatch, tmp_path, "def-killed", command=shell_that("sleeps"), timeout=1)

    assert "124" in frame["result"]
    assert frame["outcome"] == "failed"


def test_a_deferred_command_that_worked_is_still_reported_as_done(monkeypatch, tmp_path):
    frame = _deferred_turn(monkeypatch, tmp_path, "def-ok", command=shell_that("prints_deferred"))

    assert frame["outcome"] == "ok"
    assert "hi-there" in frame["result"]


def test_a_deferred_action_the_user_declined_is_not_a_failure(monkeypatch, tmp_path):
    """The refusal finding on this path: the card went unanswered or was declined, so nothing ran — and
    it completed the row with `ok=False`, which is the mark a command that ran and broke gets."""
    frame = _deferred_turn(monkeypatch, tmp_path, "def-refused", approved=False,
                           command=shell_that("touches", marker="ran-anyway"))

    assert frame["outcome"] == "refused"
    assert (tmp_path / "ran-anyway").exists() is False


def test_a_command_that_failed_is_not_reported_as_done(monkeypatch, tmp_path):
    """First measurement: `xdg-open <file that does not exist>` exited non-zero, the row printed the
    real error, and the mark stayed green. `ok` is deliberately still True here — that is the whole point:
    the tool DID hand back a usable answer, so the old signal could not have known any better."""
    frame = _turn(monkeypatch, tmp_path, "row-failed", command=shell_that("fails_naturally"))

    assert frame["ok"] is True, "the tool answered — `ok` was never the wrong value, it was the wrong question"
    assert frame["outcome"] == "failed"
    assert "exit=0" not in frame["result"]


def test_a_command_killed_at_its_timeout_is_not_reported_as_done(monkeypatch, tmp_path):
    """The two timeouts: the row read `timed out after 10s and was killed` beside a ✓. The exit code
    the sandbox sets for a kill (124) is what the mark is made of now — the words are never read."""
    frame = _turn(monkeypatch, tmp_path, "row-killed", command=shell_that("sleeps"), timeout=1)

    assert "timed out" in frame["result"] and "exit=124" in frame["result"]
    assert frame["outcome"] == "failed"


def test_a_command_that_worked_is_still_reported_as_done(monkeypatch, tmp_path):
    """The ✓ has to keep meaning something, and it now means exactly one thing: it ran and exited 0."""
    frame = _turn(monkeypatch, tmp_path, "row-ok", command=shell_that("prints"))

    assert frame["outcome"] == "ok"
    assert "hi-there" in frame["result"]


def test_refusing_is_neither_a_failure_nor_a_success(monkeypatch, tmp_path):
    """The user pressed No and nothing ran. Inline that arrived as a ✓ (the refusal sentence is text
    like any other); deferred it arrived as the same × a real failure gets. It is its own ending: nothing
    went wrong, and the user's own decision must not be drawn as a fault."""
    frame = _turn(monkeypatch, tmp_path, "row-refused", approved=False, command="rm -rf build")

    assert frame["outcome"] == "refused"
    assert frame["pending"] is False, "nothing is waiting — the answer already came"
    assert (tmp_path / "build").exists() is False


def test_a_turn_cut_mid_command_lands_as_interrupted(monkeypatch):
    """The fourth ending. It already travelled as `interrupted=True`, but only the CLI read it: the web
    store dropped the flag and told a cut turn from a failure by the English words "(interrupted)" in the
    output. It is named in `outcome` now, alongside the flag the CLI still reads."""
    import kotoba.core.loop as loop

    monkeypatch.setattr(loop, "get_client", lambda: object())

    async def cancelled_mid_tool(client, ctx, *a, **k):
        ctx._open_steps = {"call_9": "$ sleep 100"}
        raise asyncio.CancelledError()

    monkeypatch.setattr(loop, "_run_iterations", cancelled_mid_tool)

    async def go():
        queue = events.register("row-cut")
        with pytest.raises(asyncio.CancelledError):
            await loop.agentic_loop([{"role": "user", "content": "x"}], "row-cut", _DB(),
                                    asyncio.Queue(), {})
        frames = [queue.get_nowait() for _ in range(queue.qsize())]
        events.unregister("row-cut", queue)
        return frames

    step = next(f for f in asyncio.run(go())
                if f.get("kind") == "step" and f.get("phase") == "done")
    assert step["outcome"] == "interrupted"
    assert step["interrupted"] is True, "the older flag stays — cli/events_bridge reads it"


def test_a_carded_action_is_pending_and_never_anything_else():
    """A deferred action has not run, so no exit code exists to judge it by and `ok` describes only the
    "I asked you on screen" line it returned. Pending outranks every other reading until deferred_exec
    completes the same row."""
    ctx = ToolContext(db=None)
    assert _step_outcome(ctx, "c1", True, True, []) == "pending"
    assert _step_outcome(ctx, "c1", False, True, [7]) == "pending"


@pytest.mark.parametrize("ok,exits,expected", [
    (True, [0], "ok"),
    (True, [], "ok"),               # a tool that runs no process at all (a read, a memory write)
    (True, [1], "failed"),
    (True, [124], "failed"),
    (True, [0, 2], "failed"),       # any command in the call failing fails the row
    (False, [], "failed"),          # the harness itself said no: timed out, errored, returned nothing
    (False, [0], "failed"),
])
def test_the_mark_follows_the_process_not_the_prose(ok, exits, expected):
    assert _step_outcome(ToolContext(db=None), "c1", ok, False, exits) == expected


def test_every_ending_has_a_name_the_row_knows():
    """The bar: an outcome nobody anticipated must not land on ✓. This side of it is that the loop only
    ever emits words the row can draw."""
    ctx = ToolContext(db=None)
    for ok in (True, False):
        for deferred in (True, False):
            for exits in ([], [0], [1], [124]):
                assert _step_outcome(ctx, "c1", ok, deferred, exits) in KNOWN


def test_the_exit_code_is_counted_once_per_command(tmp_path):
    """The recording sandbox wraps the two entry points a tool uses, and `run_code` reaches the host
    through the backend's OWN `run` — so a snippet of Python is one command, not two."""
    ctx = ToolContext(db=None, workdir=tmp_path)

    async def go():
        with record_exits() as exits:
            sb = await ctx.ensure_sandbox()
            await sb.run_code("raise SystemExit(3)")
            await sb.run("exit 0")
        await ctx.cleanup()
        return exits

    assert asyncio.run(go()) == [3, 0]


def test_starting_the_sandbox_is_not_an_outcome(tmp_path):
    """Provisioning is not something anyone is shown. It happens on the raw backend, before the wrap, so
    a container's own create/start (which builds ExecResults of its own) can never colour a row."""
    ctx = ToolContext(db=None, workdir=tmp_path)

    async def go():
        with record_exits() as exits:
            await ctx.ensure_sandbox()
            got = list(exits)
        await ctx.cleanup()
        return got

    assert asyncio.run(go()) == []


def test_reporting_an_exit_code_with_nobody_listening_is_harmless():
    """Every caller outside a turn — the CLI's own probes, a test, a tool run straight from a script —
    executes commands with no recorder open. That must never raise."""
    note_exit(1)
