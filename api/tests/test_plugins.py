"""Community plugin discovery — from a folder and from entry points — crash-safe and gated."""
from __future__ import annotations

import importlib
import textwrap

import pytest

import kotoba.tools.registry as reg

_TOOL = textwrap.dedent(
    '''
    SCHEMA = {"type":"function","name":"%(name)s","description":"%(desc)s",
              "parameters":{"type":"object","properties":{},"required":[]}}
    BUILT_IN = False
    RISK = "%(risk)s"
    ANNOUNCE = "x"; HEARTBEAT = []; COMPLETE = "y"; FAIL = "z"
    async def execute(args, ctx):
        return "ok"
    '''
)


@pytest.fixture
def plugins(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_PLUGINS_PATH", str(tmp_path))
    saved = dict(reg._REGISTRY)
    import kotoba.core.plugins as cp

    importlib.reload(cp)
    cp._PLUGINS.clear()
    try:
        yield tmp_path, cp
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved)
        cp._PLUGINS.clear()


def test_folder_plugin_registers_under_plugin_toolset(plugins):
    root, cp = plugins
    (root / "weather").mkdir()
    (root / "weather" / "forecast.py").write_text(_TOOL % {"name": "forecast", "desc": "Weather.", "risk": "network"})
    cp.discover_plugins()

    assert "forecast" in reg._REGISTRY
    assert reg._REGISTRY["forecast"].toolset == "plugin:weather"
    assert cp.loaded_plugins()["weather"]["tools"] == ["forecast"]


def test_single_file_plugin(plugins):
    root, cp = plugins
    (root / "dice.py").write_text(_TOOL % {"name": "dice", "desc": "Roll.", "risk": "read"})
    cp.discover_plugins()
    assert "dice" in reg._REGISTRY and reg._REGISTRY["dice"].toolset == "plugin:dice"


def test_broken_plugin_is_skipped_not_fatal(plugins):
    """One plugin that will not import must not take discovery down: it is skipped, the good one
    beside it still loads, and nothing raises."""
    root, cp = plugins
    (root / "ok").mkdir()
    (root / "ok" / "good.py").write_text(_TOOL % {"name": "good_tool", "desc": "Fine.", "risk": "read"})
    (root / "bad").mkdir()
    (root / "bad" / "boom.py").write_text("import this_module_does_not_exist_xyz\n")
    cp.discover_plugins()
    assert "good_tool" in reg._REGISTRY
    assert "bad" not in cp.loaded_plugins()


def test_plugin_tool_obeys_gating(plugins):
    """A network-risk plugin tool is offered in work mode but NOT in casual companion mode — the
    plugin toolset is not companion-safe."""
    from kotoba.tools.registry import schemas_for

    root, cp = plugins
    (root / "net").mkdir()
    (root / "net" / "fetch.py").write_text(_TOOL % {"name": "fetchit", "desc": "Fetch.", "risk": "network"})
    cp.discover_plugins()

    companion = {s.get("name") for s in schemas_for("companion")}
    work = {s.get("name") for s in schemas_for("work", {"read", "write", "exec", "network"})}
    assert "fetchit" not in companion
    assert "fetchit" in work


def test_disabling_plugin_toolset_hides_its_tools(plugins):
    from kotoba.tools.registry import schemas_for, set_toolset_enabled

    root, cp = plugins
    (root / "p1").mkdir()
    (root / "p1" / "t.py").write_text(_TOOL % {"name": "p1tool", "desc": "T.", "risk": "read"})
    cp.discover_plugins()
    assert "p1tool" in {s.get("name") for s in schemas_for("work", {"read", "write", "exec", "network"})}
    set_toolset_enabled("plugin:p1", False)
    assert "p1tool" not in {s.get("name") for s in schemas_for("work", {"read", "write", "exec", "network"})}
    set_toolset_enabled("plugin:p1", True)


def test_disabling_a_colliding_plugin_leaves_the_builtin_alone(plugins):
    """`register(allow_override=False)` refuses to displace a core tool — and its answer was discarded.

    The plugin recorded the name anyway, so switching it off deregistered the REAL `shell`: a plugin
    could delete a built-in from the toolset by being installed and then turned off."""
    root, cp = plugins
    assert reg.registry().get("shell") is not None, "no built-in shell to protect"

    (root / "collide").mkdir()
    (root / "collide" / "shell.py").write_text(
        _TOOL % {"name": "shell", "desc": "Not the real one.", "risk": "read"})
    cp.discover_plugins()

    core = reg.registry().get("shell")
    assert core is not None and core.toolset == "terminal", "the plugin displaced the core shell"

    cp.unload_plugin("collide")
    assert reg.registry().get("shell") is not None, "disabling the plugin removed the built-in shell"
