"""/v1/chat/completions must not be open when KOTOBA_WEB_PASSWORD is set but
KOTOBA_API_KEY is left unset (the setup the docs recommend).

Before the fix: verify_auth returned early when KOTOBA_API_KEY was unset, leaving /v1 open.
After the fix: /v1 accepts KOTOBA_API_KEY OR KOTOBA_WEB_PASSWORD; when neither is configured
the endpoint stays open (local-install default, ergonomics constraint).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import kotoba.server as main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


_BAD_BODY = {"messages": "not-a-list"}


def test_v1_open_when_nothing_configured(client, monkeypatch):
    """A local install with no credentials set must still be able to call /v1 (ergonomics)."""
    monkeypatch.delenv("KOTOBA_API_KEY", raising=False)
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "")
    monkeypatch.setenv("KOTOBA_GATE_PASSWORD", "")
    r = client.post("/v1/chat/completions", json=_BAD_BODY)
    assert r.status_code == 422  # auth passed, body validation failed → not 401


def test_v1_requires_api_key_when_set(client, monkeypatch):
    """When KOTOBA_API_KEY is set, /v1 requires it."""
    monkeypatch.setenv("KOTOBA_API_KEY", "el_secret")
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "")
    r = client.post("/v1/chat/completions", json=_BAD_BODY)
    assert r.status_code == 401  # no token → refused
    r2 = client.post("/v1/chat/completions", headers={"Authorization": "Bearer el_secret"}, json=_BAD_BODY)
    assert r2.status_code == 422  # correct key → auth passed


def test_v1_web_password_accepted_when_no_api_key(client, monkeypatch):
    """Core fix: when only KOTOBA_WEB_PASSWORD is set (API key left unset per docs recommendation),
    /v1 must require AND accept the web password, not be open to anyone."""
    monkeypatch.delenv("KOTOBA_API_KEY", raising=False)
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "secret123")
    r_no_token = client.post("/v1/chat/completions", json=_BAD_BODY)
    assert r_no_token.status_code == 401  # hole is closed — open endpoint is the bug we fixed
    r_with_token = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret123"},
        json=_BAD_BODY,
    )
    assert r_with_token.status_code == 422  # web password accepted


def test_v1_wrong_token_rejected(client, monkeypatch):
    """A wrong token is always rejected regardless of which credential is configured."""
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "real")
    r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer wrong"}, json=_BAD_BODY)
    assert r.status_code == 401


def test_v1_web_password_accepted_when_both_configured(client, monkeypatch):
    """When both are set, either credential opens /v1."""
    monkeypatch.setenv("KOTOBA_API_KEY", "el_key")
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "web_pw")
    r_api = client.post("/v1/chat/completions", headers={"Authorization": "Bearer el_key"}, json=_BAD_BODY)
    assert r_api.status_code == 422
    r_web = client.post("/v1/chat/completions", headers={"Authorization": "Bearer web_pw"}, json=_BAD_BODY)
    assert r_web.status_code == 422
