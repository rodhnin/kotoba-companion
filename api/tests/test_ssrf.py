"""Audit — SSRF guard for web_extract. The model fetches user-supplied URLs server-side; it must never
reach internal/cloud-metadata/loopback/private targets, directly or via redirect."""
from __future__ import annotations

import socket

import pytest

from kotoba.core import ssrf
from kotoba.core.ssrf import check_redirect, url_block_reason

# IP-literal / scheme / hostname cases — no DNS needed, so the test is hermetic.
BLOCKED = [
    "http://169.254.169.254/latest/meta-data/",   # AWS/GCP/Azure metadata (link-local)
    "http://metadata.google.internal/",            # GCP metadata by name
    "http://localhost:8000/api/settings",          # our own internal endpoints
    "http://127.0.0.1:6379/",                      # loopback (redis etc.)
    "http://0.0.0.0/",                             # unspecified
    "http://[::1]:8000/",                          # ipv6 loopback
    "http://10.0.0.5/x",                           # private A
    "http://172.16.0.1/x",                         # private B
    "http://192.168.1.1/",                         # private C
    "http://2130706433/",                          # 127.0.0.1 as a 32-bit integer
    "http://[::ffff:127.0.0.1]/",                  # ipv4-mapped ipv6 loopback
    "file:///etc/passwd",
    "ftp://internal/",
    "gopher://x/",
    "javascript:alert(1)",
    "",
]


@pytest.mark.parametrize("url", BLOCKED)
def test_blocked_urls_have_a_reason(url):
    assert url_block_reason(url) is not None, f"SSRF guard let through: {url!r}"


@pytest.mark.parametrize("url", [
    "http://93.184.216.34/",      # a public IP literal (example.com's old IP) — allowed
    "https://8.8.8.8/",           # public
])
def test_public_ip_literals_allowed(url):
    assert url_block_reason(url) is None, f"SSRF guard wrongly blocked public {url!r}"


def test_redirect_to_internal_is_blocked():
    # An external page 30x-redirecting to the metadata IP must be refused.
    target, reason = check_redirect("https://evil.example/start", "http://169.254.169.254/")
    assert target is None and reason is not None


def test_redirect_to_public_is_allowed():
    target, reason = check_redirect("https://site.example/a", "https://8.8.8.8/b")
    assert reason is None and target == "https://8.8.8.8/b"


def test_unresolvable_host_fails_closed(monkeypatch):
    """A name that can't be resolved must NOT be fetched: don't fetch what you can't vet.

    The resolver is stubbed rather than trusted. A lookup is a packet leaving the machine, and this
    suite sends none; it is also seconds of nothing wherever the resolver is slow to say no."""
    def _no_answer(*a, **k):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", _no_answer)
    assert url_block_reason("http://this-domain-does-not-exist-kotoba-xyz.invalid/") is not None
