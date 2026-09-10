"""SSRF guard for outbound fetches the model can be steered to make.

FOUR callers, not one: `web_extract`, `remember_image`, the model download, and the remote MCP connect
— which takes its url off a public registry and is the only one with an opt-out
(KOTOBA_ALLOW_LOCAL_MCP). Relaxing anything here to suit one changes the other three. Without a guard, "read http://169.254.169.254/…"
(cloud metadata → IAM creds) or an internal endpoint would exfiltrate through Kotoba.

`url_block_reason(url)` allows only http/https, resolves the hostname to ALL its IPs and blocks if ANY
is loopback, private, link-local, reserved, multicast or unspecified, and blocks well-known internal
names as a cheap first cut. A true TOCTOU rebind stays open; callers must re-check every redirect hop."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata", "instance-data"}


def _ip_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → treat as unsafe
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) — unwrap and re-check.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _ip_blocked(str(ip.ipv4_mapped))
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local      # 169.254.0.0/16 (cloud metadata) + fe80::/10
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def url_block_reason(url: str) -> str | None:
    """Return a short reason the URL must not be fetched (SSRF), or None if it's a safe public http(s)."""
    try:
        p = urlparse((url or "").strip())
    except Exception:
        return "unparseable url"
    if p.scheme.lower() not in ("http", "https"):
        return f"scheme {p.scheme!r} not allowed"
    host = p.hostname
    if not host:
        return "no host"
    if host.lower() in _BLOCKED_HOSTNAMES:
        return f"blocked host {host!r}"
    # A literal IP in the URL — check directly (getaddrinfo would echo it, but be explicit).
    try:
        ipaddress.ip_address(host)
        if _ip_blocked(host):
            return f"blocked ip {host}"
        return None
    except ValueError:
        pass  # it's a hostname → resolve it
    try:
        port = p.port or (443 if p.scheme.lower() == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:
        return "could not resolve host"  # don't fetch what we can't vet
    if not infos:
        return "no address"
    for info in infos:
        ip_str = info[4][0]
        if _ip_blocked(ip_str):
            return f"resolves to blocked ip {ip_str}"
    return None


def check_redirect(base_url: str, location: str) -> tuple[str | None, str | None]:
    """Resolve a redirect Location against base_url and SSRF-check it.
    Returns (absolute_url, None) if safe to follow, or (None, reason) if it must be blocked."""
    target = urljoin(base_url, location or "")
    reason = url_block_reason(target)
    return (None, reason) if reason else (target, None)
