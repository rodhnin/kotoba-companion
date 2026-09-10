"""delegate — hand a focused subtask to a subagent and get its summary back.

The subagent is another run of the SAME agentic loop with an isolated conversation (just the goal, not
the parent's voice history), a restricted toolset, the SHARED workdir/sandbox, and no voice. Only the
SUMMARY returns, keeping the parent's context clean. Every frame carries the PARENT run's `run_id`, so
a helper the detached job spawned never draws inside an interactive turn's reply.

The helper's loop is sized with the main loop's iterations = tool_calls + 1 arithmetic: passing the
iteration budget alone left the tool-withdrawal gate evaluating against work mode's cap of 40 calls,
unreachable in twelve iterations, so a helper ran out mid-chain with empty text, reported as a failure."""
from __future__ import annotations

import asyncio
import logging
import uuid
from kotoba.soul import prompt as _prompt

log = logging.getLogger("kotoba")

MAX_SPAWN_DEPTH = 1  # a context this deep may not spawn: the parent (0) may, a helper (1) may not
SUB_MAX_ITERATIONS = 12

SCHEMA = {
    "type": "function",
    "name": "delegate",
    "description": (
        "Hand a focused subtask to a specialist helper and get back a concise summary. Use it to split "
        "heavy or parallelizable work (research, writing files, running code, browsing) so you stay "
        "responsive. The helper works on its own and reports back."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "The focused subtask for the helper."},
            "toolset": {
                "type": "string",
                "enum": ["research", "web", "file", "code", "browser"],
                "description": (
                    "Which tools the helper may use. Default to 'research'/'web' (fast web_search) for "
                    "looking things up on the internet — it's light and quick. Use 'browser' ONLY when the "
                    "subtask MUST interact with a live page (log in, fill/submit a form, click through a "
                    "flow, or screenshot a specific site) — it's much heavier (token-hungry snapshots) and "
                    "slower. Use 'file' for reading/writing files, 'code' for running code."
                ),
            },
            "context": {"type": "string", "description": "Any context the helper needs to do the goal."},
        },
        "required": ["goal"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "subagent"
RISK = "exec"
# A subagent runs a FULL agentic loop (TPM pacing + 429 backoff per iteration) — the default 30s killed it.
TIMEOUT = 420

ANNOUNCE = "Let me get a little help on this~"
HEARTBEAT = ["My helper's on it...", "Coordinating...", "Almost got it back..."]
COMPLETE = "Okay, my helper came back with this:"
FAIL = "That didn't pan out — let me just do it myself."
EXPRESSIONS = {"focus": "determined", "done": "happy", "fail": "thinking"}

_TOOLSET_MAP = {"research": "web", "web": "web", "file": "file", "code": "code", "browser": "browser"}

_SUB_SYSTEM = (
    "You are a focused helper working on ONE subtask for the main assistant. Use your available tools "
    "to accomplish it. Stay on task. When you're done, reply with a SHORT, concrete summary of what you "
    "found or did (a few sentences max) — that summary is all that gets passed back."
)


async def execute(args: dict, ctx) -> str:
    from kotoba.core import citations
    from kotoba.core.events import emit_task
    from kotoba.core.llm import get_client
    from kotoba.core.loop import _run_iterations

    args = args or {}
    goal = (args.get("goal") or "").strip()
    if not goal:
        return None
    if ctx.spawn_depth >= MAX_SPAWN_DEPTH:
        from kotoba.core.loop import note_tool_refusal

        note_tool_refusal(ctx)
        return "I can't nest helpers any deeper — I'll handle this part myself."
    client = get_client()
    if client is None:
        return None

    toolset = (args.get("toolset") or "research").strip()
    context = (args.get("context") or "").strip()
    sub_id = uuid.uuid4().hex[:8]

    rid = getattr(ctx, "run_id", "")
    await emit_task(ctx.session_id, "subagent_spawned", id=sub_id, goal=goal,
                    toolset=toolset if toolset in _TOOLSET_MAP else "web", run_id=rid)
    child = ctx.child(sub_id)
    child.run_id = rid
    input_items = [
        {"role": "developer", "content": _SUB_SYSTEM + _prompt.platform_note()},
        {"role": "user", "content": f"{goal}\n\n{context}".strip()},
    ]
    silent: asyncio.Queue = asyncio.Queue()  # the helper doesn't speak; its text is discarded
    summary = None
    error: str | None = None
    try:
        # Per-role model: a code/research helper runs on its tuned model, falling back work→companion.
        _role = {"code": "code", "research": "research", "web": "research"}.get(toolset, "work")
        summary = await _run_iterations(
            client, child, input_items, silent, {},
            max_iterations=SUB_MAX_ITERATIONS, mode="work",
            allow_risk={"read", "write", "exec", "network"},
            toolset_filter=_TOOLSET_MAP.get(toolset, "web"),
            model_role=_role,
            max_tool_calls=SUB_MAX_ITERATIONS - 1,
        )
    except asyncio.CancelledError:
        # CancelledError is BaseException — `except Exception` below never sees it, so the closing
        # subagent_done was skipped. Stamp it, then RE-RAISE: the turn's cancellation contract depends on it.
        me = asyncio.current_task()
        if me is not None and me.cancelling() == 0:
            # Nobody cancelled THIS helper, so the error was raised inside it — and this is the only
            # place its frames exist: `asyncio.shield` in the loop's heartbeat replaces them with a
            # bare cancellation on the way out, so a runner-side log can never name where it started.
            log.error("delegate DIED — a stray CancelledError nobody requested, goal=%r",
                      str(goal)[:120], exc_info=True)
        try:
            await emit_task(
                ctx.session_id, "subagent_done", id=sub_id, ok=False, interrupted=True,
                summary="The helper was stopped before it finished.", run_id=rid,
            )
        except Exception:
            pass
        raise
    except Exception as e:
        error = f"{type(e).__name__}: {e}"  # the REAL exception, not a placeholder
    finally:
        # In the finally, not after the call: a helper that raises still fetched real URLs the parent should
        # cite. Guarded because a raise HERE would replace the CancelledError the contract above depends on.
        try:
            citations.merge_from_child(ctx, child)
        except Exception:
            pass

    ok = error is None
    if ok and not (summary and summary.strip()):
        summary = "The helper finished without a written summary."
    await emit_task(
        ctx.session_id, "subagent_done", id=sub_id, ok=ok,
        # The FULL helper write-up — a tight cap cuts mid-sentence and mid-markdown; 6000 is a generous SSE ceiling.
        summary=(summary.strip()[:6000] if ok else (error or "")[:300]), run_id=rid,
    )
    return summary if ok else f"(helper failed: {error})"
