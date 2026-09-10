"""POST /api/mcp/connect with a token connects a PENDING (needs-auth) server: persists the token in the
credential store, saves the server with an auth_key pointer (not the token), and clears it from pending."""
from __future__ import annotations

import pytest

_AUTH = {"Authorization": "Bearer k"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "auth.db"))
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(tmp_path / "mcp.yaml"))
    monkeypatch.setenv("KOTOBA_PENDING_MCP", str(tmp_path / "pending_mcp.yaml"))
    from fastapi.testclient import TestClient
    import kotoba.server as main
    with TestClient(main.app) as c:
        yield c, main


class _FakeMCP:
    def __init__(self):
        self.server_tools = {}
        self.connected = []

    async def connect(self, name, cfg):
        self.connected.append((name, cfg))
        self.server_tools[name] = ["notion__read", "notion__search"]
        return self.server_tools[name]

    async def disconnect(self, name):
        self.server_tools.pop(name, None)


def test_connect_pending_with_token(client, monkeypatch):
    c, main = client
    from kotoba.core.mcp import config, pending

    fake = _FakeMCP()
    main.app.state.mcp = fake
    pending.record("notion", {"url": "https://notion/mcp"}, "needs a token", "token", "Notion.")

    r = c.post("/api/mcp/connect", headers=_AUTH, json={"name": "notion", "token": "tok-xyz"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # connected with a Bearer header; token NOT in the persisted cfg (pointer only); pending cleared.
    _, connect_cfg = fake.connected[0]
    assert connect_cfg["headers"]["Authorization"] == "Bearer tok-xyz"
    saved = config.load_servers().get("notion") or {}
    assert saved.get("auth_key") == "mcp:notion" and "tok-xyz" not in str(saved)
    assert pending.get("notion") is None

    # no longer shown as pending in the Settings UI
    s = c.get("/api/settings", headers=_AUTH).json()
    assert all(p["name"] != "notion" for p in s.get("pending_mcp", []))


def test_connect_known_without_credential_returns_clear_error(client, monkeypatch):
    """The old bug: clicking Connect on a known server that needs a credential (github needs a PAT) did
    nothing — the backend 500'd and the frontend swallowed it. Now it returns a 400 with a clear message
    (and never even attempts the connect)."""
    c, main = client
    fake = _FakeMCP()
    main.app.state.mcp = fake
    # Clear ALL accepted PAT aliases — "no credential" must mean none of them is set, or the alias-aware
    # connect gate (correctly) finds GITHUB_MCP_TOKEN (which load_dotenv pulls from api/.env into the test env).
    for _v in ("GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_MCP_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(_v, raising=False)

    r = c.post("/api/mcp/connect", headers=_AUTH, json={"name": "github"})
    assert r.status_code == 400, r.text
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in r.json()["detail"]
    assert fake.connected == []  # never attempted the connect


def test_connect_known_oauth_vendor_records_pending(client, monkeypatch):
    """Connecting a known OAuth vendor (Notion) by name from Settings can't finish in one call (a
    browser sign-in is needed), so instead of a dead-end 400 it RECORDS the server as pending-oauth (with its
    official url, secret-free) and returns pending:true. The Settings UI then shows a 'Sign in' button."""
    c, main = client
    from kotoba.core.mcp import pending

    fake = _FakeMCP()
    main.app.state.mcp = fake

    r = c.post("/api/mcp/connect", headers=_AUTH, json={"name": "notion"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body.get("pending") is True and body["name"] == "notion"
    assert fake.connected == []  # never attempted a doomed connect

    pend = pending.get("notion")
    assert pend is not None and pend.get("kind") == "oauth"
    assert pend["cfg"].get("url") == "https://mcp.notion.com/mcp"

    # surfaces in Settings under "Needs connection" as an oauth row
    s = c.get("/api/settings", headers=_AUTH).json()
    assert any(p["name"] == "notion" and p["kind"] == "oauth" for p in s.get("pending_mcp", []))


def test_connect_known_connect_failure_surfaces_502(client, monkeypatch):
    """If a known server's connect raises, it's a clean 502 with detail — not an opaque 500 the UI eats."""
    c, main = client

    class _BoomMCP(_FakeMCP):
        async def connect(self, name, cfg):
            raise RuntimeError("npx not found")

    main.app.state.mcp = _BoomMCP()
    r = c.post("/api/mcp/connect", headers=_AUTH, json={"name": "filesystem"})
    assert r.status_code == 502, r.text
    assert "couldn't connect" in r.json()["detail"]


def test_the_first_row_of_the_dropdown_never_draws_a_button_that_can_only_400(client, monkeypatch):
    """Google Calendar is a COMMAND server, and `/api/mcp/oauth/start` needs a url: `oauth:google` on it
    recorded a pending entry with `{"url": ""}`, Settings drew "Sign in", and the only answer that
    button could ever get was 400 "server has no url". It is the first row of the shipped dropdown, so
    it is the first thing a stranger tries.

    Its real mechanism is Google's own OAuth, run locally by the server (a Desktop-app client JSON in
    GOOGLE_OAUTH_CREDENTIALS, then one `npx @cocal/google-calendar-mcp auth`) — nothing an MCP client
    can drive. So there is no sign-in to offer, and the refusal has to name what a person must do."""
    c, main = client
    from kotoba.core.mcp import pending

    fake = _FakeMCP()
    main.app.state.mcp = fake
    monkeypatch.delenv("GOOGLE_OAUTH_CREDENTIALS", raising=False)

    r = c.post("/api/mcp/connect", headers=_AUTH, json={"name": "google calendar"})
    assert pending.get("google-calendar") is None, "a sign-in button was recorded for a url-less server"
    assert fake.connected == [], "never attempted a connect with no credentials to make it with"
    assert r.status_code == 400, r.text
    assert "GOOGLE_OAUTH_CREDENTIALS" in r.json()["detail"], r.text

    s = c.post("/api/mcp/oauth/start", headers=_AUTH, json={"name": "google-calendar"})
    assert "no url" not in s.text, s.text
