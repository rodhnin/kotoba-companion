"""mcp_install must handle an OAuth/token server gracefully (not a silent FAIL). When connecting a
known server raises _AuthRequired, mcp_install reuses the shared auth_flow: oauth → record pending + point to
Settings; token → ask for a key, retry, persist. Before this, the generic `except Exception: return None`
turned an OAuth need into an in-character "couldn't connect" with no pending row and no guidance."""
from __future__ import annotations

import asyncio

import kotoba.tools.action.mcp_install as mi


class _DB:
    def __init__(self):
        self.keys = {}

    async def save_key(self, name, value):
        self.keys[name] = value


class _Ctx:
    def __init__(self, mcp, db=None):
        self.mcp = mcp
        self.session_id = "s1"
        self.db = db or _DB()


class _MCPAuth:
    """Connect raises _AuthRequired (needs auth); retry (with a token) succeeds."""

    def __init__(self, kind):
        self.server_tools = {}
        self.calls = []
        self.kind = kind

    async def connect(self, name, cfg):
        from kotoba.core.mcp.client import _AuthRequired
        self.calls.append((name, cfg))
        if len(self.calls) == 1:
            raise _AuthRequired(name, "needs a browser sign-in", self.kind, cfg)
        self.server_tools[name] = ["notion__create-pages", "notion__search"]
        return self.server_tools[name]

    async def disconnect(self, name):
        self.server_tools.pop(name, None)


def test_mcp_install_oauth_records_pending_and_points_to_settings(monkeypatch):
    recorded = []
    monkeypatch.setattr(mi, "ctx", None, raising=False)  # no-op guard; ctx comes from arg
    import kotoba.core.interaction as interaction

    async def _approve(sid, text, **k):  # install now asks first — approve so we reach connect()
        return (True, False)
    monkeypatch.setattr(interaction, "request_approval", _approve)
    import kotoba.core.mcp.pending as pending
    monkeypatch.setattr(pending, "record",
                        lambda name, cfg, reason, kind, desc="": recorded.append((name, kind)))

    mcp = _MCPAuth("oauth")
    out = asyncio.run(mi.execute({"name": "notion"}, _Ctx(mcp)))

    assert len(mcp.calls) == 1                       # oauth → no token retry
    assert ("notion", "oauth") in recorded           # left in pending for the Settings Sign-in
    assert "settings" in out.lower() or "sign" in out.lower()
    assert out is not None                           # NOT a silent FAIL


def test_mcp_install_token_asks_retries_persists(monkeypatch):
    import kotoba.core.mcp.config as config
    import kotoba.core.interaction as interaction
    saved, cleared = {}, []

    async def _typed(sid, prompt, kind, **kw):
        return "tok-xyz"
    monkeypatch.setattr(interaction, "request_input", _typed)

    async def _approve(sid, text, **k):  # install now asks first — approve so we reach connect()
        return (True, False)
    monkeypatch.setattr(interaction, "request_approval", _approve)
    monkeypatch.setattr(config, "save_server", lambda n, c: saved.update({n: c}))
    import kotoba.core.mcp.pending as pending
    monkeypatch.setattr(pending, "clear", lambda n: cleared.append(n))

    mcp = _MCPAuth("token")
    ctx = _Ctx(mcp)
    out = asyncio.run(mi.execute({"name": "notion"}, ctx))

    assert len(mcp.calls) == 2                                  # initial + retry with token
    _, retry_cfg = mcp.calls[1]
    assert retry_cfg["headers"]["Authorization"] == "Bearer tok-xyz"
    assert ctx.db.keys.get("mcp:notion") == "tok-xyz"          # persisted in credential store
    assert saved.get("notion", {}).get("auth_key") == "mcp:notion" and "tok-xyz" not in str(saved)
    assert "tok-xyz" not in out
