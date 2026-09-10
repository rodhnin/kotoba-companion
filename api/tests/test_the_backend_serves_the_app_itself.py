"""Serving the UI from the API, with the same locks the frontend server had.

The risk this file exists for: making the app reachable means widening a deny-by-default gate, and
the widening is the whole attack surface. So the pages are asked for the way a browser asks, through
the real middleware, and the interesting cases are the ones where something upstream has already
gone wrong.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kotoba.core import frontend

PASSWORD = "open-sesame"

TREE = ["app.html", "setup.html", "login.html", "404.html", "index.html",
        "_next/static/chunks/main.js", "icon.svg"]


@pytest.fixture
def built(tmp_path, monkeypatch):
    for rel in TREE:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"<!-- {rel} -->", encoding="utf-8")
    monkeypatch.setattr(frontend, "_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def client(built, monkeypatch):
    for name in ("KOTOBA_GATE_PASSWORD", "KOTOBA_GATE_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", PASSWORD)
    from kotoba import server
    server._FAILED_AUTH.clear()
    with TestClient(server.app, follow_redirects=False) as c:
        yield c


def _sign_in(c):
    c.post("/gate", json={"password": PASSWORD})
    return c


def test_the_root_sends_you_to_the_app(client):
    r = client.get("/")
    assert r.status_code == 302 and r.headers["location"] == "/app"


def test_the_app_bounces_to_the_gate_when_nobody_is_signed_in(client):
    r = client.get("/app")
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=%2Fapp"
    assert r.headers.get("cache-control") == "no-store", (
        "a cached bounce is replayed after the login and the app never opens")


def test_the_bounce_carries_where_you_were_going(client):
    r = client.get("/app?panel=files")
    assert r.headers["location"] == "/login?next=%2Fapp%3Fpanel%3Dfiles"


def test_the_bounce_keeps_a_proxy_prefix(client):
    """Routing strips the prefix; the address the browser follows must not lose it."""
    r = client.get("/app", headers={}, extensions={})
    assert r.status_code == 302
    from kotoba import server
    resp = server._login_redirect({"root_path": "/kotoba", "query_string": b""}, "/app")
    assert resp.headers["location"] == "/kotoba/login?next=%2Fkotoba%2Fapp"


def test_a_signed_in_browser_gets_the_page(client):
    _sign_in(client)
    r = client.get("/app")
    assert r.status_code == 200 and "app.html" in r.text


def test_the_login_screen_is_reachable_with_nothing(client):
    r = client.get("/login")
    assert r.status_code == 200 and "login.html" in r.text


def test_the_bundle_is_reachable_with_nothing(client):
    """Already true of the frontend server today — `/_next/*` was never behind the gate there."""
    assert client.get("/_next/static/chunks/main.js").status_code == 200


def test_the_page_still_refuses_when_the_middleware_fails_open(client, monkeypatch):
    """The documented failure of this API is an allowlist failing open, twice. One check is not
    enough, so the route re-asks; this is what makes that duplication worth its keep."""
    from kotoba import server
    monkeypatch.setattr(server, "_is_public_path", lambda p: True)
    r = client.get("/app")
    assert r.status_code == 302 and "/login" in r.headers["location"]
    assert "app.html" not in r.text


def test_the_root_shell_of_the_export_is_never_served(client):
    """`index.html` in the build is the error page left by a redirect that cannot be exported. It is
    denied, so the gate refuses it before the handler is reached — either answer is fine, the bytes
    are what must never come back."""
    _sign_in(client)
    for url in ("/index.html", "/index"):
        r = client.get(url)
        assert r.status_code in (401, 404) and "index.html" not in r.text


def test_a_gate_session_still_does_not_open_the_api(client):
    _sign_in(client)
    assert client.get("/app").status_code == 200
    assert client.get("/api/settings").status_code == 401


def test_a_browser_with_no_cookie_cannot_lock_out_the_login(client):
    """The lockout is shared with the login, so reloading the app must not spend it."""
    from kotoba import server
    for _ in range(server._MAX_FAILS + 5):
        client.get("/app")
    assert client.post("/gate", json={"password": PASSWORD}).status_code == 200


@pytest.mark.parametrize("url", ["/app", "/login", "/", "/_next/static/chunks/main.js"])
def test_every_page_response_carries_the_four_headers(client, url):
    """Including the bounce and the redirect, since a redirect is the response an attacker frames."""
    r = client.get(url)
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_the_app_page_is_not_served_sandboxed(client):
    """A document with no policy of its own is handed the sandbox CSP, which drops it onto an opaque
    origin with no fetch — the app would render and then do nothing, silently."""
    _sign_in(client)
    csp = client.get("/app").headers["content-security-policy"]
    assert "sandbox" not in csp and "connect-src 'none'" not in csp


def test_the_file_viewer_can_still_be_framed(client, tmp_path, monkeypatch):
    """The viewer frames `/api/files/raw/*`. If the page headers ever reach it — say by moving them
    into a middleware — the frame goes blank with nothing on screen to explain it."""
    d = tmp_path / "files"
    d.mkdir()
    (d / "page.html").write_text("<h1>hi</h1>", encoding="utf-8")
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(d))
    # The first hit trades `?token=` for the scoped viewer cookie and bounces to itself.
    r = client.get(f"/api/files/raw/page.html?token={PASSWORD}", follow_redirects=True)
    assert r.status_code == 200
    assert r.headers.get("x-frame-options") is None
    assert "frame-ancestors" not in r.headers.get("content-security-policy", "")
    assert "sandbox allow-scripts" in r.headers["content-security-policy"]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_a_write_method_never_reaches_the_frontend(client, method):
    assert client.request(method, "/app").status_code == 401


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_a_path_that_does_not_exist_still_answers_not_found(client, method):
    """Serving the UI from a catch-all ROUTE turned every unknown path into a partial match, so an
    endpoint removed years ago started claiming its method was merely wrong. Answering from the 404
    handler is what keeps routing, and every refusal it already made, untouched."""
    _sign_in(client)
    r = client.request(method, "/api/onboarding/setup", json={}, headers={"Authorization": f"Bearer {PASSWORD}"})
    assert r.status_code == 404


def test_nothing_is_served_when_there_is_no_build(client, monkeypatch):
    monkeypatch.setattr(frontend, "_ROOT", None)
    assert client.get("/app").status_code == 401
    assert client.get("/login").status_code == 401
