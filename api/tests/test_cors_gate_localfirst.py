"""Local-first regressions, both found by live QA.

Pinning CORS_ORIGINS to the deployed origin made the default config answer the localhost frontend's
preflight with a 400, and the browser then fell back silently to the wrong voice mode. Separately,
the frontend and backend gate passwords lived under two environment variables that drifted apart.

So localhost:3000 and 127.0.0.1:3000 are now ALWAYS allowed — CORS_ORIGINS adds to that list, it
never replaces it — and the /api gate accepts one shared secret under either name:
KOTOBA_WEB_PASSWORD is canonical, KOTOBA_GATE_PASSWORD is the alias."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import kotoba.server as main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_default_origins_are_localhost_not_star(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert main.cors_origins() == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_cors_origins_env_is_additive_not_replacing(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://example.com")
    origins = main.cors_origins()
    assert "https://example.com" in origins
    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins


def test_cors_origins_trimmed_and_deduped(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " https://a.example , http://localhost:3000 ,, https://a.example ")
    origins = main.cors_origins()
    assert origins.count("https://a.example") == 1
    assert origins.count("http://localhost:3000") == 1


def test_explicit_star_still_honored(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    assert "*" in main.cors_origins()


def test_preflight_from_localhost_frontend_passes(client):
    """The exact live failure: OPTIONS /api/settings from http://localhost:3000 returned 400."""
    r = client.options("/api/settings", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_preflight_from_unknown_origin_refused(client):
    r = client.options("/api/settings", headers={
        "Origin": "https://evil.example.com",
        "Access-Control-Request-Method": "GET",
    })
    assert r.status_code == 400


def test_gate_password_alias_enables_api_gate(client, monkeypatch):
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "")
    monkeypatch.setenv("KOTOBA_GATE_PASSWORD", "onesecret")
    assert client.get("/api/files").status_code == 401
    assert client.get("/api/files", headers={"Authorization": "Bearer onesecret"}).status_code == 200


def test_web_password_wins_over_alias(client, monkeypatch):
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "primary")
    monkeypatch.setenv("KOTOBA_GATE_PASSWORD", "other")
    assert client.get("/api/files", headers={"Authorization": "Bearer primary"}).status_code == 200
    assert client.get("/api/files", headers={"Authorization": "Bearer other"}).status_code == 401


def test_gate_stays_open_when_both_unset(client, monkeypatch):
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "")
    monkeypatch.setenv("KOTOBA_GATE_PASSWORD", "")
    assert client.get("/api/files").status_code == 200
