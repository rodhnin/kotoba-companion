"""Tiny per-session runtime state shared across requests (in-memory).

Currently: mute. When the user mutes the mic, ElevenLabs may still trigger silence/turn-taking turns
against our /v1/chat/completions — which made Kotoba keep "checking in" ("something bothering you?").
The frontend tells us the mute state so we can stay silent instead of generating those prompts.
"""
from __future__ import annotations

_muted: set[str] = set()
# One-shot "the next turn is a TYPED message — answer it even while muted". The frontend sets this right
# before sending a typed message / attachment, because a muted session normally returns skip_turn for
# ElevenLabs' silence turns; without this flag a typed message while muted would be skipped too (the user
# muted the MIC, but explicitly chose to type — she must still reply).
_text_turn: set[str] = set()


def set_muted(session_id: str | None, muted: bool) -> None:
    if not session_id:
        return
    if muted:
        _muted.add(session_id)
    else:
        _muted.discard(session_id)


def is_muted(session_id: str | None) -> bool:
    return bool(session_id) and session_id in _muted


_mcp_announced: dict[str, set] = {}


def mcp_announced(session_id: str | None, server: str) -> bool:
    """True if this MCP server's install was already announced this session (don't re-narrate it)."""
    return bool(session_id) and server in _mcp_announced.get(session_id, set())


def mark_mcp_announced(session_id: str | None, server: str) -> None:
    if session_id and server:
        _mcp_announced.setdefault(session_id, set()).add(server)


def mark_text_turn(session_id: str | None) -> None:
    """Frontend signal: the imminent turn is a typed message — don't skip it even if muted."""
    if session_id:
        _text_turn.add(session_id)


def consume_text_turn(session_id: str | None) -> bool:
    """True (and clears the flag) if the current turn was flagged as a typed message. Always called once
    per turn so the flag never lingers into a later silence turn."""
    if session_id and session_id in _text_turn:
        _text_turn.discard(session_id)
        return True
    return False
