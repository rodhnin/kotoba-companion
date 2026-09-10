"""Built-in web_search must be capped per loop run (KOTOBA_WEB_SEARCH_LIMIT).

OpenAI executes web_search server-side: it streams back `web_search_call` output items, never a
function_call, so the per_tool cap never saw it. Live QA caught one research turn running 25+
near-duplicate searches (inside a delegate helper), throttling TPM for the whole turn and pushing the
helpers into the delegate TIMEOUT. These tests pin the fix: the loop counts web_search_call items,
accumulating across iterations; past the cap the built-in is dropped from the offered tools and the model
is told ONCE (developer note) to synthesize from what it has; the same cap binds a subagent run (a
delegate helper runs its own _run_iterations, so each helper gets its own budget).
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.core import events
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args, i):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=f"c{i}")
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _ev_ws(i):
    item = types.SimpleNamespace(
        type="web_search_call", id=f"ws{i}", action=types.SimpleNamespace(query=f"query {i}")
    )
    return types.SimpleNamespace(type="response.output_item.done", item=item)


class _FakeStream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _RecordingResponses:
    """Snapshots tools + input at each create() call (input_items mutates in place across iterations)."""

    def __init__(self, scripts):
        self._scripts, self._i = scripts, 0
        self.calls: list[dict] = []

    async def create(self, **kw):
        self.calls.append({"tools": list(kw.get("tools") or []), "input": list(kw.get("input") or [])})
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _FakeStream(evs)


class _FakeClient:
    def __init__(self, scripts):
        self.responses = _RecordingResponses(scripts)


class _FakeDB:
    async def insert_audit_log(self, **kw):
        pass


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def _register_probe():
    async def execute(args, ctx):
        return "probe ok"

    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": "wscap_probe"},
        __name__="kotoba.tools.x.wscap_probe", ANNOUNCE="", HEARTBEAT=[], COMPLETE="", FAIL="", execute=execute,
    )
    reg.register(ToolSpec(name="wscap_probe", module=mod, schema=mod.SCHEMA, toolset="web", risk="read"))


def _has_web_search(tools):
    return any(t.get("type") == "web_search" for t in tools)


def _run(scripts, sess, subagent_id=None):
    events.register(sess)
    ctx = ToolContext(db=_FakeDB(), session_id=sess, client=None, mode="work", subagent_id=subagent_id)
    ctx.approval = None
    client = _FakeClient(scripts)

    async def go():
        await loop._run_iterations(
            client, ctx, [{"role": "user", "content": "compare frameworks"}], asyncio.Queue(), {},
            max_iterations=8, mode="work", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )
    asyncio.run(go())
    events.unregister(sess)
    return client.responses.calls


def test_web_search_dropped_and_note_injected_past_cap(clean_registry, monkeypatch):
    monkeypatch.setattr(loop, "_WEB_SEARCH_LIMIT", 3)
    _register_probe()
    scripts = [
        [_ev_ws(1), _ev_ws(2), _ev_ws(3), _ev_call("wscap_probe", {"i": 1}, 1)],
        [_ev_call("wscap_probe", {"i": 2}, 2)],
        [_ev_text("done")],
    ]
    calls = _run(scripts, "wscap_main")

    assert _has_web_search(calls[0]["tools"]), "web_search offered before the cap"
    assert not _has_web_search(calls[1]["tools"]), "web_search still offered after hitting the cap"
    assert not _has_web_search(calls[2]["tools"]), "web_search re-offered later in the same turn"
    assert any(t.get("name") == "wscap_probe" for t in calls[1]["tools"]), "only web_search must be dropped"

    def _cap_notes(items):
        return [m for m in items if isinstance(m, dict) and m.get("role") == "developer"
                and str(m.get("content")).startswith("You've already run")]

    assert not _cap_notes(calls[0]["input"])
    assert len(_cap_notes(calls[1]["input"])) == 1, "the cap note must be injected before the next model call"
    note = _cap_notes(calls[1]["input"])[0]["content"]
    assert "SYNTHESIZE" in note and "no longer available" in note
    assert len(_cap_notes(calls[2]["input"])) == 1, "the note is injected once, not every iteration"


def test_web_search_count_accumulates_across_iterations(clean_registry, monkeypatch):
    monkeypatch.setattr(loop, "_WEB_SEARCH_LIMIT", 3)
    _register_probe()
    scripts = [
        [_ev_ws(1), _ev_ws(2), _ev_call("wscap_probe", {"i": 1}, 1)],
        [_ev_ws(3), _ev_ws(4), _ev_call("wscap_probe", {"i": 2}, 2)],
        [_ev_call("wscap_probe", {"i": 3}, 3)],
        [_ev_text("done")],
    ]
    calls = _run(scripts, "wscap_accum")

    assert _has_web_search(calls[0]["tools"])
    assert _has_web_search(calls[1]["tools"]), "2 < 3: still under the cap after the first iteration"
    assert not _has_web_search(calls[2]["tools"]), "2+2 >= 3: capped from the third iteration on"


def test_web_search_cap_applies_inside_subagent(clean_registry, monkeypatch):
    monkeypatch.setattr(loop, "_WEB_SEARCH_LIMIT", 2)
    _register_probe()
    scripts = [
        [_ev_ws(1), _ev_ws(2), _ev_call("wscap_probe", {"i": 1}, 1)],
        [_ev_text("helper summary")],
    ]
    calls = _run(scripts, "wscap_sub", subagent_id="sub1")

    assert _has_web_search(calls[0]["tools"])
    assert not _has_web_search(calls[1]["tools"]), "the cap must bind a delegate helper's loop too"


def test_web_search_cap_default_and_env_convention():
    """The shipped default, overridable with KOTOBA_WEB_SEARCH_LIMIT."""
    assert loop._WEB_SEARCH_LIMIT == 8


def test_max_tool_calls_bounds_each_response_to_remaining_budget(clean_registry, monkeypatch):
    """OpenAI chains several web_search_call items inside ONE response (seen live: 14 against a cap of 8),
    so the request must also carry max_tool_calls = remaining budget for the server to enforce."""
    monkeypatch.setattr(loop, "_WEB_SEARCH_LIMIT", 8)
    monkeypatch.setattr("kotoba.core.providers.active_provider_id", lambda: "openai")
    _register_probe()
    scripts = [
        [_ev_ws(1), _ev_ws(2), _ev_ws(3), _ev_call("wscap_probe", {"i": 1}, 1)],
        [_ev_call("wscap_probe", {"i": 2}, 2)],
        [_ev_text("done")],
    ]

    captured: list = []

    class _Rec(_RecordingResponses):
        async def create(self, **kw):
            captured.append(kw.get("max_tool_calls"))
            return await super().create(**kw)

    events.register("wscap_mtc")
    ctx = ToolContext(db=_FakeDB(), session_id="wscap_mtc", client=None, mode="work")
    ctx.approval = None
    client = _FakeClient(scripts)
    client.responses = _Rec(scripts)

    async def go():
        await loop._run_iterations(
            client, ctx, [{"role": "user", "content": "x"}], asyncio.Queue(), {},
            max_iterations=8, mode="work", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )
    asyncio.run(go())
    events.unregister("wscap_mtc")

    assert captured[0] == 8, "full budget on the first call"
    assert captured[1] == 5, "3 searches done: the next response may run at most 5 more"


def test_max_tool_calls_not_sent_to_non_openai_providers(monkeypatch):
    monkeypatch.setattr("kotoba.core.providers.active_provider_id", lambda: "xai")
    assert loop._web_search_kwargs([{"type": "web_search"}], 0) == {}
    monkeypatch.setattr("kotoba.core.providers.active_provider_id", lambda: "openai")
    assert loop._web_search_kwargs([{"type": "web_search"}], 0) == {"max_tool_calls": loop._WEB_SEARCH_LIMIT}
    assert loop._web_search_kwargs([{"type": "function", "name": "shell"}], 0) == {}, \
        "no built-in offered: don't send the parameter"


def test_a_web_search_call_row_says_what_the_call_actually_did():
    """The built-in has three actions and only `search` carries a query: `open_page` carries a url,
    `find_in_page` a pattern, and the docs say even a search "usually (but not always)" reports what it
    searched for (developers.openai.com/api/docs/guides/tools-web-search). Read as
    a query alone, all three came back empty and the row read `WEB  web search · searched the web` with
    nothing on it, beside sibling rows carrying the real query.

    In plain words: the magnifying-glass emoji this used to prefix was the one emoji that reached the
    glass — outside the renderer's verified glyph set, so a `?` under --ascii — and the emoji had to
    go. Every branch of this string is ASCII."""
    def item(**action):
        return types.SimpleNamespace(type="web_search_call", id="ws",
                                     action=types.SimpleNamespace(**action))

    assert loop._web_action_text(item(type="search", query="live2d history")) == "searched for 'live2d history'"
    assert loop._web_action_text(item(type="search", queries=["a", "b"])) == "searched for 'a, b'"
    assert loop._web_action_text(
        item(type="open_page", url="https://live2d.com/en/about/")) == "opened https://live2d.com/en/about/"
    assert loop._web_action_text(
        item(type="find_in_page", url="https://x/y", pattern="Cybernoids")) \
        == "looked for 'Cybernoids' in the page"
    assert loop._web_action_text(item(type="search")) == "searched the web"
    assert loop._web_action_text(types.SimpleNamespace(type="web_search_call", id="ws")) \
        == "searched the web"
    assert loop._web_action_text(
        types.SimpleNamespace(action={"type": "open_page", "url": "https://x/y"})) == "opened https://x/y"

    for text in (
        loop._web_action_text(item(type="search", query="live2d history")),
        loop._web_action_text(item(type="open_page", url="https://live2d.com/")),
        loop._web_action_text(item(type="find_in_page", pattern="Cybernoids")),
        loop._web_action_text(item(type="search")),
    ):
        assert text.isascii(), f"a row detail must carry no glyph of its own: {text!r}"
