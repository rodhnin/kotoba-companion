"""start_work — enter WORK MODE: hand a heavy task to a background worker that is the SAME Kotoba,
shown live in the work-mode UI. Returns instantly so the voice turn stays snappy; one item per session.

`goal` is a summary and summaries lose data, so the user's OWN message rides along verbatim: a URL in
the request stays in the request. Language is one of the things a paraphrase loses — an English goal
for a Spanish question briefs the job in English — so the runner names the user's words as authority.

What comes back says the job was LAUNCHED and nothing more, because at that instant nothing more has
happened. A screen is promised ONLY where one is drawn — asked of `events.draws_cards`, because a
Discord channel paints nothing and she was telling people to watch something that never appeared.
The correction exists too: a run that touched nothing tells her in so many words to take the line back."""
from __future__ import annotations

from kotoba.core import work_runner, work_state

SCHEMA = {
    "type": "function",
    "name": "start_work",
    "description": (
        "Start doing a heavy or multi-step task in the background (opening web pages with the browser, "
        "building files, running code, deep research, OR installing a new MCP server / gaining a new "
        "capability for yourself). It returns immediately; you keep talking. Use it for ANYTHING that "
        "needs the browser, the code sandbox, several steps, or a new tool — do NOT try to do that work "
        "inline in this turn, and do NOT just web-search and read docs aloud. After calling it, tell the "
        "user you're on it. You'll be told when it finishes so you can share the result."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": (
                "The full task to carry out, in one clear sentence, WRITTEN IN THE LANGUAGE THE USER IS "
                "SPEAKING — the worker produces its files and its summary in the language it is briefed "
                "in, so an English goal for a Spanish request gives them an English report. COPY the "
                "concrete details out of the user's message into it — the URL, the file name, the exact "
                "words to use. Never refer to the request instead of quoting it ('the page the user gave "
                "me', 'the link they sent'): the worker cannot see this conversation."
            )}
        },
        "required": ["goal"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "core"   # core ∈ COMPANION_TOOLSETS → available in the voice (companion) turn
RISK = "read"      # launching is harmless; the work itself runs gated inside the work-mode loop

ANNOUNCE = ""
HEARTBEAT: list[str] = []
COMPLETE = ""
FAIL = "I couldn't get that started — let me try again."


async def execute(args: dict, ctx) -> str:
    goal = ((args or {}).get("goal") or "").strip()
    if not goal:
        return None
    sid = ctx.session_id
    if work_state.is_running(sid):
        from kotoba.core.loop import note_tool_refusal

        current = work_state.get(sid)["goal"]
        note_tool_refusal(ctx)
        return (f"You're already working in the background on: \"{current}\". Tell the user to give you a "
                f"moment to finish that first; don't start another.")
    soul_patterns = getattr(ctx, "soul_patterns", {}) or {}
    # A detached run inherits the turn's exclusion or it becomes the way around it: the job outlives
    # the turn, and whoever asked for it does not gain a tool by leaving the room.
    work_runner.start(sid, goal, ctx.db, soul_patterns, getattr(ctx, "mcp", None),
                      request=str(getattr(ctx, "user_text", "") or ""),
                      exclude_tools=getattr(ctx, "excluded_tools", None) or None)
    from kotoba.core import events

    # A surface that takes messages is not a surface that draws a running job. Told to promise a
    # screen in a Discord channel, she promised one that is never painted and then went quiet.
    where = ("they can watch the screen" if events.draws_cards(sid)
             else "you will tell them here when it is done — there is no screen to watch")
    return ("The job has been LAUNCHED in the background — that is all that has happened so far: nothing "
            f"has been opened, read, built or run yet. Say in ONE short line that you're on it and {where}, "
            "in the future tense, and claim nothing you haven't done. Then stop — "
            "you'll be told what actually happened when it ends.")
