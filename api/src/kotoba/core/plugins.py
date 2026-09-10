"""Community plugins — extend Kotoba with third-party tools.

Two install paths: a package dropped in ~/.kotoba/plugins/<name>/, or a distribution declaring the
`kotoba.tools` entry point. A broken plugin must NEVER tumble the server — every load is isolated —
and plugin tools take the SAME registry gating as everything else, so a plugin bypasses no security.

Registry imports here are function-local: discover() pulls `discover_plugins` from THIS module, so a
module-scope import back is a cycle whose ImportError discover() swallows, leaving no plugins at all.
A DISABLED plugin is never OPENED — decided before exec_module(), from the settings FILE, since
discover() runs long before engine.start() copies those toggles into the registry."""
from __future__ import annotations

import importlib.metadata as metadata
import importlib.util
import logging
import os
from pathlib import Path
from kotoba.paths import home_dir

log = logging.getLogger("kotoba.plugins")

# name -> {"source": "folder"|"entrypoint", "tools": [tool names], "loaded": bool}
_PLUGINS: dict[str, dict] = {}


def plugins_dir() -> Path:
    return Path(
        os.getenv("KOTOBA_PLUGINS_PATH", str(home_dir() / "plugins"))
    ).expanduser()


def loaded_plugins() -> dict[str, dict]:
    return dict(_PLUGINS)


def toolset_for(plugin_name: str) -> str:
    return f"plugin:{plugin_name}"


def _remember_disabled(plugin_name: str, source: str) -> None:
    """An installed-but-switched-off plugin still needs its row in Settings — that row carries the
    only switch that turns it back on (the panel builds the list from loaded_plugins()). Recording
    the NAME, which is a directory entry or an entry-point key, is not running the code."""
    _PLUGINS.setdefault(plugin_name, {"source": source, "tools": [], "loaded": False})


def _disabled_toolsets() -> set[str]:
    """Everything switched off, from the file AND from the live registry — the two disagree by
    design early in boot (the file is written first, the registry is told in engine.start), and
    later a panel toggle updates both. A settings file we cannot read must not silently enable a
    plugin the user turned off, so an unreadable one falls back to the registry rather than to
    'nothing is disabled'."""
    from kotoba.tools import registry

    off = set(registry.disabled_toolsets())
    try:
        from kotoba.core import app_settings

        off |= app_settings.saved_disabled_toolsets()
    except Exception:
        log.warning("could not read saved toolset toggles — plugin gating falls back to the registry",
                    exc_info=True)
    return off


def _register_plugin_module(mod, plugin_name: str, fallback: str) -> str | None:
    """Register a module's tool under toolset 'plugin:<name>' so it's visible + toggleable as a plugin.
    Returns the registered tool name, or None if the module isn't a tool."""
    from kotoba.tools.registry import ToolSpec, _spec_from_module, register

    spec = _spec_from_module(mod, fallback)
    if spec is None:
        return None
    # ALWAYS namespace the toolset to the source plugin. Letting a module keep its own `plugin:*` toolset
    # let it choose a name the Settings switch doesn't govern, so toggling the plugin off toggled nothing.
    spec = ToolSpec(
        name=spec.name, module=spec.module, schema=spec.schema,
        toolset=toolset_for(plugin_name), risk=spec.risk, built_in=spec.built_in,
        check=spec.check, expressions=spec.expressions,
    )
    # allow_override=False: a plugin ADDS tools, it does not replace core ones. Overriding `shell` with a
    # spec declaring risk="read" would skip the risk filter altogether. The refusal has to be READ, not
    # only issued: the name was recorded either way, so switching the plugin off deregistered the
    # built-in it had failed to displace.
    from kotoba.tools.registry import registry

    if registry().get(spec.name) is not None:
        log.warning("plugin %r declares tool %r, which already exists — not registered",
                    plugin_name, spec.name)
        return None
    register(spec, allow_override=False)
    return spec.name


def _load_folder_plugins(only: str | None = None) -> None:
    root = plugins_dir()
    if not root.exists():
        return
    disabled = _disabled_toolsets()
    for entry in sorted(root.iterdir()):
        # A plugin is either a package dir (with .py files) or a single .py file.
        py_files: list[Path] = []
        if entry.is_dir() and not entry.name.startswith((".", "_")):
            py_files = [p for p in sorted(entry.glob("*.py")) if not p.name.startswith("_")]
            name = entry.name
        elif entry.suffix == ".py" and not entry.name.startswith((".", "_")):
            py_files = [entry]
            name = entry.stem
        else:
            continue
        if only is not None and name != only:
            continue
        if toolset_for(name) in disabled:
            _remember_disabled(name, "folder")
            continue

        tools: list[str] = []
        for py in py_files:
            try:
                mod_name = f"kotoba_plugin_{name}_{py.stem}"
                spec = importlib.util.spec_from_file_location(mod_name, py)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # may run plugin code — isolated below
                tn = _register_plugin_module(mod, name, py.stem)
                if tn:
                    tools.append(tn)
            except Exception:
                log.exception("plugin %r file %s failed to load — skipping", name, py.name)
        if tools:
            _PLUGINS[name] = {"source": "folder", "tools": tools, "loaded": True}
            log.info("loaded folder plugin %r: %s", name, tools)


def _load_entrypoint_plugins(only: str | None = None) -> None:
    try:
        eps = metadata.entry_points(group="kotoba.tools")
    except Exception:
        return
    disabled = _disabled_toolsets()
    for ep in eps:
        if only is not None and ep.name != only:
            continue
        if toolset_for(ep.name) in disabled:
            _remember_disabled(ep.name, "entrypoint")
            continue
        try:
            mod = ep.load()  # the entry point target (a module exposing a tool SCHEMA)
            tn = _register_plugin_module(mod, ep.name, ep.name)
            if tn:
                info = _PLUGINS.setdefault(ep.name, {"source": "entrypoint", "tools": []})
                info["tools"].append(tn)
                info["loaded"] = True
                log.info("loaded entry-point plugin %r: %s", ep.name, tn)
        except Exception:
            log.exception("entry-point plugin %r failed to load — skipping", ep.name)


def discover_plugins(only: str | None = None) -> None:
    """Load community plugins (folder + entry points), skipping every disabled one. Idempotent-ish;
    safe to call once at boot. Never raises: a bad plugin is logged and skipped so the server always
    comes up. `only` loads a single plugin by name — the re-enable path, where the user has just
    asked for exactly that plugin's code to run again."""
    try:
        _load_folder_plugins(only)
    except Exception:
        log.exception("folder-plugin discovery failed")
    try:
        _load_entrypoint_plugins(only)
    except Exception:
        log.exception("entry-point-plugin discovery failed")


def load_plugin(plugin_name: str) -> None:
    """Load one plugin now, unless it is already loaded. The re-enable door."""
    if _PLUGINS.get(plugin_name, {}).get("loaded"):
        return
    discover_plugins(only=plugin_name)


def unload_plugin(plugin_name: str) -> None:
    """Take a switched-off plugin's tools out of the registry entirely.

    Not cosmetic: a name the model still had from earlier in the conversation reached a plugin the
    user had just turned off. The module itself stays imported — nothing can un-run code — which is
    why the load-time gate above is the real protection and this is the second lock."""
    from kotoba.tools import registry

    info = _PLUGINS.get(plugin_name)
    if info is None:
        return
    for tool in info.get("tools", []):
        registry.deregister(tool)
    info["tools"], info["loaded"] = [], False
