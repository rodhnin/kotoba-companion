"""A queued connect that ran out of time is not a server with no tools.

MCP ops go through one serialized queue and each gets its own full _CONNECT_TIMEOUT inside the owner
task, so a dead server ahead of it eats the caller's window. `_submit` returned [] on timeout, which
means "connected, zero tools" — so `finish_connect` told the user it offered nothing AND disconnected
it, while the queued op went on to connect and register every tool seconds later.
"""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.mcp.client as mc


def test_a_connect_timeout_raises_instead_of_reporting_an_empty_toolset(monkeypatch):
    mgr = mc.MCPManager()
    monkeypatch.setattr(mc, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(mc, "_CONNECT_TIMEOUT", 0.05)

    async def never_ready(self):
        self._cmd_q = asyncio.Queue()
        self._owner_task = object()   # present, but nothing consumes the queue

    monkeypatch.setattr(mc.MCPManager, "_ensure_owner", never_ready)

    async def go():
        with pytest.raises(mc.MCPBusy):
            await mgr.connect("github", {"command": "npx", "args": []})

    asyncio.run(go())


def test_only_connect_raises_teardown_stays_a_quiet_none():
    """Teardown is best-effort by design — only connect carries a user-facing meaning. Source check
    instead of a real 12s wait: the branch is one line and waiting for it would slow the suite."""
    import inspect

    src = inspect.getsource(mc.MCPManager._submit)
    assert 'if op == "connect":' in src
    assert "raise MCPBusy" in src
    assert "return None" in src, "non-connect ops must still return quietly"


def test_the_busy_message_never_claims_the_server_was_empty(monkeypatch):
    """mcp_install must not persist, activate, or say "no tools" for a server still coming up."""
    import types

    import kotoba.tools.action.mcp_install as mi

    saved: list = []
    monkeypatch.setattr("kotoba.core.mcp.config.save_server", lambda *a, **k: saved.append(a))

    class _MCP:
        server_tools: dict = {}

        async def connect(self, name, cfg):
            raise mc.MCPBusy("still queued")

    async def approve(session_id, prompt, **kw):
        return True, False

    monkeypatch.setattr("kotoba.core.interaction.request_approval", approve)

    ctx = types.SimpleNamespace(mcp=_MCP(), session_id="s", mode="work")

    async def go():
        return await mi.execute({"name": "github"}, ctx)

    out = asyncio.run(go())
    assert out is not None, "a busy connect must say something, not fall through to a silent fail"
    assert "didn't offer any tools" not in out
    assert "longer than usual" in out
    assert not saved, "a server still coming up must not be persisted as installed"
