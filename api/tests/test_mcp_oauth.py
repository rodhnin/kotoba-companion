"""MCP OAuth ("browser sign-in"): obtain a token via the SDK OAuth flow, then connect the server through
the SAME Bearer path a pasted API key uses. Covers the flow orchestration (start → callback → finish),
the persisted refresh record, the refresh-token grant, the refresh loop, and the three HTTP endpoints
(incl. that the callback is reachable WITHOUT the API auth gate)."""
from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest

from mcp.shared.auth import OAuthToken


# ─── Fakes for the SDK + httpx, so the flow runs with no real network ────────────────────────────────
class _FakeProvider:
    """Stands in for mcp.client.auth.OAuthClientProvider — captures the handlers + exposes a .context the
    way the real provider does (oauth_metadata.token_endpoint, client_info.client_id/secret)."""

    def __init__(self, *, server_url, client_metadata, storage, redirect_handler, callback_handler, **kw):
        self.server_url = server_url
        self.storage = storage
        self.redirect_handler = redirect_handler
        self.callback_handler = callback_handler
        self.context = SimpleNamespace(
            oauth_metadata=SimpleNamespace(token_endpoint="https://auth.example/token"),
            client_info=SimpleNamespace(client_id="cid-123", client_secret=None),
        )


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient(auth=provider): a GET drives the SDK flow — emit the auth URL (with
    a `state`), block on the callback, then store the exchanged token in the provider's storage."""

    def __init__(self, *, auth=None, timeout=None, **kw):
        self.auth = auth

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        prov = self.auth
        await prov.redirect_handler("https://auth.example/authorize?state=STATE-XYZ&client_id=cid-123")
        code, _state = await prov.callback_handler()
        await prov.storage.set_tokens(OAuthToken(
            access_token="acc-" + code, token_type="Bearer", expires_in=3600,
            refresh_token="ref-1", scope="read",
        ))
        return SimpleNamespace(status_code=200)


class _FakeDB:
    def __init__(self):
        self.keys: dict[str, str] = {}

    async def save_key(self, name, value):
        self.keys[name] = value

    async def get_key(self, name):
        return self.keys.get(name)

    async def delete_key(self, name):
        self.keys.pop(name, None)

    async def list_key_names(self):
        return [{"name": k} for k in self.keys]


@pytest.fixture
def oauth(monkeypatch):
    import kotoba.core.mcp.oauth as mod
    importlib.reload(mod)  # clear any module-level _flows / _state_to_flow between tests
    monkeypatch.setattr(mod, "OAuthClientProvider", _FakeProvider)
    monkeypatch.setattr(mod, "_OAUTH_AVAILABLE", True)
    monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
    return mod


# ─── Flow orchestration ──────────────────────────────────────────────────────────────────────────────
def test_full_flow_start_callback_finish(oauth):
    """Start parks a flow behind its `state`, the browser's callback wakes it, finish persists the result.

    Two records come out of one sign-in, on purpose: the Bearer under `mcp:<name>`, which is the key
    `config.hydrate_auth` injects into the live server config, and the refresh material under
    `mcp_oauth:<name>`, which only the refresh loop reads. Splitting them means a server config can point
    at a credential by name without ever holding the token or the refresh token."""
    db = _FakeDB()

    async def scenario():
        flow_id, auth_url = await oauth.start_oauth("notion", "https://notion/mcp",
                                                    "https://pub/api/mcp/oauth/callback")
        assert "STATE-XYZ" in auth_url
        assert oauth.resolve_callback("STATE-XYZ", "the-code") is True
        record = await oauth.finish_oauth(flow_id, db)
        assert record["access_token"] == "acc-the-code"
        return record

    record = asyncio.run(scenario())
    assert db.keys["mcp:notion"] == "acc-the-code"
    saved = json.loads(db.keys["mcp_oauth:notion"])
    assert saved["refresh_token"] == "ref-1"
    assert saved["token_endpoint"] == "https://auth.example/token"
    assert saved["client_id"] == "cid-123"
    assert saved["expires_at"] > 0


def test_callback_with_unknown_state_is_ignored(oauth):
    """A `state` matching no parked flow is refused — the callback is public, so anyone can call it."""
    assert oauth.resolve_callback("no-such-state", "x") is False


def test_finish_unknown_flow_raises(oauth):
    with pytest.raises(Exception):
        asyncio.run(oauth.finish_oauth("ghost", _FakeDB()))


def test_state_mapping_cleaned_after_finish(oauth):
    """Finishing drops both the flow and its state mapping, so a replayed callback finds nothing."""
    async def scenario():
        flow_id, _ = await oauth.start_oauth("n", "https://n/mcp", "https://pub/cb")
        oauth.resolve_callback("STATE-XYZ", "c")
        await oauth.finish_oauth(flow_id, _FakeDB())

    asyncio.run(scenario())
    assert oauth._flows == {} and oauth._state_to_flow == {}


# ─── Refresh-token grant ─────────────────────────────────────────────────────────────────────────────
def test_refresh_updates_tokens(oauth, monkeypatch):
    """The refresh grant replaces the stored Bearer, and a rotated refresh token is honoured.

    Servers that rotate the refresh token on every use invalidate the old one, so keeping the original
    would break the next refresh rather than the current one — a failure that only shows up an hour
    later."""
    db = _FakeDB()
    db.keys["mcp:n"] = "old-access"
    db.keys["mcp_oauth:n"] = json.dumps({
        "refresh_token": "ref-old", "expires_at": 1.0, "token_endpoint": "https://auth.example/token",
        "client_id": "cid-123", "client_secret": None, "scope": "read",
    })

    class _RefreshClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, data=None, headers=None):
            assert data["grant_type"] == "refresh_token" and data["refresh_token"] == "ref-old"
            return SimpleNamespace(status_code=200, json=lambda: {
                "access_token": "new-access", "refresh_token": "ref-new", "expires_in": 3600, "scope": "read",
            })

    monkeypatch.setattr(oauth.httpx, "AsyncClient", _RefreshClient)
    ok = asyncio.run(oauth.refresh(db, "n"))
    assert ok is True
    assert db.keys["mcp:n"] == "new-access"
    saved = json.loads(db.keys["mcp_oauth:n"])
    assert saved["refresh_token"] == "ref-new"
    assert saved["expires_at"] > 1000


def test_refresh_without_record_returns_false(oauth):
    """A server signed in with a pasted key has no refresh record: that is not an error."""
    assert asyncio.run(oauth.refresh(_FakeDB(), "missing")) is False


def test_refresh_http_error_returns_false(oauth, monkeypatch):
    """A rejected refresh leaves the stored record exactly as it was.

    Overwriting it with a half-result would turn one recoverable failure — a flaky auth server, a
    revoked-then-restored grant — into a permanent one, because the material needed to try again is
    gone."""
    db = _FakeDB()
    db.keys["mcp_oauth:n"] = json.dumps({
        "refresh_token": "r", "token_endpoint": "https://auth.example/token", "expires_at": 1.0,
    })

    class _ErrClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            return SimpleNamespace(status_code=401, json=lambda: {})

    monkeypatch.setattr(oauth.httpx, "AsyncClient", _ErrClient)
    assert asyncio.run(oauth.refresh(db, "n")) is False
    assert json.loads(db.keys["mcp_oauth:n"])["refresh_token"] == "r"


def test_expiring_soon():
    """The refresh loop's trigger: expiring inside the window is due, and a record with no expiry never
    is (a token that never declared a lifetime is not one we can second-guess)."""
    import time
    import kotoba.core.mcp.oauth as mod
    assert mod.expiring_soon({"expires_at": time.time() + 30}) is True
    assert mod.expiring_soon({"expires_at": time.time() + 9999}) is False
    assert mod.expiring_soon({}) is False


# ─── Refresh loop reconnects the live session ───────────────────────────────────────────────────────
def test_refresh_loop_reconnects_expiring_server(oauth, monkeypatch, tmp_path):
    """Refreshing a token is only half the job: the LIVE session is still holding the expired one.

    So the loop reconnects the server with the fresh Bearer already substituted in. The `auth_key`
    pointer is resolved away in the process — the config on disk names a credential, the config handed
    to a live connection carries the header."""
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(tmp_path / "mcp.yaml"))
    from kotoba.core.mcp import config
    config.save_server("n", {"url": "https://n/mcp", "auth_key": "mcp:n"})

    db = _FakeDB()
    db.keys["mcp:n"] = "old"
    db.keys["mcp_oauth:n"] = json.dumps({
        "refresh_token": "r", "token_endpoint": "https://auth.example/token", "expires_at": 1.0,
        "client_id": "c",
    })

    async def fake_refresh(_db, name):
        _db.keys["mcp:n"] = "fresh-access"
        return True

    monkeypatch.setattr(oauth, "refresh", fake_refresh)

    reconnected = []

    class _FakeMCP:
        async def reconnect(self, name, cfg):
            reconnected.append((name, cfg))

    async def run_one_tick():
        task = asyncio.create_task(oauth.refresh_loop(db, _FakeMCP(), interval=0.01))
        await asyncio.sleep(0.08)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_one_tick())
    assert reconnected, "expiring server should be reconnected"
    name, cfg = reconnected[0]
    assert name == "n"
    assert cfg["headers"]["Authorization"] == "Bearer fresh-access"
    assert "auth_key" not in cfg


# ─── HTTP endpoints (start / callback / finish) ─────────────────────────────────────────────────────
_AUTH = {"Authorization": "Bearer k"}


class _EndpointMCP:
    def __init__(self):
        self.connected = []

    async def connect(self, name, cfg):
        self.connected.append((name, cfg))
        return ["notion__read", "notion__search"]

    async def disconnect(self, name):
        ...


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "oauth.db"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(tmp_path / "mcp.yaml"))
    monkeypatch.setenv("KOTOBA_PENDING_MCP", str(tmp_path / "pending_mcp.yaml"))
    monkeypatch.setenv("KOTOBA_PUBLIC_URL", "https://pub.example")
    from fastapi.testclient import TestClient
    import kotoba.server as main
    with TestClient(main.app) as c:
        yield c, main


def test_start_endpoint_returns_auth_url(client, monkeypatch):
    """The redirect_uri the endpoint builds is KOTOBA_PUBLIC_URL plus the callback route, because the
    authorisation server has to be able to reach it from outside."""
    c, main = client
    from kotoba.core.mcp import oauth, pending
    pending.record("notion", {"url": "https://notion/mcp"}, "needs sign-in", "oauth", "Notion.")

    async def fake_start(name, url, redirect_uri):
        assert name == "notion" and url == "https://notion/mcp"
        assert redirect_uri == "https://pub.example/api/mcp/oauth/callback"
        return "flow-1", "https://auth.example/authorize?state=S1"

    monkeypatch.setattr(oauth, "oauth_available", lambda: True)
    monkeypatch.setattr(oauth, "start_oauth", fake_start)

    r = c.post("/api/mcp/oauth/start", headers=_AUTH, json={"name": "notion"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["flow_id"] == "flow-1" and "state=S1" in body["auth_url"]


def test_start_unknown_pending_404(client, monkeypatch):
    """Sign-in can only be started for a server already recorded as pending — not for an arbitrary name."""
    c, main = client
    from kotoba.core.mcp import oauth
    monkeypatch.setattr(oauth, "oauth_available", lambda: True)
    r = c.post("/api/mcp/oauth/start", headers=_AUTH, json={"name": "ghost"})
    assert r.status_code == 404


def test_finish_endpoint_connects_and_persists(client, monkeypatch):
    """A finished sign-in connects with the obtained Bearer and saves the server as a normal one.

    What lands on disk is a POINTER (`auth_key`), never the token itself, and the server stops being
    pending."""
    c, main = client
    from kotoba.core.mcp import config, oauth, pending
    fake = _EndpointMCP()
    main.app.state.mcp = fake
    pending.record("notion", {"url": "https://notion/mcp"}, "needs sign-in", "oauth", "Notion.")

    async def fake_finish(flow_id, db):
        assert flow_id == "flow-1"
        await db.save_key("mcp:notion", "acc-token")
        await db.save_key("mcp_oauth:notion", json.dumps({"refresh_token": "r"}))
        return {"access_token": "acc-token"}

    monkeypatch.setattr(oauth, "finish_oauth", fake_finish)

    r = c.post("/api/mcp/oauth/finish", headers=_AUTH, json={"name": "notion", "flow_id": "flow-1"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    _, cfg = fake.connected[0]
    assert cfg["headers"]["Authorization"] == "Bearer acc-token"
    saved = config.load_servers().get("notion") or {}
    assert saved.get("auth_key") == "mcp:notion" and "acc-token" not in str(saved)
    assert pending.get("notion") is None


def test_finish_rolls_back_keys_when_connect_fails(client, monkeypatch):
    """finish_oauth persists the token before the connect attempt; a failed connect must leave NO orphan
    credential behind (else refresh_loop would retry a dead server's token forever)."""
    c, main = client
    from kotoba.core.mcp import oauth, pending

    class _DeadMCP:
        async def connect(self, name, cfg):
            raise RuntimeError("server unreachable")
        async def disconnect(self, name):
            ...

    main.app.state.mcp = _DeadMCP()
    pending.record("notion", {"url": "https://notion/mcp"}, "needs sign-in", "oauth", "Notion.")
    db = main.app.state.db

    async def fake_finish(flow_id, _db):
        await _db.save_key("mcp:notion", "acc-token")
        await _db.save_key("mcp_oauth:notion", json.dumps({"refresh_token": "r"}))
        return {"access_token": "acc-token"}

    monkeypatch.setattr(oauth, "finish_oauth", fake_finish)

    r = c.post("/api/mcp/oauth/finish", headers=_AUTH, json={"name": "notion", "flow_id": "f"})
    assert r.status_code == 502
    assert asyncio.run(db.get_key("mcp:notion")) is None
    assert asyncio.run(db.get_key("mcp_oauth:notion")) is None


def test_callback_endpoint_resolves_flow(client, monkeypatch):
    """The callback hands `code` and `state` straight to the parked flow and shows the user a done page."""
    c, main = client
    from kotoba.core.mcp import oauth
    seen = {}

    def fake_resolve(state, code):
        seen["state"], seen["code"] = state, code
        return True

    monkeypatch.setattr(oauth, "resolve_callback", fake_resolve)
    r = c.get("/api/mcp/oauth/callback?code=abc&state=S1")
    assert r.status_code == 200
    assert "Signed in" in r.text
    assert seen == {"state": "S1", "code": "abc"}


def test_callback_bypasses_auth_gate(client, monkeypatch):
    """With the API gate ON, the callback must still be reachable WITHOUT a token (the auth server can't
    send ours) — but other /api/* routes must still 401."""
    c, main = client
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "secret-word")
    from kotoba.core.mcp import oauth
    monkeypatch.setattr(oauth, "resolve_callback", lambda state, code: True)

    r = c.get("/api/mcp/oauth/callback?code=abc&state=S1")
    assert r.status_code != 401, r.text
    assert c.get("/api/settings").status_code == 401
