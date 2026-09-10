"""cancel_work — stop the background work in progress (user said "stop / cancel that"). Cancels the
work-runner task and clears the status. No-op (friendly) when nothing is running.

Logged on every call, because this is also `/stop`'s engine (the terminal calls execute() directly,
so no `toolcall` line covers it): a stopped job whose only trace was the runner's silent exit is how
a live session's vanished work went unexplained."""
from __future__ import annotations

import logging

from kotoba.core import deferred_exec, work_state

log = logging.getLogger("kotoba")

SCHEMA = {
    "type": "function",
    "name": "cancel_work",
    "description": (
        "Stop the background work that's currently in progress, when the user asks you to stop or cancel "
        "it. Does nothing if there's no work running."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}
BUILT_IN = False
TOOLSET = "core"
RISK = "read"

ANNOUNCE = ""
HEARTBEAT: list[str] = []
COMPLETE = ""
FAIL = "I couldn't stop it cleanly — give me a second."


async def execute(args: dict, ctx) -> str:
    sid = ctx.session_id
    task = work_state.pop_task(sid)
    running = work_state.is_running(sid)
    log.info("cancel_work: stopping background work for session %s (task=%s running=%s)",
             sid, task is not None, running)
    if task is not None and not task.done():
        task.cancel()
    work_state.clear(sid)
    # Also stop any DEFERRED approval/exec task (an approved-but-deferred shell/code command runs detached,
    # outside work_state — without this, "stop" left it running).
    deferred = deferred_exec.cancel(sid)
    if running or task is not None or deferred:
        return "Stopped the background work. Tell the user you've dropped it."
    return "There's nothing running in the background right now — tell the user that, gently."
