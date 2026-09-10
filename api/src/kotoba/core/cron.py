"""Cron worker — fires due reminders and surfaces them to the live call.

A single asyncio task polls the cronjobs table; for each due job it first reschedules a recurring job
or marks a one-shot fired, then emits a `reminder` event to every listening session.

More than one process may tick, because a tick with no listener does nothing at all. Both halves of
delivery — the queue and the stashed note — are per-process and in memory, so a process claiming a job
it cannot hand to settles it against a queue no one reads and the reminder is gone. Left due, it fires
wherever the user actually is."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from kotoba.core.events import emit_task

log = logging.getLogger("kotoba.cron")

_INTERVAL_SECS = 30

_RECUR = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}
_FMT = "%Y-%m-%d %H:%M:%S"


def _next_due(due_at: str, interval: timedelta, now: datetime) -> str:
    """Next fire time for a recurring job. Step from the job's OWN due_at by the interval (NOT from now())
    so a daily reminder keeps its time-of-day instead of drifting later each fire by the poll latency. If
    the backend was down and several periods elapsed, skip the missed ones (advance to the next FUTURE
    slot) so it fires once, not a backlog burst."""
    try:
        nxt = datetime.strptime(due_at, _FMT)
    except (ValueError, TypeError):
        nxt = now  # unparseable stored time → fall back to now-based scheduling
    nxt += interval
    while nxt <= now:
        nxt += interval
    return nxt.strftime(_FMT)


def _delivery_sessions(job_session_id: str | None) -> list[str]:
    """Where a due reminder should actually land.

    The job records the session that CREATED it, but the frontend mints a fresh uuid on every mount — so
    after any reload that id belongs to nobody, and delivering to it dropped the reminder silently while
    consuming the job. A reminder is for the USER, not for a browser tab: deliver to whatever sessions are
    listening right now. The recorded-id fallback is defensive only and does not fire in production: `_tick`
    returns before this when nothing is listening, so a reminder missed while offline stays DUE rather
    than being stashed for a tab that may never come back (the module docstring has that case)."""
    from kotoba.core.events import event_queues

    live = list(event_queues.keys())
    return live or ([job_session_id] if job_session_id else [])


async def _tick(db) -> None:
    from kotoba.core import pending_reminder

    from kotoba.core.events import event_queues

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not event_queues:
        return
    for job in await db.due_cronjobs():
        # Settle FIRST: emitting before the write means a raise here (SQLite busy under a concurrent turn)
        # leaves the job due, so the same reminder re-fires every tick and the notes pile up.
        recurring = job.get("recurring")
        try:
            if recurring in _RECUR:
                claimed = await db.reschedule_cronjob(
                    job["id"], _next_due(job["due_at"], _RECUR[recurring], now), job["due_at"]
                )
            else:
                claimed = await db.mark_cronjob_fired(job["id"])
        except Exception:
            log.warning("cron: could not settle job %s — leaving it due", job.get("id"), exc_info=True)
            continue
        if not claimed:
            # Another ticker settled this one first. Delivering anyway is how the same reminder got
            # voiced twice; the claim is the only thing that decides who speaks.
            continue
        # Stash the reminder so the voice turn (triggered by the frontend's __reminder__) injects it and
        # Kotoba voices it herself, naturally. The SSE event is just the nudge to fire that turn.
        for sid in _delivery_sessions(job.get("session_id")):
            pending_reminder.add(sid, job["message"])
            await emit_task(sid, "reminder", message=job["message"], id=job["id"])


async def cron_loop(db, interval: int = _INTERVAL_SECS) -> None:
    """Run forever (until cancelled). Never lets one bad tick kill the worker."""
    while True:
        try:
            await _tick(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("cron tick failed")
        await asyncio.sleep(interval)
