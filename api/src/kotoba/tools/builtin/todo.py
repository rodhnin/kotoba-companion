"""todo — a real per-session task list visible on screen.

`steps` REPLACES the plan, and that is the destructive half: a new list_id, every mark back to pending.
She reaches for it when she means to tick, because resending what she wrote reads as harmless — marks
in the same call landed on the fresh list, and a resend after `close` reopened a finished job. So a
`steps` step-for-step identical to the plan on screen is an UPDATE, or a refusal to reopen a closed
one, and the result NAMES the field it dropped rather than absorbing it in silence.

`run_id` is the second half: a plan claimed by a background job is that job's to tick and close, and
the companion turn that launched it shares the session, gets the same note, and is refused here."""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "todo",
    "description": (
        "YOUR OWN working task list — the notebook you keep so you don't lose the thread of a job "
        "across a long session. It is not something the user asks you for and not a deliverable: you "
        "decide, by your own judgement, when a job is involved enough to be worth tracking, and you "
        "keep it up to date until the work is finished. "
        "Pass `steps` to CREATE or REPLACE the current list (old one closes as abandoned). "
        "Pass `done`, `active`, `drop`, or `close` to UPDATE the open list — send those ALONE, never "
        "with `steps`: resending the steps you already wrote is how you replace a plan by accident. "
        "Returns a short status; the list renders on screen, so never read it aloud."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "Short title of the step."},
                                "detail": {"type": "string", "description": (
                                    "Your own notes for this step — what it involves, what you already "
                                    "know, what to watch out for. Handed back to you as you work so you "
                                    "don't have to work it out again. The user does not read these."
                                )},
                            },
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    ]
                },
                "description": (
                    "Create/replace the list with these ordered steps (max 20). A step is a short title, "
                    "or {text, detail} when you want to leave yourself notes for it."
                ),
            },
            "title": {
                "type": "string",
                "description": "Short label for the plan (optional).",
            },
            "done": {
                "type": "array",
                "items": {"type": "integer"},
                "description": ("1-based step indices for steps that have ALREADY happened. Batch them: "
                                "done=[1,2,3]. Never a step you are about to do — the user reads a tick "
                                "as work delivered."),
            },
            "active": {
                "type": "integer",
                "description": "1-based index of the step currently in progress (at most one at a time).",
            },
            "drop": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "1-based step indices to drop (shown struck-through, never deleted).",
            },
            "close": {
                "type": "boolean",
                "description": ("End the list, once the work itself is over. Steps still unmarked when "
                                "you close mark it ABANDONED, not done."),
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "core"
RISK = "read"

ANNOUNCE = "Let me map this out."
HEARTBEAT: list[str] = []
COMPLETE = "Got it, plan is on screen."
FAIL = "I lost the thread of the plan there — let's try again."

_NOT_YOURS = ("That plan belongs to the background job that is still running it — it ticks and closes "
              "its own steps. Leave it alone and just tell the user where things stand.")


async def execute(args: dict, ctx) -> str:
    from kotoba.core import task_list
    from kotoba.core.events import emit_task_list

    args = args or {}
    # An empty steps list is "I sent the field but have no steps", never "replace the plan with nothing":
    # taking it literally wipes an open plan and closes it empty.
    steps = args.get("steps") or None
    title = str(args.get("title") or "")
    done_idx = [int(i) for i in (args.get("done") or [])]
    active_idx = args.get("active")
    if active_idx is not None:
        active_idx = int(active_idx)
    drop_idx = [int(i) for i in (args.get("drop") or [])]
    close = bool(args.get("close"))
    run = str(getattr(ctx, "run_id", "") or "")
    from kotoba.core.loop import note_tool_refusal

    if task_list.held_from(ctx.session_id, run):
        note_tool_refusal(ctx)
        return _NOT_YOURS

    # On an announce turn she is reporting finished work: ticking and closing are the right ending, but
    # re-deriving the request into a NEW plan restarts the job she just completed. Cover the CLOSED-list
    # case too — otherwise the strip fell through to "pass steps to start one", she complied, and the
    # strip hit again: the same exchange until the tool-call cap stopped it.
    if steps is not None and getattr(ctx, "announce_turn", False):
        steps = None
        current = task_list.get(ctx.session_id)
        if current is None or current["status"] != "open":
            note_tool_refusal(ctx)
            return "The work is done — say so. Don't open a new plan for it."

    resent = False
    if steps is not None and task_list.same_steps(ctx.session_id, list(steps)):
        steps, resent = None, True
        current = task_list.get(ctx.session_id)
        if current is None or current["status"] != "open":
            note_tool_refusal(ctx)
            return "That plan is already finished and closed — don't reopen it. Just say it's done."

    if steps is not None:
        task_list.open_list(ctx.session_id, list(steps), title, by_run=run)
    elif (current := task_list.get(ctx.session_id)) is None or current["status"] != "open":
        note_tool_refusal(ctx)
        return "No plan open yet. Pass steps=[...] to start one."

    # Marks apply whether or not this same call created the list: "plan it and tick the first" is one
    # natural request, and an if/else here silently drops the ticks while still reporting success.
    ignored: list[str] = []
    if done_idx or active_idx is not None or drop_idx or close:
        updated = task_list.update(
            ctx.session_id,
            done=done_idx or None,
            active=active_idx,
            drop=drop_idx or None,
            close=close,
            by_run=run,
        )
        ignored = list((updated or {}).get("ignored") or [])

    f = task_list.frame(ctx.session_id)
    if f is not None:
        await emit_task_list(ctx.session_id, f, run_id=getattr(ctx, "run_id", ""))

    lst = task_list.get(ctx.session_id)
    if lst is None:
        return "Plan cleared."
    if lst.get("refused_run"):
        note_tool_refusal(ctx)
        return _NOT_YOURS
    n_total = len(lst["tasks"])
    n_done = sum(1 for t in lst["tasks"] if t["status"] == "done")
    # Steps are 1-based; a 0-based or stale index would otherwise vanish into a success line.
    warn = f" Ignored (no such step, or already settled): {', '.join(ignored)}." if ignored else ""
    if resent:
        warn += " (`steps` resent and ignored — that field REPLACES a plan; send done/active/close alone.)"
    if lst["status"] != "open":
        return f"Plan {lst['status']}. {n_done}/{n_total} steps completed.{warn}"
    next_out = next((t["order"] for t in lst["tasks"] if t["status"] in ("pending", "active")), None)
    if next_out:
        return f"Plan open: {n_total} steps. {n_done} done. Next: step {next_out}.{warn}"
    return f"Plan open: {n_total} steps. All marked. Close it with todo(close=true).{warn}"
