"""One-time, in-memory secrets.

Typed into the MASKED secure box for a single use (a password we should NOT persist). The value is held
here only for the current task, NEVER written to disk/DB and NEVER returned to the model — the MCP layer
substitutes it into the tool call at the last moment, and the work-runner clears it when the work ends.
Keyed per session. Distinct from db.save_key (the SAVE-to-DB / .env credential path).
"""
from __future__ import annotations

# session_id -> {name: value}
_store: dict[str, dict[str, str]] = {}


def put(session_id: str | None, name: str, value: str) -> None:
    if not session_id or not name:
        return
    _store.setdefault(session_id, {})[name] = value


def get(session_id: str | None, name: str) -> str | None:
    if not session_id:
        return None
    return _store.get(session_id, {}).get(name)


def clear(session_id: str | None) -> None:
    if session_id:
        _store.pop(session_id, None)
