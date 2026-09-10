"""When the browser MCP is connected but the REAL browser behind the CDP port isn't running, every
browser_* call fails with ECONNREFUSED (the @playwright/mcp process is alive, the browser is not). The
proxy must detect that, AUTO-LAUNCH the browser (ensure_browser), and retry the call ONCE — so "open the
browser yourself" works even when the MCP was already connected to a since-closed browser. If autostart
can't bring it up, fall back to the normal recovery hint (no hallucinated success)."""
from __future__ import annotations

import asyncio
import types

import kotoba.core.mcp.client as mc


def _err(text):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(text=text, type="text", data=None)], isError=True
    )


def _ok(text):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(text=text, type="text", data=None)], isError=False
    )


class _Group:
    """Fails the first N calls with a CDP connection error, then succeeds."""

    def __init__(self, fail_times, fail_text):
        self.calls = 0
        self.fail_times = fail_times
        self.fail_text = fail_text

    async def call_tool(self, name, args):
        self.calls += 1
        if self.calls <= self.fail_times:
            return _err(self.fail_text)
        return _ok("Navigated to braid.com")


def _proxy(group, ns_name="browser__browser_navigate"):
    return mc._MCPProxy(types.SimpleNamespace(group=group), ns_name)


def test_detects_browser_down_error():
    assert mc._is_browser_down_error("Error: connect ECONNREFUSED 127.0.0.1:9222") is True
    assert mc._is_browser_down_error("retrieving websocket url from http://127.0.0.1:9222") is True
    assert mc._is_browser_down_error("stale element ref e5 not found") is False
    assert mc._is_browser_down_error("") is False


def test_relaunches_and_retries_on_conn_refused(monkeypatch):
    launched = {"n": 0}

    async def fake_ensure():
        launched["n"] += 1
        return True  # browser came up

    monkeypatch.setattr(mc, "_autostart_browser", fake_ensure)
    group = _Group(fail_times=1, fail_text="Error: connect ECONNREFUSED 127.0.0.1:9222")
    ctx = types.SimpleNamespace(session_id="s1")
    out = asyncio.run(_proxy(group).execute({"url": "https://braid.com"}, ctx))

    assert launched["n"] == 1            # autostart was triggered by the conn error
    assert group.calls == 2              # original call + ONE retry
    assert "Navigated to braid.com" in str(out)  # retry succeeded → real result, not a fake success


def test_no_relaunch_on_stale_ref(monkeypatch):
    # A normal stale-ref error must NOT trigger a browser launch — only a CDP-down error does.
    launched = {"n": 0}

    async def fake_ensure():
        launched["n"] += 1
        return True

    monkeypatch.setattr(mc, "_autostart_browser", fake_ensure)
    group = _Group(fail_times=1, fail_text="stale element ref e5 not found")
    ctx = types.SimpleNamespace(session_id="s1")
    out = asyncio.run(_proxy(group).execute({"ref": "e5"}, ctx))

    assert launched["n"] == 0            # not a browser-down error → no launch
    assert group.calls == 1              # no retry; recovery hint returned
    assert "snapshot" in str(out).lower()  # the usual stale-ref recovery hint


def test_relaunch_fails_then_recovery_hint(monkeypatch):
    # Autostart can't bring the browser up → no retry, return an honest recovery hint (never a fake OK).
    async def fake_ensure():
        return False

    monkeypatch.setattr(mc, "_autostart_browser", fake_ensure)
    group = _Group(fail_times=9, fail_text="Error: connect ECONNREFUSED 127.0.0.1:9222")
    ctx = types.SimpleNamespace(session_id="s1")
    out = asyncio.run(_proxy(group).execute({"url": "x"}, ctx))

    assert group.calls == 1              # launch failed → did not retry
    assert "didn't take" in str(out) or "snapshot" in str(out).lower()


def test_browser_down_hint_names_the_browser_not_stale_refs(monkeypatch):
    """N4 second half: when the error is browser-DOWN and autostart can't recover it, the model must be
    told the browser isn't running — NOT handed the stale-ref hint ('take a fresh snapshot to get the
    current refs'), which would send it re-snapshotting a browser that doesn't exist."""
    async def fake_ensure():
        return False

    monkeypatch.setattr(mc, "_autostart_browser", fake_ensure)
    group = _Group(fail_times=9, fail_text="Timeout 30000ms exceeded retrieving websocket url")
    ctx = types.SimpleNamespace(session_id="s1")
    out = str(asyncio.run(_proxy(group).execute({"ref": "e5"}, ctx)))
    low = out.lower()

    assert group.calls == 1, "browser is down and could not be started → no retry"
    assert "isn't running" in low or "not running" in low or "unreachable" in low
    assert "stop" in low
    assert "get the current refs" not in low, "must not be the stale-ref recovery hint"


def test_stale_ref_after_recovered_browser_still_gets_the_snapshot_hint(monkeypatch):
    """The browser-down hint is scoped to the UNRECOVERED case. If autostart succeeds but the retried
    call then hits a genuine stale ref, the normal snapshot hint (not the browser-down one) is returned."""
    async def fake_ensure():
        return True

    monkeypatch.setattr(mc, "_autostart_browser", fake_ensure)

    class _DownThenStale:
        def __init__(self):
            self.calls = 0

        async def call_tool(self, name, args):
            self.calls += 1
            text = ("Error: connect ECONNREFUSED 127.0.0.1:9222" if self.calls == 1
                    else "stale element ref e5 not found")
            return _err(text)

    group = _DownThenStale()
    out = str(asyncio.run(_proxy(group).execute({"ref": "e5"}, types.SimpleNamespace(session_id="s1"))))
    assert group.calls == 2, "autostart succeeded → one retry"
    assert "snapshot" in out.lower() and "isn't running" not in out.lower()
