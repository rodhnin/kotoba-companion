"""Browser MCP consistency: when a browser tool errors or returns nothing (stale snapshot ref, transient
click miss), the model used to get None and would blindly repeat the same failing action → the "loses
consistency the moment it touches the browser" loop. Now a browser tool failure returns a SHORT actionable
hint (re-snapshot, then retry) so the reasoning model can self-correct instead of flailing."""
from __future__ import annotations

import asyncio
import types

import kotoba.core.mcp.client as mc


class _Result:
    def __init__(self, content, is_error=False):
        self.content = content
        self.isError = is_error


class _Group:
    def __init__(self, result=None, raises=False):
        self._result = result
        self._raises = raises

    async def call_tool(self, name, args):
        if self._raises:
            raise RuntimeError("Session terminated")
        return self._result


def _proxy(group, ns_name):
    mgr = types.SimpleNamespace(group=group)
    return mc._MCPProxy(mgr, ns_name)


def test_browser_iserror_returns_actionable_hint_not_none():
    block = types.SimpleNamespace(text="ref not found: page does not match any elements", type="text", data=None)
    p = _proxy(_Group(_Result([block], is_error=True)), "browser__browser_click")
    out = asyncio.run(p.execute({"ref": "e7"}, None))
    assert out is not None and isinstance(out, str)
    assert "snapshot" in out.lower()                 # tells the model how to recover
    assert "ref not found" in out.lower()            # surfaces the real reason so it can adapt


def test_browser_empty_result_returns_hint_not_none():
    p = _proxy(_Group(_Result([], is_error=False)), "browser__browser_snapshot")
    out = asyncio.run(p.execute({}, None))
    assert out is not None and "snapshot" in out.lower()


def test_non_browser_iserror_still_returns_none():
    # A non-browser MCP tool keeps the old behavior (None → graceful fail line), no browser hint.
    block = types.SimpleNamespace(text="some failure", type="text", data=None)
    p = _proxy(_Group(_Result([block], is_error=True)), "filesystem__read_file")
    out = asyncio.run(p.execute({}, None))
    assert out is None


def test_navigate_to_dns_error_page_returns_stop_not_retry():
    # browser_navigate onto a DNS/network error page returns ok (the nav happened, onto Chrome's error
    # page). Without detection the model retries URL variants forever (the example.com loop). It must get
    # a STOP instruction instead.
    block = types.SimpleNamespace(text="Navigated to https://example.com\nERR_NAME_NOT_RESOLVED", type="text", data=None)
    p = _proxy(_Group(_Result([block], is_error=False)), "browser__browser_navigate")
    out = asyncio.run(p.execute({"url": "https://example.com"}, None))
    assert out is not None and isinstance(out, str)
    low = out.lower()
    assert "did not load" in low or "unreachable" in low
    assert "stop" in low and ("not" in low)            # tells it to STOP, not retry
    assert "data:" in low or "fabricate" in low        # and not to fabricate a page


def test_navigate_dns_error_as_iserror_also_stops():
    # The DNS error can surface as isError=True (ERR_NAME_NOT_RESOLVED). It must ALSO get the STOP message,
    # not the generic "re-snapshot and retry" hint (which made her snapshot+screenshot the error page).
    block = types.SimpleNamespace(text="Error: net::ERR_NAME_NOT_RESOLVED at http://example.com/", type="text", data=None)
    p = _proxy(_Group(_Result([block], is_error=True)), "browser__browser_navigate")
    out = asyncio.run(p.execute({"url": "http://example.com"}, None))
    low = out.lower()
    assert "unreachable" in low and "stop" in low
    assert "re-snapshot" not in low and "snapshot to get the current refs" not in low  # NOT the generic hint


def test_navigate_to_good_page_passes_through():
    # A normal page must NOT trip the load-failure stop — the model sees the real content.
    block = types.SimpleNamespace(text="# Cat\nFrom Wikipedia, the free encyclopedia", type="text", data=None)
    p = _proxy(_Group(_Result([block], is_error=False)), "browser__browser_navigate")
    out = asyncio.run(p.execute({"url": "https://en.wikipedia.org/wiki/Cat"}, None))
    assert "Wikipedia" in out and "STOP" not in out


def test_css_selector_error_gets_snapshot_ref_guidance():
    # The model used a CSS selector (#email / input[name=q]) with @playwright/mcp, which is ref-based →
    # "does not match any elements". The hint must steer it to snapshot→ref and warn off CSS selectors.
    block = types.SimpleNamespace(text='Error: "#email" does not match any elements.', type="text", data=None)
    p = _proxy(_Group(_Result([block], is_error=True)), "browser__browser_fill_form")
    out = asyncio.run(p.execute({"fields": [{"ref": "#email", "value": "x"}]}, None))
    assert out is not None
    low = out.lower()
    assert "snapshot" in low and "ref" in low
    assert "css" in low or "selector" in low      # explicitly tells it NOT to use CSS selectors
