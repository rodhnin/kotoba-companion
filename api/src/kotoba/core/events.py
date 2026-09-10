"""SSE event channel registry (Channel B) — per-session queue feeding /api/events/{session_id}.

Two kinds of frame, tagged by "type": `emotion` drives the Live2D face, `task` drives the work-mode UI.
Task kinds: working · step · artifact · files_changed · need_input · reminder · report_ready ·
subagent_spawned/step/done · work_started · work_done · task_list · peek · recalled_image — which
carries only the keepsake's ID, never its bytes, since this queue also carries approval cards.

Frames from inside an agentic run also carry `run_id`, the ORIGIN field: a client deciding whether a
frame belongs to the turn it awaits or to a detached job routes on this key, never on "is a turn
running", which is how a long job's rows landed inside a new turn's reply. emit_* no-ops with no listener."""
from __future__ import annotations

import asyncio

# session_id -> queue of event dicts (typed via the "type" field)
event_queues: dict[str, asyncio.Queue] = {}


# Sessions whose listener consumes frames but paints no fire-and-forget card. Being listened to and
# being DRAWN are two questions, and a surface that answers yes to the first and no to the second made
# her announce a text box nobody could ever see. A card that BLOCKS is not in scope: every surface here
# answers one, so `has_listener` is its question.
_cardless: set[str] = set()


def register(session_id: str, *, draws_cards: bool = True) -> asyncio.Queue:
    """Claim the session's queue. `draws_cards` is restated unconditionally, so a drawing surface
    inheriting a cardless session id is not left refusing cards it would happily paint."""
    queue: asyncio.Queue = asyncio.Queue()
    event_queues[session_id] = queue
    _cardless.discard(session_id) if draws_cards else _cardless.add(session_id)
    return queue


def unregister(session_id: str, queue: asyncio.Queue | None = None) -> None:
    """Drop the session's queue. Pass the endpoint's OWN queue so an overlapping SSE reconnect can't have
    the OLD connection's teardown delete the NEW connection's queue (which would silently kill every
    emotion/task frame until the next reconnect). Only pop if the registered queue is still `queue`."""
    if queue is not None and event_queues.get(session_id) is not queue:
        return  # a newer connection replaced us — leave its queue alone
    event_queues.pop(session_id, None)
    _cardless.discard(session_id)


def draws_cards(session_id: str | None) -> bool:
    """True when a card emitted for this session is actually painted for somebody to answer."""
    return has_listener(session_id) and session_id not in _cardless


def has_listener(session_id: str | None) -> bool:
    """True when a frame emitted for this session would actually reach someone.

    Callers that need an ANSWER must check this: emit_* is a silent no-op with no queue registered, so an
    approval card opened into nothing waits out its whole window and then reads as a denial. Anything that
    only reports progress can keep ignoring it."""
    return bool(session_id) and event_queues.get(session_id) is not None


async def _put(session_id: str | None, frame: dict) -> None:
    if not session_id:
        return
    queue = event_queues.get(session_id)
    if queue is not None:
        await queue.put(frame)


async def emit_emotion(session_id: str | None, emotion: str, run_id: str = "") -> None:
    frame: dict = {"type": "emotion", "emotion": emotion}
    if run_id:
        frame["run_id"] = run_id
    await _put(session_id, frame)


async def emit_task(session_id: str | None, kind: str, **data) -> None:
    """Push a work-mode UI event (see module docstring for the full kind list). `data` is JSON-serializable.
    A falsy `run_id` is stripped so the wire honours "key present ⇒ real id" — a comparison against an
    empty id must never match another empty id."""
    if "run_id" in data and not data["run_id"]:
        del data["run_id"]
    await _put(session_id, {"type": "task", "kind": kind, **data})


async def emit_task_list(session_id: str | None, list_frame: dict, run_id: str = "") -> None:
    """Push a complete task-list frame. list_frame is the dict returned by core.task_list.frame()."""
    await emit_task(session_id, "task_list", **list_frame, run_id=run_id)
