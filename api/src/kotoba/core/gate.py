"""The browser access gate: an HMAC-signed session cookie, minted and checked here.

The wire format belongs to the frontend that already reads these cookies, so a session minted by
either side verifies on the other. Divergence is invisible from outside: the login succeeds and
every request after it refuses.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

COOKIE = "kotoba_gate"
TTL_SECONDS = 12 * 60 * 60


def password() -> str:
    return os.getenv("KOTOBA_WEB_PASSWORD") or os.getenv("KOTOBA_GATE_PASSWORD") or ""


def enabled() -> bool:
    """No password configured means the gate is OPEN — a fresh clone must not lock its owner out."""
    return bool(password())


def _secret() -> str:
    return os.getenv("KOTOBA_GATE_SECRET") or password()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sign(payload: str) -> str:
    return _b64url(hmac.new(_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest())


def password_matches(attempt: str) -> bool:
    """Both sides hashed before the compare, so neither length nor content leaks through timing."""
    real = password()
    if not real:
        return True
    key = _secret().encode("utf-8")
    a = hmac.new(key, (attempt or "").encode("utf-8"), hashlib.sha256).digest()
    b = hmac.new(key, real.encode("utf-8"), hashlib.sha256).digest()
    return hmac.compare_digest(a, b)


def sign_session(now: float | None = None) -> str:
    """Separators without spaces: the signature covers the payload TEXT, so a byte the other side
    would not have emitted makes a cookie it cannot verify."""
    iat = int(now if now is not None else time.time())
    payload = _b64url(json.dumps({"iat": iat}, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_sign(payload)}"


def verify_session(value: str | None, now: float | None = None) -> bool:
    if not value:
        return False
    payload, dot, sig = value.rpartition(".")
    if not dot or not payload:
        return False
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        pad = "=" * (-len(payload) % 4)
        iat = int(json.loads(base64.urlsafe_b64decode(payload + pad).decode("utf-8")).get("iat", 0))
    except Exception:
        return False
    return iat + TTL_SECONDS >= int(now if now is not None else time.time())


def request_is_secure(scheme: str, forwarded: str | None) -> bool:
    """Lopsided on purpose: any hop claiming https wins, and only a scheme that positively says http
    turns the flag off. A needlessly-secure cookie is dropped by the browser in silence and the login
    loops forever with nothing to read; the alternative is one open cookie on a loopback address."""
    if forwarded and any(v.strip().lower() == "https" for v in forwarded.split(",")):
        return True
    return scheme != "http"
