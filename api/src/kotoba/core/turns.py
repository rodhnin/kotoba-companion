"""Per-session turn arbitration — one active turn at a time, with real barge-in.

ElevenLabs posts to /v1/chat/completions on every turn-taking event, sometimes before the user has
finished speaking. Without arbitration a second post starts a SECOND agentic_loop for the same
session: the two collide on the shared workdir and the single SSE queue, and the old loop's teardown
emits `working off` while the new one is working, so the face and chip flicker.

This module tracks the in-flight turn per session so a new one can CANCEL the previous and wait for
its full teardown before starting — clean, non-interleaved ordering on the SSE channel."""
from __future__ import annotations

import asyncio

# session_id -> the asyncio.Task running that session's current produce()/agentic_loop.
_active: dict[str, asyncio.Task] = {}
# session_id -> lock serializing the "cancel old, install new" critical section (so two near-simultaneous
# posts can't both see "no active turn" and race past each other). Entries are kept for the life of the
# process ON PURPOSE: pruning one while another task already holds the object from lock() forks the lock —
# two turns inside the critical section, the exact collision this dict exists to prevent — and an entry
# costs ~180 bytes against a handful of distinct session_ids a day.
_locks: dict[str, asyncio.Lock] = {}


def lock(session_id: str) -> asyncio.Lock:
    lk = _locks.get(session_id)
    if lk is None:
        lk = _locks[session_id] = asyncio.Lock()
    return lk


async def supersede(session_id: str) -> None:
    """Barge-in: cancel any in-flight turn for this session and AWAIT its teardown to completion.

    Awaiting matters: the cancelled turn's `finally` emits `working off` and tears down its sandbox, so
    waiting gives clean ordering and no orphaned sandboxes. CancelledError is a BaseException, so the
    catch is broad.

    That broad catch swallows the AWAITED task's cancellation — the point — but never OUR OWN: it
    cannot tell them apart, and teardown here takes seconds, so a caller cancelled while parked on this
    await used to eat its own CancelledError and run on, building a turn for a request whose client was
    already gone with nothing left to cancel it. Hence the current_task().cancelling() re-raise."""
    task = _active.get(session_id)
    if task is not None and not task.done():
        import logging
        logging.getLogger("kotoba").warning(
            "supersede: cancelling in-flight turn for session %s (a new turn arrived)", session_id
        )
        task.cancel()
        try:
            await task
        except BaseException:
            me = asyncio.current_task()
            if me is not None and me.cancelling():
                raise


def register(session_id: str, task: asyncio.Task) -> None:
    _active[session_id] = task


def clear(session_id: str, task: asyncio.Task) -> None:
    """Drop `task` as the active turn — but only if it still IS the active one. A turn that was
    superseded must not clear the entry now owned by the turn that replaced it (identity check)."""
    if _active.get(session_id) is task:
        _active.pop(session_id, None)
