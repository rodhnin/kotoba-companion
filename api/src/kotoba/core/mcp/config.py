"""MCP server config persistence — ~/.kotoba/mcp.yaml (so installs survive restarts).

Shape:
    mcp_servers:
      filesystem:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      some-http:
        url: https://host/mcp
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml

from kotoba.core import atomic_file
from kotoba.paths import home_dir

log = logging.getLogger("kotoba.mcp")


def config_path() -> Path:
    return Path(
        os.getenv("KOTOBA_MCP_CONFIG", str(home_dir() / "mcp.yaml"))
    ).expanduser()


def load_servers() -> dict[str, dict]:
    p = config_path()
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        servers = data.get("mcp_servers") or {}
        return servers if isinstance(servers, dict) else {}
    except Exception:
        log.exception("failed to read MCP config %s", p)
        return {}


_SECRET_KEYS = ("headers", "env")


def strip_secrets(cfg: dict) -> dict:
    """A cfg fit to write to disk. `headers`/`env` are where a live token rides, and several callers hand
    over a cfg that already carries one (known.build_cfg injects the real PAT). Auth is re-derived at
    connect time from the `auth_key` pointer (hydrate_auth) or from the environment, so a persisted copy is
    never needed — and it is worse than redundant: a stored header SHADOWS revocation, so deleting the
    keystore entry left her still authenticating with the old credential."""
    if not isinstance(cfg, dict):
        return cfg
    out = {k: v for k, v in cfg.items() if k not in _SECRET_KEYS}
    # A non-secret env pair (a region, a flag) is worth keeping; anything that looks like a credential name
    # or holds a template is not.
    env = cfg.get("env")
    if isinstance(env, dict):
        safe = {k: v for k, v in env.items()
                if not re.search(r"token|secret|key|password|pat\b|auth", str(k), re.I)}
        if safe:
            out["env"] = safe
    return out


def save_server(name: str, cfg: dict) -> None:
    """Persist (or update) one server entry, preserving the rest. Secrets are stripped here rather than
    at each call site, so no future caller can leak one by forgetting.

    The read-modify-write is locked: it used to publish through a temp file whose name was fixed and
    shared, so two concurrent connects lost each other's server outright — measured at 87 of 120 — and
    one of them raised FileNotFoundError on a temp the other had already renamed away."""
    cfg = strip_secrets(cfg)
    p = config_path()
    with atomic_file.exclusive(p):
        data = {}
        if p.exists():
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        servers = data.get("mcp_servers")
        if not isinstance(servers, dict):
            servers = {}
        servers[name] = cfg
        data["mcp_servers"] = servers
        atomic_file.write_text(p, yaml.safe_dump(data, sort_keys=True, default_flow_style=False))


def hydrate_auth(cfg: dict, get_key) -> dict:
    """Inject a persisted auth token into a saved server cfg at connect time. The cfg stores only an
    `auth_key` POINTER (never the token); `get_key(auth_key)` reads the real token from the backend-only
    credential store. Returns a NEW cfg with the pointer consumed and the token applied:
      • remote (has `url`) → `Authorization: Bearer <token>` header
      • local → the env var named by `auth_env` (default the auth_key tail upper-cased)
    No `auth_key` → returned unchanged. Token missing/revoked → pointer dropped, no auth applied (the
    connect then fails cleanly → pending, never a crash)."""
    if not isinstance(cfg, dict) or "auth_key" not in cfg:
        return cfg
    out = {k: v for k, v in cfg.items() if k not in ("auth_key", "auth_env")}
    try:
        token = get_key(cfg["auth_key"])
    except Exception:
        token = None
    if not token:
        return out  # no token → drop the pointer; connect will fail → pending
    if out.get("url"):
        out["headers"] = {**(out.get("headers") or {}), "Authorization": f"Bearer {token}"}
    else:
        env_name = cfg.get("auth_env") or cfg["auth_key"].split(":")[-1].upper()
        out["env"] = {**(out.get("env") or {}), env_name: token}
    return out


def remove_server(name: str) -> None:
    p = config_path()
    if not p.exists():
        return
    with atomic_file.exclusive(p):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            return
        servers = data.get("mcp_servers") or {}
        if isinstance(servers, dict) and name in servers:
            servers.pop(name)
            data["mcp_servers"] = servers
            atomic_file.write_text(p, yaml.safe_dump(data, sort_keys=True, default_flow_style=False))
