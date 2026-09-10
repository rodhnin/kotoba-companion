"""Which transport owns this turn's clock — and what that clock costs.

`voice_mode` cannot answer that: it is a DECLARATION OF INTENT in settings.yaml, and `/v1` is guarded
by its bearer alone, so an ElevenLabs agent turn still arrives there with `voice_mode=local` set. Any
`if voice_mode == "local"` that RELAXES a limit relaxes it for the caller it was written for.

So the fact travels PER TURN. `/v1`'s producer is the only code that knows an agent is on the other
end and marks its own task; a ContextVar carries it, and create_task copies the context. Named for the
CONSTRAINT, not the setting — the CLI is neither voice mode and must read False. The EL duration and
both work budgets live here, once, because they were written down with three different values."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

# ElevenLabs' hard cap on one agent conversation. sendUserActivity does NOT reset it, so the frontend's
# work keepalive cannot extend it either. Raised by ElevenLabs from 600s; 600 is dead.
EL_MAX_DURATION_SECONDS = 1800

# Compute-seconds one background work item gets. Merit-based: nothing external is holding a clock, so
# the real bound is the iteration / tool-call / failure caps, and an hour is what a long build wants.
WORK_TIMEOUT_SECONDS = 3600.0
# Bound to an ElevenLabs call instead: under EL_MAX_DURATION_SECONDS with headroom for the announce turn.
WORK_TIMEOUT_EL_BOUND_SECONDS = 1500.0

_EL_CALL_BOUND: ContextVar[bool] = ContextVar("kotoba_el_call_bound", default=False)


def el_call_bound() -> bool:
    """Is an ElevenLabs agent call holding this turn's clock open right now?"""
    return _EL_CALL_BOUND.get()


@contextmanager
def el_call_turn() -> Iterator[None]:
    """Mark everything run inside as served to an ElevenLabs agent. Only `/v1` may use this."""
    token = _EL_CALL_BOUND.set(True)
    try:
        yield
    finally:
        _EL_CALL_BOUND.reset(token)


@contextmanager
def detached_from_el_call() -> Iterator[None]:
    """Drop the mark for code that OUTLIVES the turn it was created in. Only the work runner may use it.

    `asyncio.create_task` copies the current context, so a background job launched inside an ElevenLabs
    turn inherits `True` and keeps reading it for the next half hour — long after that turn returned
    its "I'll get on it" line. The mark answers one question, "is an agent holding THIS code's clock
    open", and for a detached job the honest answer is no.

    What the job's transport genuinely still decides — its compute ceiling — does not come through the
    ContextVar at all: `start` captures it at creation and passes it down, because a detached reader
    cannot be trusted to re-derive it."""
    token = _EL_CALL_BOUND.set(False)
    try:
        yield
    finally:
        _EL_CALL_BOUND.reset(token)
