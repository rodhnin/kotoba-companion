"""One session cookie, two implementations that must stay byte-identical.

The packaged install mints it in Python and a contributor running the dev server mints it in
TypeScript, against the same password. If the two ever disagree the failure is silent from outside:
the login succeeds and every following request bounces back to it. So the wire format is pinned to
vectors TAKEN FROM the TypeScript, never recomputed here — a vector this module generated would only
prove Python agrees with itself.
"""
from __future__ import annotations

import pytest

from kotoba.core import gate

NOW = 1_700_000_000

#: (env, cookie signed at NOW) as `lib/gate.ts` emits them.
VECTORS = [
    ({"KOTOBA_WEB_PASSWORD": "open-sesame"},
     "eyJpYXQiOjE3MDAwMDAwMDB9.U2qkmH76uRJx50aQu9jNjVCyD23GurD0NiEcXmAGupI"),
    ({"KOTOBA_WEB_PASSWORD": "contraseña-ñ"},
     "eyJpYXQiOjE3MDAwMDAwMDB9.C2gT_ovl_qbkKYHjLIuFDVKIG33cP03rP9X_xYhlfn4"),
    ({"KOTOBA_GATE_PASSWORD": "open-sesame"},
     "eyJpYXQiOjE3MDAwMDAwMDB9.U2qkmH76uRJx50aQu9jNjVCyD23GurD0NiEcXmAGupI"),
    ({"KOTOBA_WEB_PASSWORD": "open-sesame", "KOTOBA_GATE_SECRET": "a-different-signing-key"},
     "eyJpYXQiOjE3MDAwMDAwMDB9.60D_EsVEuCbCVGcJBvfYOsBsnAZOaVSd1NI-bW4_hD0"),
    ({"KOTOBA_WEB_PASSWORD": "wins", "KOTOBA_GATE_PASSWORD": "loses"},
     "eyJpYXQiOjE3MDAwMDAwMDB9.DbSu7pADIH3c0mA5a97x17xS687UEaW4MCiQvWAz67Y"),
]

NAMES = ("KOTOBA_WEB_PASSWORD", "KOTOBA_GATE_PASSWORD", "KOTOBA_GATE_SECRET")


@pytest.fixture
def env(monkeypatch):
    def apply(values):
        for name in NAMES:
            monkeypatch.delenv(name, raising=False)
        for name, value in values.items():
            monkeypatch.setenv(name, value)
    return apply


@pytest.mark.parametrize("values,expected", VECTORS)
def test_python_signs_the_bytes_the_frontend_signs(env, values, expected):
    env(values)
    assert gate.sign_session(now=NOW) == expected


@pytest.mark.parametrize("values,expected", VECTORS)
def test_python_accepts_a_session_the_frontend_minted(env, values, expected):
    env(values)
    assert gate.verify_session(expected, now=NOW)


def test_the_alias_and_the_canonical_name_reach_the_same_secret(env):
    """Two names, one value — the divergence this guards against is a login that works against one
    while every gated request refuses against the other."""
    env({"KOTOBA_WEB_PASSWORD": "open-sesame"})
    canonical = gate.sign_session(now=NOW)
    env({"KOTOBA_GATE_PASSWORD": "open-sesame"})
    assert gate.sign_session(now=NOW) == canonical


def test_a_session_signed_with_another_secret_is_refused(env):
    env({"KOTOBA_WEB_PASSWORD": "open-sesame"})
    minted = gate.sign_session(now=NOW)
    env({"KOTOBA_WEB_PASSWORD": "some-other-password"})
    assert not gate.verify_session(minted, now=NOW)


def test_a_session_older_than_its_life_is_refused(env):
    env({"KOTOBA_WEB_PASSWORD": "open-sesame"})
    minted = gate.sign_session(now=NOW)
    assert gate.verify_session(minted, now=NOW + gate.TTL_SECONDS)
    assert not gate.verify_session(minted, now=NOW + gate.TTL_SECONDS + 1)


@pytest.mark.parametrize("value", [
    "", None, ".", "nodot", "eyJpYXQiOjE3MDAwMDAwMDB9.", ".U2qkmH76uRJx50aQu9jNjVCyD23GurD0NiEcXmAGupI",
    "eyJpYXQiOjE3MDAwMDAwMDB9.U2qkmH76uRJx50aQu9jNjVCyD23GurD0NiEcXmAGupj",
    "not-base64-at-all.U2qkmH76uRJx50aQu9jNjVCyD23GurD0NiEcXmAGupI",
])
def test_nothing_malformed_is_ever_a_session(env, value):
    env({"KOTOBA_WEB_PASSWORD": "open-sesame"})
    assert not gate.verify_session(value, now=NOW)


def test_no_password_configured_leaves_the_gate_open(env):
    """A fresh clone has no password and must not lock its owner out of their own machine."""
    env({})
    assert not gate.enabled()
    assert gate.password_matches("anything at all")


def test_a_wrong_attempt_is_refused_and_an_accented_one_does_not_crash(env):
    """`hmac.compare_digest` raises on non-ASCII str, so comparing the raw text would 500 the login
    for anybody whose password has an accent in it."""
    env({"KOTOBA_WEB_PASSWORD": "contraseña-ñ"})
    assert gate.password_matches("contraseña-ñ")
    assert not gate.password_matches("contrasena-n")
    assert not gate.password_matches("")


@pytest.mark.parametrize("scheme,forwarded,secure", [
    ("http", None, False),
    ("https", None, True),
    ("http", "https", True),
    ("http", "http, https", True),
    ("http", "http", False),
    ("ws", None, True),
])
def test_the_cookie_follows_the_connection_and_not_the_build(scheme, forwarded, secure):
    """A needlessly-secure cookie is dropped by the browser without a word and the login loops with
    nothing to read; only a scheme that positively says http turns the flag off."""
    assert gate.request_is_secure(scheme, forwarded) is secure
