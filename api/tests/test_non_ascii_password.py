"""A non-ASCII password must gate normally, never 500 the API.

`hmac.compare_digest` raises TypeError on str with non-ASCII characters. Comparing the raw strings
meant an accented password made every /api/* request fail inside the auth middleware, and any client
could force a 500 unauthenticated just by sending `?token=ñ`. Compare bytes.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PW = "contraseña-Ünicode-日本"


@pytest.fixture
def app_with_pw(monkeypatch):
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", PW)
    monkeypatch.delenv("KOTOBA_GATE_PASSWORD", raising=False)
    monkeypatch.delenv("KOTOBA_API_KEY", raising=False)
    import kotoba.server as m

    with TestClient(m.app) as c:
        yield c


# HTTP headers cannot carry non-ASCII, so the query token and the cookie are the reachable paths —
# which is also how the frontend gate passes it.

def test_the_right_non_ascii_password_is_accepted(app_with_pw):
    r = app_with_pw.get("/api/settings", params={"token": PW})
    assert r.status_code == 200, r.text


def test_a_wrong_non_ascii_token_is_401_not_500(app_with_pw):
    r = app_with_pw.get("/api/settings", params={"token": "ñ-wrong"})
    assert r.status_code == 401, r.text


def test_an_accented_password_does_not_break_an_ordinary_login(app_with_pw):
    """The likeliest real case: the operator picks an accented password, then a normal ASCII token
    arrives. The comparison — not the token — is what used to raise, so EVERY request 500'd."""
    r = app_with_pw.get("/api/settings", params={"token": "plain-ascii-guess"})
    assert r.status_code == 401, r.text


def test_a_non_ascii_query_token_cannot_force_a_500(app_with_pw):
    """Reachable unauthenticated: the crash happened before any credential was validated."""
    r = app_with_pw.get("/api/settings?token=%C3%B1")
    assert r.status_code == 401, r.text


def test_v1_bearer_also_survives_a_non_ascii_key(monkeypatch):
    monkeypatch.setenv("KOTOBA_API_KEY", "clave-ñ")
    monkeypatch.delenv("KOTOBA_WEB_PASSWORD", raising=False)
    monkeypatch.delenv("KOTOBA_GATE_PASSWORD", raising=False)
    import kotoba.server as m

    with TestClient(m.app) as c:
        assert c.post("/v1/chat/completions", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
