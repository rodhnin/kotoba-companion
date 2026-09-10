"""Audit — the unauthed mutation endpoints never 500 on hostile/garbage payloads (they 4xx or no-op
gracefully), and auth is still enforced on the one protected route. Single-user self-hosted; prod
multi-user still needs auth on /api/* (documented), but robustness must hold regardless.
"""
from __future__ import annotations


import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "ep.db"))
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    from fastapi.testclient import TestClient
    import kotoba.server as main
    with TestClient(main.app) as c:
        yield c


# Every degenerate payload below must NOT produce a 5xx.
_CASES = [
    ("POST", "/api/settings/soul", {"personality": {"nested": True}}),  # ignored (allowlist) → ok
    ("POST", "/api/settings/soul", {"name": 999, "language": ["a"]}),
    ("POST", "/api/settings/toolset", {}),
    ("POST", "/api/settings/toolset", {"name": "../etc", "enabled": "maybe"}),
    ("POST", "/api/session/s1/mute", {}),
    ("POST", "/api/session/s1/mute", {"muted": "yes please"}),
    ("POST", "/api/session/s1/input", {"weird": [1, 2, 3]}),
    ("DELETE", "/api/mcp/does-not-exist", None),
    ("DELETE", "/api/keys/ghost", None),
    ("DELETE", "/api/cron/ghost", None),
    ("DELETE", "/api/memory/topic/ghost", None),
]


@pytest.mark.parametrize("method,path,body", _CASES)
def test_no_5xx_on_garbage(client, method, path, body):
    r = client.request(method, path, json=body) if body is not None else client.request(method, path)
    assert r.status_code < 500, f"{method} {path} → {r.status_code}: {r.text[:120]}"


def test_onboarding_endpoint_is_gone(client):
    # Nothing called it: the CLI first-run wizard writes those fields in-process.
    assert client.post("/api/onboarding/setup", json={"field": "user_name", "value": "x"}).status_code == 404


def test_chat_requires_auth(client):
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_chat_auth_rejects_wrong_and_partial_bearer(client):
    # constant-time compare (hmac) — a wrong bearer, and a right-prefix guess, both 401 (no accept, no crash)
    for h in ("Bearer wrong", "Bearer ", "k", "Bearer kk", "Basic k", "Bearer k2"):
        r = client.post("/v1/chat/completions", headers={"Authorization": h},
                        json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 401, f"{h!r} → {r.status_code}"


def test_chat_auth_accepts_the_scheme_in_any_case(client):
    """RFC 7235: the scheme is case-insensitive, and the /api gate always read it that way. /v1 did not,
    so one client got two different answers depending on which surface it called."""
    r = client.post("/v1/chat/completions", headers={"Authorization": "bearer k"},
                    json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code != 401


def test_files_raw_html_has_locked_csp(client, tmp_path, monkeypatch):
    # A served HTML preview must carry a CSP that blocks fetch/XHR/forms so an injected page can't
    # exfiltrate/delete the library.
    from kotoba.core import file_library
    monkeypatch.setattr(file_library, "library_dir", lambda: tmp_path)
    (tmp_path / "page.html").write_text("<html><body>hi</body></html>")
    # first hit carries the token → 302 sets cookie; follow to the clean URL which serves the file
    r = client.get("/api/files/raw/page.html?token=k", follow_redirects=True)
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "connect-src 'none'" in csp and "form-action 'none'" in csp
    # A real subresource gets no CSP restriction. This used to probe with data.txt, on the belief that
    # "not HTML" means "not a script host" — the belief that once served .svg and .xhtml bare. The
    # rule is now inert-or-sandboxed, and text/plain is the fallback for EVERY unknown extension, so it
    # is sandboxed too; a stylesheet is what actually has to stay unrestricted.
    (tmp_path / "style.css").write_text("body{}")
    r2 = client.get("/api/files/raw/style.css?token=k", follow_redirects=True)
    assert "connect-src 'none'" not in r2.headers.get("content-security-policy", "")


def test_settings_soul_ignores_unknown_fields_and_stringifies(client):
    # personality prose is NOT settable here (allowlist name/language/voice_id) → can't poison the
    # config into a non-string that would crash prompt building.
    r = client.post("/api/settings/soul", json={"personality": {"x": 1}, "name": 42})
    assert r.status_code == 200
    s = client.get("/api/settings").json()
    assert isinstance(s["personality"]["name"], str)  # stored as a string, never a dict
