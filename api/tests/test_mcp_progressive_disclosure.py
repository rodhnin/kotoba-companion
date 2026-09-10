"""Progressive MCP disclosure: a connected server's tools are DEFERRED (not in the
toolset) until the model activates it — so 44 unused github tools don't bloat/confuse the toolset (selection
accuracy drops past ~30-50 tools). Browser is always-active; others load via activate_tools / on install."""
from __future__ import annotations

import asyncio
import types

import pytest

import kotoba.core.mcp_active as ma
import kotoba.tools.registry as reg
from kotoba.tools.registry import ToolSpec, schemas_for


def setup_function():
    ma._active.clear()


def test_always_active_includes_browser():
    assert "browser" in ma.always_active()
    assert ma.active("s1") == ma.always_active()           # nothing activated yet
    ma.activate("s1", "github")
    assert "github" in ma.active("s1") and "browser" in ma.active("s1")
    assert "github" not in ma.active("other")              # per-session


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def _fake_mcp_tool(ns_name, server):
    mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": ns_name}, __name__=ns_name)
    reg.register(ToolSpec(name=ns_name, module=mod, schema=mod.SCHEMA, toolset=f"mcp:{server}", risk="network"))


def test_schemas_for_defers_inactive_mcp_servers(clean_registry):
    _fake_mcp_tool("browser__browser_click", "browser")
    _fake_mcp_tool("github__create_issue", "github")
    names = lambda act: {t.get("name") for t in schemas_for("work", {"network"}, None, act)}
    # only browser active → github deferred (not offered)
    offered = names({"browser"})
    assert "browser__browser_click" in offered and "github__create_issue" not in offered
    # after activating github → both offered
    offered2 = names({"browser", "github"})
    assert "github__create_issue" in offered2
    # mcp_active=None → no MCP filtering (back-compat)
    assert "github__create_issue" in names(None)


def test_activate_tools_marks_server_active():
    import kotoba.tools.builtin.activate_tools as at

    class _MCP:
        server_tools = {"github": ["github__create_issue", "github__search"], "browser": ["browser__x"]}

    ctx = types.SimpleNamespace(mcp=_MCP(), session_id="s2")
    out = asyncio.run(at.execute({"server": "GitHub"}, ctx))  # fuzzy match on case
    assert "activated" in out.lower() and "2 tools" in out.lower()
    assert ma.is_active("s2", "github")
    # unknown server → guidance to mcp_find, not activated
    out2 = asyncio.run(at.execute({"server": "spotify"}, ctx))
    assert "mcp_find" in out2.lower() and not ma.is_active("s2", "spotify")


def test_deferred_servers_block_lists_only_inactive():
    import kotoba.core.loop as loop

    mcp = types.SimpleNamespace(server_tools={
        "browser": ["browser__a", "browser__b"],
        "github": ["github__create_issue", "github__search_repos", "github__list_prs"],
    })
    block = loop._deferred_servers_block(mcp, {"browser"})
    assert "AVAILABLE TOOLSETS" in block and "activate_tools" in block
    assert "github (3 tools" in block          # github listed (deferred)
    assert "browser" not in block              # browser is active → not listed
    # nothing deferred → empty
    assert loop._deferred_servers_block(mcp, {"browser", "github"}) == ""
    assert loop._deferred_servers_block(None, {"browser"}) == ""
