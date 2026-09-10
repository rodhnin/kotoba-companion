"""Who may open the voice socket.

A WebSocket handshake is exempt from CORS, so the allowlist that guards every HTTP route does not
guard this one: any page the user visits could otherwise dial the loopback backend and drive an agent
that runs host commands. The rule grew a second case when the backend began serving the app itself,
and this file exists because that case shipped with the parameter missing from the signature — every
handshake answered 500 and nothing in the suite noticed.
"""
from __future__ import annotations

import pytest

from kotoba import server

SELF = "127.0.0.1:8123"


def test_a_page_this_server_serves_may_open_it():
    assert server._ws_origin_allowed(f"http://{SELF}", SELF)


def test_the_dev_frontend_on_its_own_port_may_open_it():
    """Dev keeps the frontend on another port, since Next does not forward upgrades."""
    assert server._ws_origin_allowed("http://localhost:3000", SELF)


def test_a_caller_that_is_not_a_page_may_open_it():
    """No Origin means no browser: the CLI, a script, a test."""
    assert server._ws_origin_allowed(None, SELF)


@pytest.mark.parametrize("origin", [
    "http://evil.example", "https://evil.example", "http://127.0.0.1:9999",
    "http://127.0.0.1:8123.evil.example", "null", "http://localhost:3001",
])
def test_no_other_page_may_open_it(origin):
    assert not server._ws_origin_allowed(origin, SELF)


def test_a_foreign_page_cannot_forge_the_match():
    """The browser writes Host from the address it dialled, so the two agree only for our own page."""
    assert not server._ws_origin_allowed("http://evil.example", "evil.example")


def test_the_check_survives_a_handshake_with_no_host():
    assert not server._ws_origin_allowed("http://evil.example", None)


def test_the_route_passes_both_headers():
    """The defect this file was written for: the second argument was read and never handed over."""
    import inspect

    src = inspect.getsource(server.voice_ws)
    assert 'headers.get("origin")' in src and 'headers.get("host")' in src


@pytest.mark.parametrize("origin,host", [
    ("http://127.0.0.1:8123", "127.0.0.1:8123"),
    ("http://localhost:8123", "localhost:8123"),
    ("http://[::1]:8123", "[::1]:8123"),
])
def test_the_same_origin_case_is_loopback_only(origin, host):
    assert server._ws_origin_allowed(origin, host)


@pytest.mark.parametrize("name", ["evil.example", "kotoba.example.com", "192.168.1.40:8123"])
def test_a_rebound_name_is_not_a_way_in(name):
    """A page keeps its own origin while its name resolves to this machine, so Origin and Host agree
    and a default install has no password to ask for. Loopback is what the packaged server binds."""
    assert not server._ws_origin_allowed(f"http://{name}", name)
