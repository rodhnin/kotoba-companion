"""The login moved into the backend, and it has to stay a lock after the move.

Three routes reach a browser holding nothing: two of them so it can stop holding nothing, and that
is the whole reason they are exempt from the token middleware. The exemption is the risk, so it is
pinned here from the outside — through the real middleware stack, never by calling the handlers.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kotoba.core import gate

PASSWORD = "open-sesame"


@pytest.fixture
def client(monkeypatch):
    for name in ("KOTOBA_GATE_PASSWORD", "KOTOBA_GATE_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", PASSWORD)
    from kotoba import server
    server._FAILED_AUTH.clear()
    with TestClient(server.app) as c:
        yield c


@pytest.fixture
def open_client(monkeypatch):
    """No password anywhere: the fresh-clone case, where the gate must not lock anyone out."""
    for name in ("KOTOBA_WEB_PASSWORD", "KOTOBA_GATE_PASSWORD", "KOTOBA_GATE_SECRET"):
        monkeypatch.delenv(name, raising=False)
    from kotoba import server
    server._FAILED_AUTH.clear()
    with TestClient(server.app) as c:
        yield c


def test_the_right_password_mints_a_session(client):
    r = client.post("/gate", json={"password": PASSWORD})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert gate.verify_session(r.cookies.get(gate.COOKIE))


def test_the_wrong_password_mints_nothing(client):
    r = client.post("/gate", json={"password": "not it"})
    assert r.status_code == 401
    assert gate.COOKIE not in r.cookies


@pytest.mark.parametrize("body", [{}, {"password": None}, {"password": 7}, {"password": ""}])
def test_no_shape_of_missing_password_is_accepted_as_one(client, body):
    """A body that carries no password must be a refusal, never an empty attempt that compares equal
    to an unset variable somewhere."""
    assert client.post("/gate", json=body).status_code == 401


def test_a_body_that_is_not_json_is_refused_before_anything_is_compared(client):
    assert client.post("/gate", content=b"not json at all").status_code == 400


def test_the_token_is_refused_without_a_session(client):
    assert client.get("/gate/token").status_code == 401


def test_the_token_is_refused_to_a_forged_session(client):
    assert client.get("/gate/token", cookies={gate.COOKIE: "eyJpYXQiOjF9.forged"}).status_code == 401


def test_a_session_earns_the_token_and_it_is_never_cached(client):
    client.post("/gate", json={"password": PASSWORD})
    r = client.get("/gate/token")
    assert r.status_code == 200 and r.json()["token"] == PASSWORD
    assert "no-store" in r.headers.get("cache-control", "")


def test_signing_out_drops_the_session(client):
    client.post("/gate", json={"password": PASSWORD})
    assert client.get("/gate/token").status_code == 200
    client.delete("/gate")
    assert client.get("/gate/token").status_code == 401


def test_the_gate_does_not_open_the_api(client):
    """Passing the gate is not the same as holding the API token: `/api/*` still wants the token, and
    a session cookie must not be mistaken for one."""
    client.post("/gate", json={"password": PASSWORD})
    assert client.get("/api/settings").status_code == 401


def test_an_open_install_hands_out_the_empty_token(open_client):
    assert open_client.post("/gate", json={"password": "anything"}).json() == {"ok": True, "open": True}
    assert open_client.get("/gate/token").json() == {"token": ""}


def test_guessing_runs_into_the_lockout(client):
    from kotoba import server
    for _ in range(server._MAX_FAILS):
        client.post("/gate", json={"password": "wrong"})
    assert client.post("/gate", json={"password": "wrong"}).status_code == 429
    assert client.post("/gate", json={"password": PASSWORD}).status_code == 429, (
        "the lockout has to hold against the real password too, or it is a free oracle for it")


def test_the_exemption_is_exactly_two_paths(client):
    """Matched whole. A prefix rule here would hand every path under /gate to an anonymous caller."""
    from kotoba.server import _is_public_path
    assert _is_public_path("/gate") and _is_public_path("/gate/token")
    for path in ("/gate/", "/gate/token/", "/gateway", "/gate/../api/settings", "/api/settings"):
        assert not _is_public_path(path), path


@pytest.mark.parametrize("headers,secure", [
    ({}, False),
    ({"x-forwarded-proto": "https"}, True),
])
def test_the_cookie_is_marked_secure_only_when_the_browser_used_tls(client, headers, secure):
    r = client.post("/gate", json={"password": PASSWORD}, headers=headers)
    assert ("Secure" in r.headers["set-cookie"]) is secure
