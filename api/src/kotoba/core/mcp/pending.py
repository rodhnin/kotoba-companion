"""Pending-MCP store — servers Kotoba found but couldn't connect because they need auth.

Recorded so the Settings panel can list them ("Needs connection") and the user can finish the connection
later, and so the boot reconnect does NOT keep retrying a server that needs a credential. Persisted to
~/.kotoba/pending_mcp.yaml (override KOTOBA_PENDING_MCP). NEVER stores a secret — only the cfg WITHOUT
credentials plus the reason/kind/description for display.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

from kotoba.core import atomic_file
from kotoba.paths import home_dir

log = logging.getLogger("kotoba.mcp")


def _path() -> Path:
    return Path(
        os.getenv("KOTOBA_PENDING_MCP", str(home_dir() / "pending_mcp.yaml"))
    ).expanduser()


def _load() -> dict[str, dict]:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        items = data.get("pending") or {}
        return items if isinstance(items, dict) else {}
    except Exception:
        log.exception("failed to read pending MCP file %s", p)
        return {}


def _save(items: dict[str, dict]) -> None:
    atomic_file.write_text(_path(), yaml.safe_dump({"pending": items}, sort_keys=True, default_flow_style=False))


def record(name: str, cfg: dict, reason: str, kind: str, description: str = "") -> None:
    """Add/replace a pending server. The cfg is stripped HERE rather than trusted from the caller: the
    docstring used to say "MUST be secret-free" and the callers handed over `known.build_cfg`'s output,
    which carries the real PAT in `headers`."""
    from kotoba.core.mcp.config import strip_secrets

    with atomic_file.exclusive(_path()):  # load+save is one critical section, or a concurrent entry is lost
        items = _load()
        items[name] = {
            "cfg": strip_secrets(cfg or {}),
            "reason": str(reason or ""),
            "kind": kind if kind in ("token", "oauth") else "token",
            "description": str(description or ""),
        }
        _save(items)


def list_pending() -> list[dict]:
    """[{name, reason, kind, description}] — display-safe rows for Settings (no cfg/secret)."""
    return [
        {"name": n, "reason": v.get("reason", ""), "kind": v.get("kind", "token"),
         "description": v.get("description", "")}
        for n, v in sorted(_load().items())
    ]


def get(name: str) -> dict | None:
    """Full record incl. cfg (for connect-from-Settings), or None."""
    v = _load().get(name)
    if v is None:
        return None
    return {"name": name, **v}


def clear(name: str) -> None:
    with atomic_file.exclusive(_path()):
        items = _load()
        if name in items:
            items.pop(name)
            _save(items)
