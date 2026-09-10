"""Pending proactive reminders.

When the cron worker fires a due reminder it stashes the message here, keyed by session. The frontend, on
the `reminder` SSE event, nudges a voice turn (the hidden __reminder__ trigger); load_context then injects
the stashed reminder so Kotoba brings it up HERSELF, naturally and in her own words — not as a robotic
"Reminder:" card. In-memory like work_state / session_state (a missed reminder while offline is dropped,
not replayed — acceptable for a proactive nudge).
"""
from __future__ import annotations

_pending: dict[str, list[str]] = {}  # session_id -> queued reminder messages (FIFO)


def add(session_id: str | None, message: str) -> None:
    if not session_id or not message:
        return
    _pending.setdefault(session_id, []).append(message)


def prompt_note(session_id: str | None, consume: bool = True) -> str:
    """The developer note to inject so the model voices the due reminder(s). Clears them by default (the
    turn it's injected into is the one that announces — same one-shot semantics as work_state.announced)."""
    msgs = _pending.get(session_id or "") or []
    if not msgs:
        return ""
    if consume:
        _pending.pop(session_id or "", None)
    joined = "; ".join(m for m in msgs if m)
    return (
        f'[DUE REMINDER] A reminder you set for the user just came due: "{joined}". Bring it up with them '
        f"NOW — naturally and warmly, in your own words, like a friend who remembered. Don't read it like a "
        f"system notification, and don't mention reminders/cron as machinery; just remind them kindly."
    )


def has_pending(session_id: str | None) -> bool:
    """Peek (non-consuming): is there a due reminder waiting to be voiced for this session? Used by the
    muted-turn guard to let ONE silence turn through so Kotoba can announce it (mirrors work_state)."""
    return bool(_pending.get(session_id or ""))


def clear(session_id: str | None) -> None:
    _pending.pop(session_id or "", None)


def discard(message: str) -> None:
    """Drop one specific stashed message from EVERY session's queue. For cronjob's skip/remove: cron
    fans a due reminder out to all live sessions, and a user who says the THING is done has
    acknowledged it everywhere — without this, "ya lo hice" was followed by her reminding it anyway.
    Other pending reminders are untouched."""
    for sid in list(_pending):
        kept = [m for m in _pending[sid] if m != message]
        if kept:
            _pending[sid] = kept
        else:
            _pending.pop(sid, None)
