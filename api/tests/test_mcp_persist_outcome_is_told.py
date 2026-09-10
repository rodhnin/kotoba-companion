"""A voice audit found "Connected" said over a failed persistence, leaving the config pointing at a
credential that does not exist. The session really works either way, since connecting succeeded
before anything was persisted — the lie was only about what survives a restart.

If the token-store save fails, the saved config has no token after a restart, boot's connect 401s,
and the server lands in pending — and the dead pointer is strictly worse than none, since recovery
from a token in the environment only fires when the saved config has no pointer at all. If saving
the server itself fails, the server is gone entirely after a restart. The fix: the pointer is only
written when the token really landed, and the reply now carries the persistence outcome — connected
for this session, will need connecting again after a restart."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import kotoba.core.mcp.auth_flow as af


class _MCP:
    """connect succeeds (the token-retry path is past _AuthRequired by the time persistence runs)."""

    def __init__(self):
        self.server_tools = {}

    async def connect(self, name, cfg):
        return ["srv__alpha", "srv__beta"]

    async def disconnect(self, name):
        self.server_tools.pop(name, None)


class _BadDB:
    async def save_key(self, name, value):
        raise OSError("keystore locked")


class _GoodDB:
    def __init__(self):
        self.keys = {}

    async def save_key(self, name, value):
        self.keys[name] = value


def _typed(token="tok-1"):
    async def fake(sid, prompt, kind, **kw):
        return token
    return fake


_AUTH = SimpleNamespace(kind="token", reason="401")


def test_failed_token_save_drops_the_pointer_and_is_told(monkeypatch):
    saved = {}
    monkeypatch.setattr(af.config, "save_server", lambda n, c: saved.update({n: c}))
    monkeypatch.setattr(af.interaction, "request_input", _typed())
    ctx = SimpleNamespace(mcp=_MCP(), session_id="s-h6a", db=_BadDB())
    out = asyncio.run(af.handle_auth(ctx, "srv", {"url": "https://x.example/mcp"}, "", _AUTH))
    assert "auth_key" not in saved["srv"]          # never point at a credential that does not exist
    assert "Connected" in out                      # the session genuinely works
    assert "restart" in out.lower() and "this session" in out.lower()


def test_no_db_means_no_pointer_and_the_reply_says_so(monkeypatch):
    saved = {}
    monkeypatch.setattr(af.config, "save_server", lambda n, c: saved.update({n: c}))
    monkeypatch.setattr(af.interaction, "request_input", _typed())
    ctx = SimpleNamespace(mcp=_MCP(), session_id="s-h6b", db=None)
    out = asyncio.run(af.handle_auth(ctx, "srv", {"url": "https://x.example/mcp"}, "", _AUTH))
    assert "auth_key" not in saved["srv"]
    assert "restart" in out.lower()


def test_failed_server_save_is_told(monkeypatch):
    def _boom(n, c):
        raise OSError("disk full")
    monkeypatch.setattr(af.config, "save_server", _boom)
    ctx = SimpleNamespace(mcp=_MCP(), session_id="s-h6c", db=None)
    out = asyncio.run(af.finish_connect(ctx, "srv", ["srv__alpha"], {}))
    assert "Connected" in out
    assert "restart" in out.lower() and "this session" in out.lower()


def test_successful_persist_carries_no_caveat(monkeypatch):
    saved = {}
    monkeypatch.setattr(af.config, "save_server", lambda n, c: saved.update({n: c}))
    monkeypatch.setattr(af.interaction, "request_input", _typed())
    db = _GoodDB()
    ctx = SimpleNamespace(mcp=_MCP(), session_id="s-h6d", db=db)
    out = asyncio.run(af.handle_auth(ctx, "srv", {"url": "https://x.example/mcp"}, "", _AUTH))
    assert saved["srv"].get("auth_key") == "mcp:srv" and db.keys.get("mcp:srv") == "tok-1"
    assert "restart" not in out.lower()            # nothing failed → no caveat to speak


def test_mcp_install_save_failure_reaches_the_reply(monkeypatch):
    import kotoba.core.mcp.config as cfg_mod
    import kotoba.tools.action.mcp_install as mi

    fake_mcp = MagicMock()
    fake_mcp.server_tools = {}
    fake_mcp.connect = AsyncMock(return_value=["browser__navigate"])

    class _Ctx:
        mcp = fake_mcp
        session_id = "s-h6e"
        db = None

    with patch("kotoba.core.interaction.request_approval", new_callable=AsyncMock,
               return_value=(True, None)), \
         patch.object(cfg_mod, "save_server", side_effect=OSError("disk full")), \
         patch("kotoba.core.mcp_active.activate"):
        out = asyncio.run(mi.execute({"name": "browser"}, _Ctx()))
    assert out and "Connected" in out
    assert "restart" in out.lower() and "this session" in out.lower()
