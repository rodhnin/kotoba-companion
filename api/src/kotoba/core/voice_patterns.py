"""Build SOUL_PATTERNS — the single runtime dict the loop + heartbeat read.

Defaults come from each tool module's ANNOUNCE/HEARTBEAT/COMPLETE/FAIL; the SOUL.md
'## Tool voice patterns' section overrides them (SOUL.md wins). Partial overrides allowed.

Exports build_soul_patterns ONLY — do NOT build at import time (soul_config comes from an
async DB call, impossible at module load). core.engine.start() builds it once per process and
hands it to every entry point (the server's lifespan, the CLI, doctor) on Engine.soul_patterns.
"""
from __future__ import annotations

import json

from kotoba.tools import TOOL_REGISTRY


def parse_tool_patterns(markdown: str) -> dict[str, dict]:
    """Parse the raw '## Tool voice patterns' markdown into {tool: {before, heartbeat[], after, fail}}.
    Only keys present are returned (partial overrides)."""
    patterns: dict[str, dict] = {}
    current: str | None = None

    for raw in (markdown or "").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indented = raw[:1].isspace()
        if not indented and stripped.endswith(":"):
            current = stripped[:-1].strip()
            patterns[current] = {}
            continue

        if indented and current is not None and ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if key not in {"before", "heartbeat", "after", "fail"}:
                continue
            patterns[current][key] = _parse_value(key, value)

    # Drop empty tool blocks (e.g. a header with only comments under it).
    return {k: v for k, v in patterns.items() if v}


def _parse_value(key: str, value: str):
    if key == "heartbeat":
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            pass
        return []
    try:
        return str(json.loads(value))  # unquote "..." cleanly
    except json.JSONDecodeError:
        return value.strip('"').strip("'")


def build_soul_patterns(soul_config: dict) -> dict[str, dict]:
    patterns: dict[str, dict] = {}
    for name, mod in TOOL_REGISTRY.items():
        # getattr with defaults: voice patterns are OPTIONAL — a minimal valid tool or plugin is SCHEMA +
        # execute, with no ANNOUNCE/HEARTBEAT/COMPLETE/FAIL, and unguarded access crashed boot.
        patterns[name] = {
            "before": getattr(mod, "ANNOUNCE", ""),
            "heartbeat": list(getattr(mod, "HEARTBEAT", []) or []),
            "after": getattr(mod, "COMPLETE", ""),
            "fail": getattr(mod, "FAIL", ""),
        }
    overrides = parse_tool_patterns(soul_config.get("tool_patterns", ""))
    for name, override in overrides.items():
        patterns.setdefault(name, {"before": "", "heartbeat": [], "after": "", "fail": ""})
        patterns[name].update(override)
    return patterns
