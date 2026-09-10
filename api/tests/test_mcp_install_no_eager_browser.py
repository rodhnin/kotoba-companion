"""Installing the "browser" MCP must not open a browser — least of all before the approval card.

`mcp_install` used to call `ensure_browser` above `request_approval`, so a window landed on the user's
screen and stayed there when they answered no. Permission asked after the fact is not permission. The
connect attaches per tool call, and a call against a cold CDP port is recovered by the proxy's autostart
retry, so nothing here needs a live browser.
"""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.mcp.browser_launch as bl
import kotoba.core.interaction as interaction
import kotoba.core.mcp.config as mcp_config
import kotoba.tools.action.mcp_install as mcp_install


class _FakeMCP:
    def __init__(self):
        self.server_tools: dict[str, list[str]] = {}
        self.connected: list[str] = []

    async def connect(self, name, cfg):
        self.connected.append(name)
        return [f"{name}__browser_snapshot"]


class _Ctx:
    def __init__(self, mcp):
        self.mcp = mcp
        self.session_id = "s1"


@pytest.fixture
def counted(monkeypatch):
    """Count real launch attempts. The call site is gone, so any reappearance trips these."""
    launched: list[str] = []

    async def _fake_ensure_browser(cdp):
        launched.append(cdp)
        return True

    monkeypatch.setenv("KOTOBA_BROWSER_CDP", "http://127.0.0.1:9222")
    monkeypatch.setattr(bl, "ensure_browser", _fake_ensure_browser)
    monkeypatch.setattr(mcp_config, "save_server", lambda *a, **k: None)
    return launched


def _install(answer, monkeypatch):
    async def _fake_approval(session_id, prompt, family="", card=None, **kw):
        if card is not None:
            card["verdict"] = interaction.APPROVED if answer else interaction.DECLINED
        return answer, False

    monkeypatch.setattr(interaction, "request_approval", _fake_approval)
    mcp = _FakeMCP()
    out = asyncio.run(mcp_install.execute({"name": "browser"}, _Ctx(mcp)))
    return mcp, out


def test_a_denied_install_leaves_no_window_behind(counted, monkeypatch):
    mcp, out = _install(False, monkeypatch)
    assert counted == []
    assert mcp.connected == []
    assert "said NO" in out and "did not happen" in out


def test_an_approved_install_connects_without_opening_a_window(counted, monkeypatch):
    mcp, out = _install(True, monkeypatch)
    assert counted == []
    assert mcp.connected == ["browser"]
    assert "browser" in out
