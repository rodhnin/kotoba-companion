"""shell and execute_code run in the sandbox; a dangerous shell command needs approval.

Against the LOCAL sandbox, which is what a fresh install runs. Gated on a Docker daemon these five
never ran under the daemon at all — the suite pins the shipped default, so on a machine that happened
to have Docker they ran on the host anyway, and everywhere else they were skipped. Docker's own
isolation is somebody else's file, behind the network opt-in.
"""
from __future__ import annotations

import asyncio

from conftest import posix_only

from kotoba.core.approval import ApprovalGate
from kotoba.tools import ToolContext
from kotoba.tools.action import execute_code, shell


def _ctx(tmp_path, **kw):
    # channel="text" as the work runner sets it: whether an approval BLOCKS inline or is deferred to an
    # SSE card is a property of the transport, not of the mode (a live ElevenLabs turn is the only thing
    # that cannot wait). Leaving it unset would default to "voice" and defer.
    kw.setdefault("channel", "text")
    return ToolContext(db=None, session_id="t", workdir=tmp_path, mode="work", **kw)


def _run(ctx, coro_factory):
    async def go():
        try:
            return await coro_factory()
        finally:
            await ctx.cleanup()

    return asyncio.run(go())


def test_execute_code_runs_in_sandbox(tmp_path):
    ctx = _ctx(tmp_path)
    out = _run(ctx, lambda: execute_code.execute({"code": "print(6 * 7)"}, ctx))
    assert "42" in out and "exit=0" in out


@posix_only("a POSIX shell")
def test_shell_echo_runs(tmp_path):
    ctx = _ctx(tmp_path, approval=ApprovalGate())
    out = _run(ctx, lambda: shell.execute({"command": "echo hi-there"}, ctx))
    assert "hi-there" in out and "exit=0" in out


@posix_only("a POSIX shell")
def test_dangerous_shell_denied_by_default(tmp_path):
    """The deletion must not happen, which is a different claim from a refusal being printed.

    Asserting that no container was provisioned could not fail: the shipped sandbox provisions none
    either way, so the guard read as green whether the files survived or not."""
    (tmp_path / "doomed").mkdir()
    (tmp_path / "doomed" / "keep.txt").write_text("still here")
    ctx = _ctx(tmp_path, approval=ApprovalGate())

    out = _run(ctx, lambda: shell.execute({"command": "rm -rf doomed"}, ctx))

    assert (tmp_path / "doomed" / "keep.txt").read_text() == "still here"
    assert ("held off" in out.lower()) or ("risky" in out.lower())


@posix_only("a POSIX shell")
def test_dangerous_shell_allowed_when_approved(tmp_path):
    async def yes(action, risk, family=None):
        return True

    (tmp_path / "doomed").mkdir()
    (tmp_path / "doomed" / "gone.txt").write_text("not for long")
    ctx = _ctx(tmp_path, approval=ApprovalGate(ask=yes))

    out = _run(ctx, lambda: shell.execute({"command": "rm -rf doomed && echo cleaned"}, ctx))

    assert "cleaned" in out
    assert not (tmp_path / "doomed").exists()


@posix_only("a POSIX shell")
def test_shell_and_files_share_workdir(tmp_path):
    from kotoba.tools.action import file_write

    ctx = _ctx(tmp_path, approval=ApprovalGate())

    async def both():
        await file_write.execute({"path": "data.txt", "content": "shared!"}, ctx)
        return await shell.execute({"command": "cat data.txt"}, ctx)

    assert "shared!" in _run(ctx, both)
