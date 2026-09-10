"""Self-registering tool registry.

A ToolSpec wraps a tool module with what the core needs to gate it: `toolset`, `risk`, `check`, and
an optional face profile. `schemas_for(...)` is the single source the loop reads each iteration.

SECURITY: tools are registered from explicit in-tree imports, never a built module name. `file` must
stay in the companion set, or writing a .txt falls back to a `shell` heredoc that runs anything.
`discord` is there for its own reason: its tools answer check() with "is the bot process running",
so the latency question that set settles never arises for them.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("kotoba.tools")

COMPANION_TOOLSETS = {"web", "memory", "core", "skills", "cron", "terminal", "code", "report",
                      "file", "discord"}

COMPANION_ONLY_TOOLS = {"start_work", "cancel_work"}

_CHECK_TTL = 30.0  # seconds — cache check() results (polling state is costly)


@dataclass
class ToolSpec:
    name: str
    module: object  # exposes execute(), SCHEMA, ANNOUNCE/HEARTBEAT/COMPLETE/FAIL
    schema: dict
    toolset: str = "core"
    risk: str = "read"  # read | write | exec | network
    built_in: bool = False
    check: Callable[[], bool] = field(default=lambda: True)
    expressions: dict | None = None  # optional per-state face profile; only focus and fail are emitted


_REGISTRY: dict[str, ToolSpec] = {}
_check_cache: dict[str, tuple[float, bool]] = {}
_disabled_toolsets: set[str] = set()


def set_toolset_enabled(toolset: str, enabled: bool) -> None:
    """Toggle a family. For a PLUGIN family the switch also moves third-party code in and out of the
    process: disabling deregisters its tools, and enabling loads the plugin now rather than at the next
    boot — running its module body, which is exactly what the user asked for. Built-in families keep the
    filter-only behaviour: their modules are ours and already imported, so for them this set is the whole
    of the switch — which is why a dispatch must go through `dispatchable`, never `registry().get`."""
    if enabled:
        _disabled_toolsets.discard(toolset)
    else:
        _disabled_toolsets.add(toolset)
    if not isinstance(toolset, str) or not toolset.startswith("plugin:"):
        return
    name = toolset.split(":", 1)[1]
    try:
        from kotoba.core import plugins

        if enabled:
            plugins.load_plugin(name)
        else:
            plugins.unload_plugin(name)
    except Exception:
        log.exception("could not %s plugin %r", "load" if enabled else "unload", name)


def disabled_toolsets() -> set[str]:
    return set(_disabled_toolsets)


def all_toolsets() -> list[str]:
    return sorted({spec.toolset for spec in _REGISTRY.values()})


def register(spec: ToolSpec, *, allow_override: bool = True) -> None:
    """Add or replace a tool. `allow_override=False` refuses to displace an existing one.

    Plugins and runtime MCP registration both land here, and plugins load AFTER the builtins — so a folder
    plugin declaring SCHEMA["name"] = "shell" replaced the core spec outright, module, `risk` and `check`
    included. Re-declaring `risk="read"` on a shell replacement skips the risk filter entirely, which is
    the opposite of the "a plugin can't bypass security, it just adds tools" promise."""
    existing = _REGISTRY.get(spec.name)
    if existing is not None:
        if not allow_override:
            log.warning("refusing to override existing tool %r", spec.name)
            return
        log.warning("tool %r already registered — overriding (last wins)", spec.name)
        # A replaced spec must not inherit the previous one's cached availability.
        _check_cache.pop(spec.name, None)
    _REGISTRY[spec.name] = spec


def deregister(name: str) -> None:
    _REGISTRY.pop(name, None)
    _check_cache.pop(name, None)


def registry() -> dict[str, ToolSpec]:
    """Live registry (name -> ToolSpec), including MCP and plugin tools registered at runtime."""
    return _REGISTRY


def dispatchable(name: str) -> ToolSpec | None:
    """The spec a tool CALL may reach — None when the name is unknown OR its family is switched off.

    `registry().get(name)` is the wrong door for a dispatch, and core.loop used it: `schemas_for` stops
    OFFERING a disabled family, but the model keeps every name it already had from earlier in the turn,
    and that lookup reads nothing about `_disabled_toolsets`. A plugin family had no hole (disabling one
    deregisters its tools, which is also how its code leaves the process); a built-in family keeps the
    filter-only behaviour on purpose, so the door is where it has to be closed.

    Deliberately NOT routed through `_passes_check`: that memoises for _CHECK_TTL, and a switch the user
    just flipped in Settings must not go on lying for thirty seconds in either direction."""
    spec = _REGISTRY.get(name)
    return None if spec is None or spec.toolset in _disabled_toolsets else spec


def function_tool_names() -> set[str]:
    """Names the model invokes as function calls (everything except OpenAI built-ins like web_search)."""
    return {name for name, spec in _REGISTRY.items() if not spec.built_in}


def modules_by_name() -> dict[str, object]:
    return {name: spec.module for name, spec in _REGISTRY.items()}


def _spec_from_module(mod, fallback_name: str) -> ToolSpec | None:
    schema = getattr(mod, "SCHEMA", None)
    if not isinstance(schema, dict):
        return None  # not a tool module — skip silently
    # built-in schemas like {"type":"web_search"} carry no name
    name = schema.get("name") or fallback_name
    return ToolSpec(
        name=name,
        module=mod,
        schema=schema,
        toolset=getattr(mod, "TOOLSET", "core"),
        risk=getattr(mod, "RISK", "read"),
        built_in=getattr(mod, "BUILT_IN", False),
        check=getattr(mod, "check", lambda: True),
        expressions=getattr(mod, "EXPRESSIONS", None),
    )


def _register_modules(modules) -> None:
    for mod in modules:
        fallback = getattr(mod, "__name__", "tool").rsplit(".", 1)[-1]
        spec = _spec_from_module(mod, fallback)
        if spec is not None:
            register(spec)


def discover() -> None:
    """Register all in-tree tools. Idempotent.

    Built-in (companion-safe) tools and action tools are imported explicitly here — by module object,
    never via a dynamically-constructed name — so no untrusted string can load arbitrary code. New
    action tools are added to `tools.action.ACTION_TOOLS`; they register automatically.
    """
    from kotoba.tools.builtin import (
        activate_tools,
        ask_user,
        cancel_work,
        clarify,
        memory_recall,
        memory_write,
        open_link,
        recall_image,
        remember_image,
        session_search,
        skill_list,
        skill_view,
        start_work,
        todo,
        view_capture,
        web_extract,
        web_search,
    )

    _register_modules(
        [web_search, web_extract, memory_write, memory_recall, session_search, todo, clarify,
         skill_list, skill_view, ask_user, start_work, cancel_work, view_capture,
         remember_image, recall_image, activate_tools, open_link]
    )

    # Fail LOUD, unlike plugins below: these are core. Swallowed, a circular import (whose likelihood
    # depends on which module the entry point touches first) left her with half her tools and a log line.
    try:
        from kotoba.tools import action

        _register_modules(list(getattr(action, "ACTION_TOOLS", [])))
    except Exception as e:
        log.exception("action-tool discovery failed")
        raise RuntimeError(
            f"core action tools failed to load ({e}) — refusing to start with a crippled toolset"
        ) from e

    try:
        from kotoba.core.plugins import discover_plugins

        discover_plugins()
    except Exception:
        log.exception("plugin discovery failed")


def _passes_check(spec: ToolSpec) -> bool:
    """Is this tool currently usable? Cached for _CHECK_TTL, because a check() may do real IO (the docker
    backend probes the daemon) and this runs from schemas_for on EVERY loop iteration, inside the event
    loop — so a check() must stay cheap and bounded (docker's does a socket pre-check first). A probe that
    RAISES keeps the last known answer instead of flapping the tool out of the schema."""
    now = time.monotonic()
    cached = _check_cache.get(spec.name)
    if cached is not None and (now - cached[0]) < _CHECK_TTL:
        return cached[1]
    try:
        ok = bool(spec.check())
    except Exception:
        log.warning("check() for tool %r raised — treating as unavailable", spec.name, exc_info=True)
        ok = cached[1] if cached is not None else False
    _check_cache[spec.name] = (now, ok)
    return ok


def schemas_for(
    mode: str = "companion",
    allow_risk: set[str] | None = None,
    toolset_filter: str | None = None,
    mcp_active: set[str] | None = None,
    exclude_tools: frozenset[str] | None = None,
) -> list[dict]:
    """The tool schemas to offer the model, gated by availability + mode + risk + optional toolset.

    - `check()` (30s cached) decides if a tool is available right now.
    - companion mode offers only COMPANION_TOOLSETS.
    - `allow_risk`, when given, is an extra filter on RISK; None applies no risk filter, since the
      companion gate and the sandbox/approval layer are the real safeguards.
    - `toolset_filter` (subagents) restricts to one toolset but always keeps built-ins.
    - `mcp_active`: a connected server's tools are offered only if it is in this set, so 44 unused
      github tools do not bloat the toolset. None means no MCP filtering.
    - `exclude_tools`: names to drop unconditionally."""
    out: list[dict] = []
    for spec in _REGISTRY.values():
        if spec.toolset in _disabled_toolsets:
            continue
        if not _passes_check(spec):
            continue
        if allow_risk is not None and spec.risk not in allow_risk:
            continue
        if mode == "companion" and spec.toolset not in COMPANION_TOOLSETS:
            continue
        if mode != "companion" and spec.name in COMPANION_ONLY_TOOLS:
            continue
        if mcp_active is not None and isinstance(spec.toolset, str) and spec.toolset.startswith("mcp:"):
            if spec.toolset.split(":", 1)[1] not in mcp_active:
                continue
        if toolset_filter is not None and not spec.built_in:
            # Also match the "mcp:<filter>" form — else delegate(toolset='browser') gets ZERO browsing tools.
            if spec.toolset != toolset_filter and spec.toolset != f"mcp:{toolset_filter}":
                continue
        if exclude_tools and spec.name in exclude_tools:
            continue
        out.append(spec.schema)
    return out
