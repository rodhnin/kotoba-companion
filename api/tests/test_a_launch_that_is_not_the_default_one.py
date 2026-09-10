"""Two ways a first run dies for somebody whose setup is not the one it was written on.

A terminal that has not been told its size answers ZERO instead of failing — `docker run -t` before
the client sends one, a fresh pane, some CI. Copied over the 80x24 default it divides by zero two
screens later, so `kotoba setup` ends on a traceback the first time a stranger runs it.

And `kotoba serve` on any web port but 3000 left voice silently dead: the WebSocket handshake is
exempt from CORS, so the backend keeps its own allowlist, and that list knew one port. The browser
got a 403 the UI does not surface, so the call button simply did nothing.
"""
from __future__ import annotations

import os
import types

import pytest

from kotoba.cli import serve
from kotoba.cli.render.caps import Caps


class _Size:
    def __init__(self, columns: int, lines: int) -> None:
        self.columns, self.lines = columns, lines


@pytest.fixture
def reported(monkeypatch):
    """Whatever the terminal claims its size is."""
    def _set(columns, lines):
        monkeypatch.setattr(os, "get_terminal_size", lambda *a: _Size(columns, lines))
        monkeypatch.setattr("sys.__stdout__", types.SimpleNamespace(fileno=lambda: 1))
        caps = Caps()
        caps.sync_size()
        return caps
    return _set


def test_a_terminal_that_has_not_said_its_size_keeps_the_default(reported):
    caps = reported(0, 0)
    assert (caps.width, caps.height) == (80, 24)


def test_one_dimension_missing_does_not_take_the_other_down(reported):
    caps = reported(120, 0)
    assert (caps.width, caps.height) == (120, 24)


def test_a_real_size_is_still_honoured(reported):
    """Guards the guard: a clamp that ignored every size would pass the two tests above."""
    caps = reported(120, 40)
    assert (caps.width, caps.height) == (120, 40)


def test_the_width_can_never_be_zero_where_it_is_divided_by(reported):
    """The crash was `-(-cell_len(text) // caps.width)` two screens later, not the read itself."""
    caps = reported(0, 0)
    assert caps.width > 0


def test_the_backend_is_told_which_port_the_page_is_on(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    named = serve._backend_env(18100)["CORS_ORIGINS"].split(",")
    assert "http://127.0.0.1:18100" in named and "http://localhost:18100" in named


def test_a_deployed_origin_is_added_to_and_never_replaced(monkeypatch):
    """CORS_ORIGINS adds to the defaults everywhere else; it must not lose its meaning here."""
    monkeypatch.setenv("CORS_ORIGINS", "https://deployed.example.com")
    named = serve._backend_env(3000)["CORS_ORIGINS"].split(",")
    assert named[0] == "https://deployed.example.com"
    assert "http://127.0.0.1:3000" in named


def test_the_page_origin_actually_passes_the_socket_gate(monkeypatch):
    """The gate is what the fix exists for, so drive the gate — not the string that feeds it."""
    from kotoba import server

    monkeypatch.setenv("CORS_ORIGINS", serve._backend_env(18100)["CORS_ORIGINS"])
    assert server._ws_origin_allowed("http://127.0.0.1:18100", "127.0.0.1:18099")


def test_a_stranger_page_still_cannot_open_the_socket(monkeypatch):
    """Widening the allowlist must not widen it to everybody: this socket runs host tools."""
    from kotoba import server

    monkeypatch.setenv("CORS_ORIGINS", serve._backend_env(18100)["CORS_ORIGINS"])
    assert not server._ws_origin_allowed("https://evil.example.com", "127.0.0.1:18099")


def test_a_limit_set_to_nonsense_warns_instead_of_killing_the_import(monkeypatch, caplog):
    """`_limit` exists so a bad value cannot stop her starting — and its own error path raised
    NameError, so the one case it was written for was the one that crashed at import."""
    import importlib
    import logging

    monkeypatch.setenv("KOTOBA_PER_TOOL_LIMIT", "abc")
    with caplog.at_level(logging.WARNING, logger="kotoba"):
        loop = importlib.reload(importlib.import_module("kotoba.core.loop"))
    assert loop._PER_TOOL_LIMIT == 3
    assert any("is not a number" in r.message for r in caplog.records)
    monkeypatch.delenv("KOTOBA_PER_TOOL_LIMIT")
    importlib.reload(loop)

