"""Sandbox acquisition: a companion turn CAN now get a sandbox (it has terminal/code tools), but it SHARES
the per-session sandbox and never tears down one the background work-runner owns."""
from __future__ import annotations

import asyncio

from kotoba.tools import ToolContext


def test_no_workdir_no_sandbox():
    # Nothing to jail to → no sandbox, in either mode.
    ctx = ToolContext(db=None, session_id="s1", mode="companion", workdir=None)
    assert asyncio.run(ctx.ensure_sandbox()) is None


def test_companion_with_workdir_acquires_session_sandbox(tmp_path, monkeypatch):
    # Companion now runs quick commands → it acquires a sandbox when a workdir is set (default local
    # backend). It's session-owned, so ctx.cleanup() must NOT kill it (only a sessionless ctx-owned one).
    monkeypatch.setenv("KOTOBA_SANDBOX", "local")

    async def go():
        ctx = ToolContext(db=None, session_id="iso-s2", mode="companion", workdir=tmp_path)
        sb = await ctx.ensure_sandbox()
        assert sb is not None              # companion acquired one
        assert ctx._session_sandbox is True  # it came from the per-session registry (shared, not owned)
        await ctx.cleanup()                # must NOT kill a session-owned sandbox
        from kotoba.core import session_sandbox
        still = session_sandbox._live.get("iso-s2")
        assert still is not None           # survived cleanup (work-runner could still be using it)
        await session_sandbox.release("iso-s2")

    asyncio.run(go())


def test_none_backend_yields_no_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    ctx = ToolContext(db=None, session_id="iso-s3", mode="companion", workdir=tmp_path)
    assert asyncio.run(ctx.ensure_sandbox()) is None  # no backend → graceful no-op
