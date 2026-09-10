"""The browser tool denylist is OPT-IN (KOTOBA_BROWSER_TOOL_DENY). By default NOTHING is blocked — every
browser tool, including run_code/evaluate, stays available (the model may need the raw-JS escape hatch).
We steer toward the accessibility flow with guidance + higher work-mode reasoning, not by removing tools."""
from __future__ import annotations

from kotoba.core.mcp.client import _is_bypass_browser_tool


def test_nothing_blocked_by_default(monkeypatch):
    monkeypatch.delenv("KOTOBA_BROWSER_TOOL_DENY", raising=False)
    for t in ("browser__browser_run_code_unsafe", "browser__browser_evaluate",
              "browser__browser_navigate", "browser__browser_snapshot"):
        assert not _is_bypass_browser_tool(t), t


def test_denylist_when_explicitly_set(monkeypatch):
    monkeypatch.setenv("KOTOBA_BROWSER_TOOL_DENY", "run_code,evaluate")
    assert _is_bypass_browser_tool("browser__browser_run_code_unsafe")
    assert _is_bypass_browser_tool("browser__browser_evaluate")
    assert not _is_bypass_browser_tool("browser__browser_click")
    assert not _is_bypass_browser_tool("github__run_code")  # only the browser server is affected
