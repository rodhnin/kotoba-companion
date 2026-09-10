"""Sandbox interface — the execution backend shared by shell, file and code tools.

One Sandbox instance per session keeps a persistent working dir. `local` runs on the host in a jailed
workdir with a scrubbed env; `docker` is the opt-in isolated backend.

`record_exits` is how the EXIT CODE survives the trip to the user's screen. A tool hands the loop a
string, and "timed out after 10s and was killed" is a perfectly good one, so `ok` said yes and the
terminal drew a green ✓ over a killed command. So every model-run command reports its code into a
ContextVar-scoped list the loop opens around the call, rather than being re-read out of prose two
tools word differently. A detached run inherits that list, which is why it is capped."""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


_MAX_RECORDED = 32
_exits: ContextVar[list[int] | None] = ContextVar("kotoba_sandbox_exits", default=None)


@contextlib.contextmanager
def record_exits() -> Iterator[list[int]]:
    """Collect the exit code of every model-run command executed inside this block, in order."""
    codes: list[int] = []
    token = _exits.set(codes)
    try:
        yield codes
    finally:
        _exits.reset(token)


def note_exit(code: int) -> None:
    """Report one command's exit code to the innermost open `record_exits` block, if any."""
    codes = _exits.get()
    if codes is not None and len(codes) < _MAX_RECORDED:
        codes.append(int(code))


def exit_outcome(codes: list[int]) -> str:
    """The codes a block collected, as the word the terminal row is drawn from: `ok` or `failed`.

    Any non-zero fails the row — a tool call that runs three commands and breaks on the second did not
    succeed. No code at all is `ok`, which is a tool that ran no process (a read, a memory write) and
    not a silent failure: the caller has already answered the "did this tool work" question by its own
    means before asking this one. It lives here, next to the recorder, because the same call is judged
    at two different moments — inline by core.loop while the turn is open, and minutes later by
    core.deferred_exec once a human has approved it — and two copies of this rule would be two marks
    for one ending."""
    return "ok" if all(code == 0 for code in codes) else "failed"


@runtime_checkable
class Sandbox(Protocol):
    """Async execution backend. Implementations isolate the model's code from the host."""

    async def start(self) -> None: ...
    async def run(self, command: str, cwd: str = ".", timeout: int = 60) -> ExecResult: ...
    async def run_code(self, code: str, lang: str = "python", timeout: int = 60) -> ExecResult: ...
    async def read(self, path: str) -> bytes: ...
    async def write(self, path: str, data: bytes) -> None: ...
    async def kill(self) -> None: ...
