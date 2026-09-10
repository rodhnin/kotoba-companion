"""shell — run a command in the Task's sandbox: the HOST in a jailed workdir on `local`, an isolated
network-off container on `docker`. The ApprovalGate decides what may run — trivially-safe reads and an
"always allow" family go unprompted, everything else on the host asks first.

Whether a gated command blocks inline or DEFERS is keyed on the TRANSPORT — an ElevenLabs agent turn
is the only one that cannot block, since EL times a silent turn out and re-fires it, orphaning the
approval. `mode` and `channel` were both tried and named something adjacent to the constraint.

Both exits owe the audit trail the truth: the scheduler returns non-None when this command was already
carded for this request, and an inline denial marks the call as never executed."""
from __future__ import annotations

from kotoba.core.approval import detect_dangerous

SCHEMA = {
    "type": "function",
    "name": "shell",
    "description": (
        "Run a shell command in your working folder. Use it to run scripts, list files, etc. "
        "Anything beyond simple read-only commands asks the user to approve first. "
        "You CAN open things on the user's desktop from here: `xdg-open <path-or-url>` (macOS: `open`, "
        "Windows: `start`) opens a file, folder or link in whatever application they normally use. So "
        "when they ask you to open a PDF, a folder or a web page FOR THEM, do it with this tool instead "
        "of saying you cannot — you can. Run the opener in the FOREGROUND, never with `&` or with its "
        "output redirected away: it hands off and exits at once, and its exit code is your only proof "
        "(a `… &` line exits 0 even when the file does not exist). Backgrounding with redirected output "
        "is only for launching a GUI application binary directly, and is written differently per shell. Report "
        "it opened ONLY on exit 0; on failure tell the user what the error said."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": (
                "The command line, already run through this machine's own shell — `sh -c` on Linux and "
                "macOS, PowerShell on Windows — so pipes, redirects and quoting work as written. "
                "Never wrap it in another shell; that only hides what you are running."
            )},
            "timeout": {"type": "integer", "description": (
                "Max seconds to wait for the command itself. Default 60 — leave it there unless you "
                "have a reason. Backgrounding is not a reason: `… &` returns the moment the shell "
                "exits and what you started keeps running afterwards, so it needs no budget of its own."
            )},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "terminal"
RISK = "exec"
# The harness cancel honors the command's own `timeout` — else a 45s `pip install` the user approved
# dies at the 30s default. MAX_TIMEOUT is the hard ceiling.
ARG_TIMEOUT = True
MAX_TIMEOUT = 600

ANNOUNCE = "Let me run that real quick~"
HEARTBEAT = ["Working on it...", "Still running...", "Almost there..."]
COMPLETE = "Done! Here's what came back:"
FAIL = "That command didn't go through — want me to try another way?"
EXPRESSIONS = {"focus": "determined", "done": "happy", "fail": "embarrassed"}


def check() -> bool:
    from kotoba.core.sandbox import sandbox_available_sync

    return sandbox_available_sync()


async def execute(args: dict, ctx) -> str:
    command = (args or {}).get("command", "").strip()
    if not command:
        return None
    timeout = int((args or {}).get("timeout", 60) or 60)

    if ctx.approval is not None:
        from kotoba.core import events as _events
        from kotoba.core.interaction import el_agent_turn

        cannot_block = (el_agent_turn(ctx) is True
                        and _events.has_listener(getattr(ctx, "session_id", None)))
        if cannot_block and not ctx.approval.would_auto_allow(command, "exec"):
            from kotoba.core import deferred_exec

            async def _run() -> str:
                sb = await ctx.ensure_sandbox()
                if sb is None:
                    # Raised, not returned: a returned sentence is a SUMMARY, and the deferred path
                    # would file this approval as a command that reached the machine.
                    raise deferred_exec.NothingRan("I couldn't get the environment ready to run it.")
                res = await sb.run(command, timeout=timeout)
                out = (res.stdout or "")[:2000]
                err = (res.stderr or "")[:500]
                s = f"I ran `{command}` (exit code {res.exit_code})."
                if out:
                    s += " Output: " + out
                if err:
                    s += " Warning: " + err
                return s

            # Non-None: already carded for this request — one approval must never become two executions.
            already = deferred_exec.schedule(ctx, command, _run, label=command, step_kind="shell")
            if already:
                return already
            return ("I asked for your permission on screen to run that — approve it and I'll run it "
                    "right away; I'll tell you the result when it's done.")
        if not await ctx.approval.confirm(command, "exec"):
            # Nothing ran: suppress the loop's "executed" audit row, leaving only the gate's decision.
            from kotoba.core.deferred_exec import _mark_no_execution
            from kotoba.core.interaction import no_run_result

            _mark_no_execution(ctx, getattr(ctx, "call_id", ""))
            return no_run_result(
                ctx, f"running “{command}”",
                "I held off on that one — it looked risky and I didn't get the go-ahead.")
    elif detect_dangerous(command):
        # No gate wired (sessionless curl/test) → never run a destructive command unattended.
        from kotoba.core.loop import note_tool_refusal

        note_tool_refusal(ctx)
        return "That command looked risky, and I can't get your okay right now, so I left it alone."

    sb = await ctx.ensure_sandbox()
    if sb is None:
        return None  # no sandbox backend → graceful FAIL line
    res = await sb.run(command, timeout=timeout)
    out = (res.stdout or "")[:6000]
    err = (res.stderr or "")[:1500]
    parts = [f"exit={res.exit_code}"]
    if out:
        parts.append("stdout:\n" + out)
    if err:
        parts.append("stderr:\n" + err)
    return "\n".join(parts)
