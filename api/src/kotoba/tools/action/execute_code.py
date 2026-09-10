"""execute_code — run Python in the Task's sandbox. On the default `local` backend that is the HOST, in
the jailed working dir with a scrubbed env; with KOTOBA_SANDBOX=docker it is an isolated, network-off
container. On the host the ApprovalGate asks before running unless the user chose "always allow".

Arbitrary Python is never auto-safe on the host, so an "always allow" is saved under one fixed
`execute_code` family, not per snippet, and code that deletes trees, shells out, evals or opens raw
sockets re-prompts anyway. The WHOLE snippet is the approval label, so nothing is approved unseen.

The choice between blocking on the card and deferring is `shell`'s. The fallback below it is not
shared: with no gate wired at all, `shell` refused destructive commands outright and this had not."""
from __future__ import annotations

from kotoba.core.approval import dangerous_code

SCHEMA = {
    "type": "function",
    "name": "execute_code",
    "description": (
        "Run a snippet of Python in your working folder and get stdout/stderr back. Good for quick computation, "
        "transforming files in the working folder, etc. The user is asked to approve before it runs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to run."},
            "timeout": {"type": "integer", "description": "Max seconds. Default 60."},
        },
        "required": ["code"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "code"
RISK = "exec"
# Honor the code's own `timeout` (+ approval headroom) in the harness cancel.
ARG_TIMEOUT = True
MAX_TIMEOUT = 600

ANNOUNCE = "Let me work that out in my head~"
HEARTBEAT = ["Crunching it...", "Almost have the result..."]
COMPLETE = "Got it! Here's the result:"
FAIL = "That didn't run cleanly — let me look at it again."
EXPRESSIONS = {"focus": "thinking", "done": "excited", "fail": "confused"}


def check() -> bool:
    from kotoba.core.sandbox import sandbox_available_sync

    return sandbox_available_sync()


async def execute(args: dict, ctx) -> str:
    code = (args or {}).get("code", "")
    if not code.strip():
        return None
    timeout = int((args or {}).get("timeout", 60) or 60)

    if ctx.approval is not None:
        action = f"run Python:\n{code}"
        # `force_ask` skips the allowlist/saved/auto-safe shortcut, re-prompting under the family.
        risky = dangerous_code(code) is not None
        from kotoba.core import events as _events
        from kotoba.core.interaction import el_agent_turn

        cannot_block = (el_agent_turn(ctx) is True
                        and _events.has_listener(getattr(ctx, "session_id", None)))
        if cannot_block and not ctx.approval.would_auto_allow(action, "exec", family="execute_code", force_ask=risky):
            from kotoba.core import deferred_exec

            async def _run() -> str:
                sb = await ctx.ensure_sandbox()
                if sb is None:
                    # Raised, not returned: a returned sentence is a SUMMARY, and the deferred path
                    # would file this approval as code that reached the machine.
                    raise deferred_exec.NothingRan("I couldn't get the environment ready to run it.")
                res = await sb.run_code(code, timeout=timeout)
                out = (res.stdout or "")[:2000]
                err = (res.stderr or "")[:500]
                s = f"I ran your code (exit code {res.exit_code})."
                if out:
                    s += " Output: " + out
                if err:
                    s += " Warning: " + err
                return s

            # Non-None: already carded for this request — one approval must never become two executions.
            already = deferred_exec.schedule(
                ctx, action, _run, label="your code", step_kind="code", family="execute_code",
            )
            if already:
                return already
            return ("I asked for your permission on screen to run that code — approve it and I'll run it "
                    "right away; I'll tell you the result when it's done.")
        if not await ctx.approval.confirm(action, "exec", family="execute_code", force_ask=risky):
            # Nothing ran → the loop must not write its "executed" audit row.
            from kotoba.core.deferred_exec import _mark_no_execution
            from kotoba.core.interaction import no_run_result

            _mark_no_execution(ctx, getattr(ctx, "call_id", ""))
            return no_run_result(ctx, "running that code",
                                 "I held off on running that code — I didn't get the go-ahead.")

    elif dangerous_code(code):
        # No gate wired → never run code that reaches disk or the network unattended.
        from kotoba.core.loop import note_tool_refusal

        note_tool_refusal(ctx)
        return "That code touches things I shouldn't run without your okay, so I left it alone."

    sb = await ctx.ensure_sandbox()
    if sb is None:
        return None
    res = await sb.run_code(code, timeout=timeout)
    out = (res.stdout or "")[:6000]
    err = (res.stderr or "")[:1500]
    parts = [f"exit={res.exit_code}"]
    if out:
        parts.append("stdout:\n" + out)
    if err:
        parts.append("stderr:\n" + err)
    return "\n".join(parts)
