"""Work mode must not open a browser WINDOW unless the task actually drives the browser.

Proven by counting launches through the real launch code path. A task that never calls a browser
tool: ZERO launches, while the browser MCP still pre-connects and its tools are offered from the
first model call. A task that does drive the browser: EXACTLY ONE launch, triggered by the first
browser tool call (never before it), and that call's retry returns the real result.

Guidance about snapshot-then-target ships at loop start when the browser pre-connects, and the
mid-loop fallback still injects it once if the browser genuinely arrives late.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.core.loop as loop
import kotoba.core.mcp.browser_launch as bl
import kotoba.core.mcp.client as mc
import kotoba.tools.registry as reg
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args, call_id):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)
    return types.SimpleNamespace(type="response.output_item.done", item=item)


class _FakeStream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _FakeResponses:
    """Scripted stream per create() call; snapshots the offered tool names and the input items at
    call time (the loop mutates input_items in place, so a live reference would lie)."""

    def __init__(self, scripts):
        self._scripts = scripts
        self.calls: list[dict] = []

    async def create(self, **kw):
        self.calls.append({
            "tools": {t.get("name") or t.get("type") for t in kw.get("tools", [])},
            "input": [dict(it) if isinstance(it, dict) else it for it in kw.get("input", [])],
        })
        evs = self._scripts[min(len(self.calls) - 1, len(self._scripts) - 1)]
        return _FakeStream(evs)


class _FakeClient:
    def __init__(self, scripts):
        self.responses = _FakeResponses(scripts)


class _FakeDB:
    async def insert_audit_log(self, **kw):
        pass


def _err(text):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(text=text, type="text", data=None)], isError=True
    )


def _ok(text):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(text=text, type="text", data=None)], isError=False
    )


class _ColdThenOkGroup:
    """Models the real @playwright/mcp against a cold CDP port: every call fails browser-down until
    the browser is actually launched (state['port_up']), then succeeds."""

    def __init__(self, state):
        self.calls = 0
        self._state = state

    async def call_tool(self, name, args):
        self.calls += 1
        if not self._state["port_up"]:
            return _err("Error: connect ECONNREFUSED 127.0.0.1:9245")
        return _ok("Navigated to example.org — page loaded")


class _LazyMCP:
    """Fake MCPManager: connect('browser', …) registers a browser tool backed by a real _MCPProxy,
    exactly what the real manager does — without spawning npx."""

    def __init__(self, group):
        self.server_tools: dict[str, list[str]] = {}
        self.connect_calls: list[str] = []
        self._group = group

    async def connect(self, name, cfg):
        self.connect_calls.append(name)
        ns = "browser__browser_navigate"
        proxy = mc._MCPProxy(types.SimpleNamespace(group=self._group), ns)
        reg.register(ToolSpec(
            name=ns, module=proxy,
            schema={"type": "function", "name": ns, "parameters": {"type": "object", "properties": {}}},
            toolset="mcp:browser", risk="network",
        ))
        self.server_tools[name] = [ns]
        return [ns]


def _instrument_launches(monkeypatch, tmp_path, state, group=None):
    """Count real browser launches through the genuine ensure_browser path: the port reads cold until
    a (fake) Popen 'opens' it. Records the browser-tool call count at each launch, so a test can
    assert the launch was triggered by a tool call rather than up-front."""
    monkeypatch.setenv("KOTOBA_BROWSER_CDP", "http://127.0.0.1:9245")
    monkeypatch.setenv("KOTOBA_BROWSER_PROFILE", str(tmp_path / "profile"))
    monkeypatch.setattr(bl, "_port_open", lambda host, port: state["port_up"])
    monkeypatch.setattr(bl, "_detect_browser", lambda: "/usr/bin/fake-brave")

    def fake_popen(argv, **kw):
        state["launches"] += 1
        state["at_group_calls"].append(group.calls if group is not None else -1)
        state["port_up"] = True
        return types.SimpleNamespace(pid=4242)

    monkeypatch.setattr(bl, "subprocess", types.SimpleNamespace(Popen=fake_popen))


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def _register_write_tool():
    async def execute(args, ctx):
        return "Wrote 5 characters to hello.txt."
    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": "write_file"}, __name__="tools.x.write_file",
        ANNOUNCE="", HEARTBEAT=[], COMPLETE="done", FAIL="oops", execute=execute,
    )
    reg.register(ToolSpec(name="write_file", module=mod, schema=mod.SCHEMA, toolset="file", risk="write"))


def _run_work_turn(scripts, session, mcp):
    client = _FakeClient(scripts)
    ctx = ToolContext(db=_FakeDB(), session_id=session, client=None, mode="work", mcp=mcp)
    ctx.approval = None

    async def go():
        return await loop._run_iterations(
            client, ctx, [{"role": "user", "content": "do the thing"}], asyncio.Queue(), {},
            max_iterations=5, mode="work", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )

    final = asyncio.run(go())
    return client.responses.calls, final


def _dev_texts(items):
    return [it.get("content", "") for it in items
            if isinstance(it, dict) and it.get("role") == "developer"]


def test_no_web_work_task_opens_no_browser_window(clean_registry, monkeypatch, tmp_path):
    """The bar, kind A: a work task that writes a file and never touches the web must launch ZERO
    browsers — while the MCP still pre-connects and the browser tools are still offered from the
    first model call (so the zero is laziness, not absence)."""
    state = {"launches": 0, "port_up": False, "at_group_calls": []}
    group = _ColdThenOkGroup(state)
    _instrument_launches(monkeypatch, tmp_path, state, group)
    mcp = _LazyMCP(group)
    _register_write_tool()

    scripts = [
        [_ev_call("write_file", {"path": "hello.txt", "content": "hello"}, "c_wf")],
        [_ev_text("All done!")],
    ]
    calls, final = _run_work_turn(scripts, "lb_noweb", mcp)

    assert state["launches"] == 0
    assert mcp.connect_calls == ["browser"]
    assert "browser__browser_navigate" in calls[0]["tools"]
    assert final == "All done!"


def test_web_work_task_launches_once_at_first_browser_call(clean_registry, monkeypatch, tmp_path):
    """The bar, kind B: a work task that drives the browser gets exactly ONE launch, triggered BY
    the first browser tool call (browser-down autostart + one retry), never up-front — and the
    retried call feeds the model the REAL page result, with no mcp_install round-trip first."""
    state = {"launches": 0, "port_up": False, "at_group_calls": []}
    group = _ColdThenOkGroup(state)
    _instrument_launches(monkeypatch, tmp_path, state, group)
    mcp = _LazyMCP(group)

    scripts = [
        [_ev_call("browser__browser_navigate", {"url": "https://example.org"}, "c_nav")],
        [_ev_text("Opened it.")],
    ]
    calls, final = _run_work_turn(scripts, "lb_web", mcp)

    assert state["launches"] == 1
    assert state["at_group_calls"] == [1]
    assert group.calls == 2
    assert "browser__browser_navigate" in calls[0]["tools"]
    outputs = [it for it in calls[1]["input"]
               if isinstance(it, dict) and it.get("type") == "function_call_output"]
    assert outputs and "Navigated to example.org" in str(outputs[0]["output"])
    assert final == "Opened it."


def test_regression_no_mcp_install_roundtrip(clean_registry, monkeypatch, tmp_path):
    """Bug 1 guard (fails against a naive removal of _ensure_work_browser): with a real browser
    configured but its MCP not yet connected, loop start reconnects it, so the browser tools are in
    the FIRST iteration's offered toolset — the model never needs an mcp_install round-trip."""
    state = {"launches": 0, "port_up": False, "at_group_calls": []}
    group = _ColdThenOkGroup(state)
    _instrument_launches(monkeypatch, tmp_path, state, group)
    mcp = _LazyMCP(group)

    calls, _ = _run_work_turn([[_ev_text("Hi.")]], "lb_reg1", mcp)

    assert mcp.connect_calls == ["browser"]
    assert "browser__browser_navigate" in calls[0]["tools"]


def test_regression_guidance_carries_browser_workflow_at_loop_start(clean_registry, monkeypatch, tmp_path):
    """Bug 2 guard (fails against a naive removal): the loop-start guidance is built AFTER the
    browser pre-connect, so the very first model call already carries the snapshot→target workflow
    and the anti-fabrication block — the model never acts on a guessed ref."""
    state = {"launches": 0, "port_up": False, "at_group_calls": []}
    group = _ColdThenOkGroup(state)
    _instrument_launches(monkeypatch, tmp_path, state, group)
    mcp = _LazyMCP(group)

    calls, _ = _run_work_turn([[_ev_text("Hi.")]], "lb_reg2", mcp)

    guidance = "\n".join(_dev_texts(calls[0]["input"]))
    assert "browser_snapshot" in guidance
    assert "target" in guidance
    assert "NEVER fabricate or assume success" in guidance


def test_mid_loop_browser_arrival_still_injects_guidance(clean_registry, monkeypatch, tmp_path):
    """The fallback half of bug 2: when no real browser is configured (no CDP → no pre-connect) and
    the browser tools genuinely arrive mid-loop (an mcp_install), the next iteration both offers
    them and appends the browser workflow guidance once."""
    monkeypatch.delenv("KOTOBA_BROWSER_CDP", raising=False)

    async def execute(args, ctx):
        ns = "browser__browser_navigate"
        reg.register(ToolSpec(
            name=ns, module=types.SimpleNamespace(
                SCHEMA={"type": "function", "name": ns}, __name__="tools.x.nav",
                ANNOUNCE="", HEARTBEAT=[], COMPLETE="", FAIL="",
                execute=lambda a, c: None,
            ),
            schema={"type": "function", "name": ns, "parameters": {"type": "object", "properties": {}}},
            toolset="mcp:browser", risk="network",
        ))
        return "Connected the browser — its tools are available now."
    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": "fake_install"}, __name__="tools.x.fake_install",
        ANNOUNCE="", HEARTBEAT=[], COMPLETE="", FAIL="", execute=execute,
    )
    reg.register(ToolSpec(name="fake_install", module=mod, schema=mod.SCHEMA, toolset="mcp", risk="network"))

    scripts = [
        [_ev_call("fake_install", {"name": "browser"}, "c_inst")],
        [_ev_text("Ready.")],
    ]
    calls, _ = _run_work_turn(scripts, "lb_midloop", _LazyMCP(_ColdThenOkGroup({"port_up": True})))

    first = "\n".join(_dev_texts(calls[0]["input"]))
    assert "browser_snapshot" not in first
    assert "browser__browser_navigate" in calls[1]["tools"]
    second = "\n".join(_dev_texts(calls[1]["input"]))
    assert "browser_snapshot" in second and "target" in second
