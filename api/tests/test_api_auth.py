"""API auth gate: once KOTOBA_WEB_PASSWORD is set, every /api/* route requires that password as the token
(Bearer header or ?token= for SSE/img/links); /health stays public; OPTIONS preflight is not blocked; when
the password is unset the gate is open (back-compat / local dev). The two secrets are strictly separated:
KOTOBA_API_KEY opens /v1 and only /v1, the gate password opens /api/* and only /api/*."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import kotoba.server as main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_open_when_password_unset(client, monkeypatch):
    monkeypatch.delenv("KOTOBA_WEB_PASSWORD", raising=False)
    assert client.get("/api/files").status_code == 200          # gate off → /api open


def test_blocks_without_token(client, monkeypatch):
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    assert client.get("/api/files").status_code == 401          # no token → refused
    assert client.get("/api/settings").status_code == 401


def test_allows_with_bearer_header(client, monkeypatch):
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    r = client.get("/api/files", headers={"Authorization": "Bearer letmein"})
    assert r.status_code == 200


def test_allows_with_query_token(client, monkeypatch):
    # EventSource / <img> / new-tab links can't set headers → they pass ?token=.
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    assert client.get("/api/files", params={"token": "letmein"}).status_code == 200


@pytest.mark.parametrize("path", ["/api/files", "/api/settings", "/api/session/s1/work"])
def test_api_key_rejected_on_api_routes(client, monkeypatch, path):
    # KOTOBA_API_KEY is the /v1 bearer ONLY. It must never open /api/* — by any of the three token
    # carriers — or an exposed install would be one shared secret away from the whole control surface.
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    monkeypatch.setenv("KOTOBA_API_KEY", "el_secret")
    assert client.get(path, headers={"Authorization": "Bearer el_secret"}).status_code == 401
    assert client.get(path, params={"token": "el_secret"}).status_code == 401
    client.cookies.set("kf", "el_secret")
    assert client.get(path).status_code == 401
    client.cookies.clear()
    assert client.get(path, headers={"Authorization": "Bearer letmein"}).status_code == 200


def test_api_key_is_accepted_on_v1(client, monkeypatch):
    # The other half of the boundary: /v1 takes the API key and nothing else. A deliberately invalid
    # body separates "auth passed" (422 from validation) from "auth refused" (401) without an LLM call.
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    monkeypatch.setenv("KOTOBA_API_KEY", "el_secret")
    bad_body = {"messages": "not-a-list"}
    r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer el_secret"}, json=bad_body)
    assert r.status_code == 422


def test_gate_password_accepted_on_v1(client, monkeypatch):
    # /v1 now accepts the web password too (so the docs-recommended setup — leave API key unset,
    # set the web password — still protects this endpoint). When both are set, either works.
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    monkeypatch.setenv("KOTOBA_API_KEY", "el_secret")
    r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer letmein"},
                    json={"messages": "not-a-list"})
    assert r.status_code == 422  # web password accepted → auth passed, 422 from body validation


def test_post_with_token_reaches_route_body_intact(client, monkeypatch):
    # The ASGI gate must not consume/break the request body: a POST with a valid token + JSON reaches the
    # route and round-trips its body (not 401 from the gate, not a body error).
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    r = client.post(
        "/api/session/sess9/mute",
        headers={"Authorization": "Bearer letmein"},
        json={"muted": True},
    )
    assert r.status_code == 200
    assert r.json()["muted"] is True


def test_health_always_public(client, monkeypatch):
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    assert client.get("/health").status_code == 200             # health never gated (monitoring)


def test_preflight_not_blocked(client, monkeypatch):
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    # A CORS preflight (OPTIONS) must pass so the browser can then send the real request with its token.
    # Origin must be one of the built-in defaults: the CORS middleware is built at import time, so a
    # CORS_ORIGINS-dependent origin would only pass on a machine whose .env happens to allow it.
    r = client.options("/api/files", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
    })
    assert r.status_code in (200, 204)
