"""Per-session set of ACTIVE MCP servers — progressive tool disclosure.

A connected MCP server (github alone is 44 tools) dumps ALL of them into the work toolset, and tool-
selection accuracy falls off a cliff past roughly 30-50 offered at once — a published finding this
project has reproduced. So non-core servers are DEFERRED: their tools are not offered until the model
activates the server, at which point schemas_for includes them next iteration. Browser stays
always-active, being the core work capability; deferring it would add a round trip to every task.

Activation is per SESSION and in memory, like work_state."""
from __future__ import annotations

import os

# Servers whose tools are ALWAYS offered in work mode (no activate_tools needed). Override with
# KOTOBA_MCP_ALWAYS_ACTIVE (comma-separated server names).
def always_active() -> set[str]:
    raw = os.getenv("KOTOBA_MCP_ALWAYS_ACTIVE", "browser")
    return {s.strip() for s in raw.split(",") if s.strip()}


_active: dict[str, set[str]] = {}  # session_id -> servers the model activated this session


def active(session_id: str | None) -> set[str]:
    """The MCP servers whose tools should be OFFERED for this session = always-active ∪ activated."""
    return always_active() | _active.get(session_id or "", set())


def activate(session_id: str | None, server: str) -> None:
    if not server:
        return
    _active.setdefault(session_id or "", set()).add(server)


def is_active(session_id: str | None, server: str) -> bool:
    return server in active(session_id)


def clear(session_id: str | None) -> None:
    _active.pop(session_id or "", None)
