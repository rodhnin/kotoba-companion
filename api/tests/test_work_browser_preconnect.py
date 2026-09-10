"""Work-mode browser pre-connect + guidance, so the model need not install the browser first.

Browser tools used to appear only after a mid-loop install, so the model installed the browser on
every work task, and the guidance built once at loop start never saw the browser -- it guessed a
ref and fabricated success. The work loop now connects the browser UP-FRONT before building it.

Pre-connect must NOT launch the real browser: connecting the MCP attaches to the remote debugging
endpoint lazily, per tool call, and launching here popped a window on every work task, web or not.
The window opens on the first browser tool call instead, via the browser-down autostart retry.
"""
from __future__ import annotations

import asyncio

import kotoba.core.loop as loop


class _FakeMCP:
    def __init__(self, server_tools):
        self.server_tools = server_tools
        self.connect_calls: list[tuple[str, dict]] = []

    async def connect(self, name, cfg):
        self.connect_calls.append((name, cfg))
        return []


class _Ctx:
    def __init__(self, mcp):
        self.mcp = mcp


def test_ensure_work_browser_noop_when_already_registered(monkeypatch):
    # Browser already in server_tools → no reconnect attempt (and no browser launch).
    monkeypatch.setenv("KOTOBA_BROWSER_CDP", "http://127.0.0.1:9222")
    mcp = _FakeMCP({"browser": ["browser__browser_snapshot"]})
    asyncio.run(loop._ensure_work_browser(_Ctx(mcp)))
    assert mcp.connect_calls == []


def test_ensure_work_browser_skips_when_no_cdp_configured(monkeypatch):
    # No real browser configured → stay lazy (the model can still mcp_install on demand); never connect.
    monkeypatch.delenv("KOTOBA_BROWSER_CDP", raising=False)
    mcp = _FakeMCP({})  # browser NOT registered
    asyncio.run(loop._ensure_work_browser(_Ctx(mcp)))
    assert mcp.connect_calls == []


def test_ensure_work_browser_connects_when_cdp_set_and_missing(monkeypatch):
    # CDP configured but browser not registered → connect the MCP, but never launch the window here.
    monkeypatch.setenv("KOTOBA_BROWSER_CDP", "http://127.0.0.1:9222")
    launched: list[str] = []

    async def _fake_ensure_browser(cdp):
        launched.append(cdp)

    import kotoba.core.mcp.browser_launch as bl

    monkeypatch.setattr(bl, "ensure_browser", _fake_ensure_browser)
    mcp = _FakeMCP({})
    asyncio.run(loop._ensure_work_browser(_Ctx(mcp)))
    assert launched == []
    assert mcp.connect_calls and mcp.connect_calls[0][0] == "browser"


def test_work_guidance_text_includes_browser_workflow_when_browser_offered():
    # With a browser tool present, the work guidance carries BOTH the anti-fabrication block AND the
    # snapshot->target browser workflow (so the model never guesses a ref like the failing run did).
    g = loop._work_guidance_text({"browser__browser_snapshot", "write_file"})
    low = g.lower()
    assert "snapshot" in low and "target" in low
    # Unique to VERIFICATION_GUIDANCE — a generic "verif"/"never" match holds even with the block deleted.
    assert "NEVER fabricate or assume success" in g


def test_work_guidance_text_no_browser_workflow_without_browser():
    g = loop._work_guidance_text({"write_file", "web_search"}).lower()
    assert "snapshot" not in g  # no browser tool → no browser workflow text (still has verification)
