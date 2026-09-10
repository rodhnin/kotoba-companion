"""Five correctness fixes with nothing in common but the sweep that found them.

`fetch_recent_turns` returns the MOST RECENT turns in chronological order with no duplicate of the
current turn — the old query was ASC + LIMIT, which handed back the OLDEST ones instead. `events.unregister`
carries an identity guard, so a stale connection tearing down cannot take a newer connection's queue with
it. A subagent restricted to the `browser` toolset receives the `mcp:browser` tools. `build_soul_patterns`
tolerates a minimal plugin — SCHEMA and execute, no voice attributes. `model_call_kwargs` and
`is_reasoning_model` gate on the ROLE's model, so a delegate on a non-reasoning model is never sent
reasoning kwargs, which the provider answers with a 400.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def db(tmp_path):
    """Closed on the way out. This returned the database open, and the only thing that ever stopped its
    worker was `Connection.__del__` firing when the last reference went — a non-daemon thread whose exit
    depends on refcounting, in a suite whose own conftest says atexit is too late to help."""
    from kotoba.db.database import Database

    d = Database("sqlite:///" + str(tmp_path / "d1.db"))
    asyncio.run(d.connect())
    yield d
    asyncio.run(d.close())


def test_recent_turns_are_the_latest_in_chronological_order(db):
    """Thirty turns in, a limit of five must give back m25 to m29 in that order — the newest five,
    oldest first. The bug returned m0 to m4, the oldest five in the conversation."""
    async def go():
        await db.ensure_session("s1")
        for i in range(30):
            await db.insert_turn("s1", "user" if i % 2 == 0 else "assistant", f"m{i}")
        rows = await db.fetch_recent_turns("s1", limit=5)
        return rows

    rows = asyncio.run(go())
    contents = [r["content"] for r in rows]
    assert contents == ["m25", "m26", "m27", "m28", "m29"]


def test_events_unregister_identity_guard():
    """A reconnect replaces the session's queue. When the OLD connection then tears down, it must
    unregister its OWN queue and leave the live one in place; the live connection's own teardown
    still removes the session."""
    from kotoba.core import events

    events.event_queues.clear()
    q1 = events.register("sX")
    q2 = events.register("sX")
    assert events.event_queues["sX"] is q2
    events.unregister("sX", q1)
    assert events.event_queues.get("sX") is q2
    events.unregister("sX", q2)
    assert "sX" not in events.event_queues


def test_schemas_for_browser_filter_includes_mcp_browser(monkeypatch):
    """A delegate asking for the `browser` toolset must receive the tools a connected MCP browser
    server registers under `mcp:browser`, not just the built-in ones. The spec below stands in for
    one of those connected tools."""
    import kotoba.tools.registry as reg

    Spec = type("Spec", (), {})
    fake = Spec()
    fake.toolset = "mcp:browser"
    fake.built_in = False
    fake.risk = "network"
    fake.name = "browser__navigate"
    fake.schema = {"type": "function", "name": "browser__navigate"}
    fake.check = None

    monkeypatch.setattr(reg, "_passes_check", lambda spec: True)
    monkeypatch.setattr(reg, "_REGISTRY", {"browser__navigate": fake})
    out = reg.schemas_for(mode="work", toolset_filter="browser")
    assert any(s["name"] == "browser__navigate" for s in out), "delegate(toolset='browser') got zero browser tools"


def test_voice_patterns_minimal_plugin_no_crash(monkeypatch):
    """A plugin is valid with nothing but SCHEMA and execute — the voice attributes (ANNOUNCE,
    HEARTBEAT, COMPLETE, FAIL) are optional. Building the patterns must fall back to empty strings
    for such a plugin rather than raising AttributeError."""
    import kotoba.core.voice_patterns as vp

    class _Minimal:
        SCHEMA = {"type": "function", "name": "x"}

    monkeypatch.setattr(vp, "TOOL_REGISTRY", {"x": _Minimal()})
    patterns = vp.build_soul_patterns({})
    assert patterns["x"] == {"before": "", "heartbeat": [], "after": "", "fail": ""}


def test_model_call_kwargs_role_aware(monkeypatch, tmp_path):
    """Reasoning kwargs follow the ROLE's model, not the companion's.

    With a reasoning model configured for the companion and a non-reasoning one for the code role,
    the companion call carries the reasoning kwargs and the delegate's call carries none — sending
    them to a non-reasoning model is what the provider answers with a 400. `is_reasoning_model`
    reads the same way."""
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "s.yaml"))
    monkeypatch.setenv("KOTOBA_KEYSTORE_KEY_FILE", str(tmp_path / "ks"))
    from kotoba.core import app_settings, llm

    app_settings.set_runtime("provider", "openai")
    app_settings.set_runtime("reasoning_effort", "low")
    app_settings.set_runtime("model", "gpt-5.4-mini")        # companion: reasoning
    app_settings.set_runtime("code_model", "gpt-4o-mini")    # code role: NON-reasoning

    assert "reasoning" in llm.model_call_kwargs("companion")
    assert llm.model_call_kwargs("work", role="code") == {}
    assert llm.is_reasoning_model() is True
    assert llm.is_reasoning_model(role="code") is False
