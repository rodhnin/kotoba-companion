"""Switching a plugin off must stop its CODE, not just hide its tools.

`disabled_toolsets` was read only when building schemas, while plugin discovery and exec
fire at import time, before startup applies it — a disabled plugin was still opened, exec'd
and registered, then merely filtered out of the schema list. That is a security property:
someone disabling a plugin does not want its code running in their process.

The second half is dispatch: the loop resolved a tool by name from the registry, which never
consulted the disabled set. Disabling now deregisters, so there is nothing left to reach."""
from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import posix_only
import yaml

import kotoba.tools.registry as reg

API_SRC = Path(__file__).resolve().parent.parent / "src"

_SIDE_EFFECT_TOOL = textwrap.dedent(
    '''
    from pathlib import Path
    Path(r"%(sentinel)s").write_text("this module body ran")

    SCHEMA = {"type": "function", "name": "%(name)s", "description": "Probe.",
              "parameters": {"type": "object", "properties": {}, "required": []}}
    BUILT_IN = False
    RISK = "read"
    ANNOUNCE = "x"; HEARTBEAT = []; COMPLETE = "y"; FAIL = "z"

    async def execute(args, ctx):
        return "ok"
    '''
)


@pytest.fixture
def plugins(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    root.mkdir()
    monkeypatch.setenv("KOTOBA_PLUGINS_PATH", str(root))
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    saved_registry = dict(reg._REGISTRY)
    saved_disabled = set(reg._disabled_toolsets)
    import kotoba.core.plugins as cp

    importlib.reload(cp)
    cp._PLUGINS.clear()
    try:
        yield root, cp, tmp_path / "settings.yaml"
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved_registry)
        reg._disabled_toolsets.clear()
        reg._disabled_toolsets.update(saved_disabled)
        cp._PLUGINS.clear()


def _write_plugin(root, name, sentinel):
    (root / name).mkdir()
    (root / name / "tool.py").write_text(
        _SIDE_EFFECT_TOOL % {"name": f"{name}_tool", "sentinel": sentinel}
    )


def _disable_in_settings(settings_file, *toolsets):
    settings_file.write_text(yaml.safe_dump({"disabled_toolsets": sorted(toolsets)}))


def test_a_plugin_disabled_on_disk_is_never_executed(plugins):
    """The boot order case: the switch is in settings.yaml, the registry has not been told yet."""
    root, cp, settings = plugins
    sentinel = root.parent / "it-ran.txt"
    _write_plugin(root, "sideeffect", sentinel)
    _disable_in_settings(settings, "plugin:sideeffect")

    cp.discover_plugins()

    assert not sentinel.exists(), "a disabled plugin's module body was executed anyway"
    assert "sideeffect_tool" not in reg._REGISTRY
    # Still LISTED, never LOADED: Settings builds its plugin rows from loaded_plugins(), and the row
    # is where the switch that turns it back on lives.
    assert cp.loaded_plugins()["sideeffect"] == {"source": "folder", "tools": [], "loaded": False}


def test_an_enabled_plugin_beside_it_still_loads(plugins):
    """The switch must be per plugin, not a blanket off."""
    root, cp, settings = plugins
    off = root.parent / "off-ran.txt"
    on = root.parent / "on-ran.txt"
    _write_plugin(root, "offone", off)
    _write_plugin(root, "onone", on)
    _disable_in_settings(settings, "plugin:offone")

    cp.discover_plugins()

    assert not off.exists()
    assert on.exists()
    assert "onone_tool" in reg._REGISTRY


def test_a_disabled_entry_point_plugin_is_never_loaded(plugins, monkeypatch):
    """Entry points name their toolset before load() too, so the same gate applies."""
    root, cp, settings = plugins
    _disable_in_settings(settings, "plugin:evilep")
    loaded: list[str] = []

    class FakeEP:
        def __init__(self, name):
            self.name = name

        def load(self):
            loaded.append(self.name)
            raise AssertionError(f"entry point {self.name} was loaded")

    monkeypatch.setattr(cp.metadata, "entry_points", lambda group=None: [FakeEP("evilep")])
    cp.discover_plugins()

    assert loaded == [], "a disabled entry-point plugin was imported"
    assert cp.loaded_plugins()["evilep"]["loaded"] is False


def test_disabling_a_live_plugin_takes_its_tools_out_of_reach(plugins):
    """core.loop dispatches by registry().get(name) — hiding the schema is not enough."""
    root, cp, settings = plugins
    _write_plugin(root, "livep", root.parent / "live-ran.txt")
    cp.discover_plugins()
    assert "livep_tool" in reg._REGISTRY

    reg.set_toolset_enabled("plugin:livep", False)

    assert reg.registry().get("livep_tool") is None, (
        "a switched-off plugin tool is still resolvable by name")


def test_re_enabling_a_plugin_brings_it_back(plugins):
    """Turning it back on is a request to run its code again — the toggle stays live-settable."""
    root, cp, settings = plugins
    sentinel = root.parent / "back-ran.txt"
    _write_plugin(root, "backp", sentinel)
    cp.discover_plugins()
    reg.set_toolset_enabled("plugin:backp", False)
    sentinel.unlink()

    reg.set_toolset_enabled("plugin:backp", True)

    assert sentinel.exists(), "re-enabling did not reload the plugin"
    assert "backp_tool" in reg._REGISTRY


def test_a_plugin_disabled_at_boot_can_still_be_switched_back_on(plugins):
    """The whole loop, in the order a real install hits it: off in settings.yaml, never executed,
    still listed, and the panel's toggle runs it."""
    from kotoba.core import app_settings

    root, cp, settings = plugins
    sentinel = root.parent / "boot-ran.txt"
    _write_plugin(root, "bootp", sentinel)
    _disable_in_settings(settings, "plugin:bootp")
    cp.discover_plugins()
    assert not sentinel.exists()
    assert "bootp" in cp.loaded_plugins()

    # The panel's own door (POST /api/settings/toolset), not the low-level registry one: the
    # persisted choice has to be updated before the loader is asked to trust it.
    app_settings.set_toolset_enabled("plugin:bootp", True)

    assert sentinel.exists()
    assert "bootp_tool" in reg._REGISTRY
    assert cp.loaded_plugins()["bootp"]["tools"] == ["bootp_tool"]


# The registry↔plugins cycle: discover() imports discover_plugins out of core.plugins, so a
# module-scope import the other way makes whichever one an entry point touches first find the other
# half-built. discover() only LOGS that, so the process runs on with no plugins and nothing fails.
_IMPORT_ORDER_PROBE = """
import importlib, sys
importlib.import_module({first!r})
import kotoba.tools.registry as reg
print("YES" if "probe_tool" in reg.registry() else "NO")
"""

_FIRST_IMPORTS = ["kotoba.core.plugins", "kotoba.core.settings", "kotoba.core.app_settings",
                  "kotoba.core.mcp.client", "kotoba.tools", "kotoba.server"]


@posix_only("a child environment of PATH and HOME alone")
@pytest.mark.parametrize("first", _FIRST_IMPORTS)
def test_a_plugin_loads_whichever_module_is_imported_first(tmp_path, first):
    root = tmp_path / "plugins"
    (root / "probe").mkdir(parents=True)
    (root / "probe" / "t.py").write_text(
        _SIDE_EFFECT_TOOL % {"name": "probe_tool", "sentinel": tmp_path / "ran.txt"})
    env = {
        "PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
        "KOTOBA_PLUGINS_PATH": str(root),
        "KOTOBA_SETTINGS": str(tmp_path / "settings.yaml"),
        "DATABASE_URL": "sqlite:///" + str(tmp_path / "t.db"),
    }
    out = subprocess.run([sys.executable, "-c", _IMPORT_ORDER_PROBE.format(first=first)],
                         cwd=API_SRC, capture_output=True, text=True, timeout=180, env=env)
    assert out.returncode == 0, f"probe crashed:\n{out.stderr[-2000:]}"
    assert out.stdout.strip() == "YES", (
        f"importing {first} first left the plugin undiscovered:\n{out.stderr[-2000:]}")


def test_a_core_toolset_toggle_is_untouched(plugins):
    """Only plugin:* toolsets are (de)registered; a built-in family keeps the filter-only behaviour."""
    root, cp, settings = plugins
    before = set(reg._REGISTRY)
    reg.set_toolset_enabled("web", False)
    try:
        assert set(reg._REGISTRY) == before
        assert "web" in reg.disabled_toolsets()
    finally:
        reg.set_toolset_enabled("web", True)
