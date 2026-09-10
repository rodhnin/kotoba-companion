"""mcp_install — install and connect an MCP server by name, so Kotoba gains its tools.

She resolves a vetted server from the curated allowlist by name, asks for approval
because launching a process is RISK=exec, connects, and persists it. The new tools appear on the NEXT
loop iteration.

Installing "browser" does NOT open one: ensure_browser used to run BEFORE the card, so a window landed
on screen even when the user then said no. The card goes through ask_approval on the CHANNEL this turn
reaches the user by, or a background install buys the 25-second voice clock. `_unmet_setup` refuses to
spend a card on a certainty — narrowly, since GitHub's missing PAT IS answerable from inside Kotoba."""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "mcp_install",
    "description": (
        "Install and connect one of your KNOWN MCP servers to gain its tools, by name. You can ONLY "
        "install these (use the exact name): 'browser' (drive a real web browser — navigate, click, "
        "type, screenshot a page), 'filesystem' (read/write files in a folder), 'github' (repos, issues, "
        "PRs), 'google calendar' (events), 'memory' (a knowledge graph), 'notion' / 'linear' / 'slack' "
        "(these need a browser sign-in — installing adds them to Settings → 'Needs connection' for the user "
        "to finish with one tap). For browsing a website or taking a screenshot, install 'browser'. Do NOT "
        "invent commands or package names — just pass the name."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": ("One of: browser, filesystem, github, google calendar, memory, "
                                "notion, linear, slack."),
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "mcp"
RISK = "exec"  # launches/installs a process → approval
# First-run `npx @playwright/mcp` downloads package + browser binaries — well over the default 30s.
TIMEOUT = 150

ANNOUNCE = "Let me get that set up for you..."
HEARTBEAT = ["Installing it...", "Almost connected...", "Setting up the tools..."]
COMPLETE = "All set — I've got those tools now!"
FAIL = "I couldn't get that one connected. Want me to try a different one?"
EXPRESSIONS = {"focus": "thinking", "done": "excited", "fail": "embarrassed"}


def _resolve(args: dict) -> tuple[str, dict] | None:
    """Resolve a server from the CURATED allowlist by name. We intentionally do NOT accept arbitrary
    command/url anymore: the model would invent garbage like `npx @mcp/cli install` (404). Installs are
    limited to whatever `core.mcp.known.KNOWN_SERVERS` holds — today browser, filesystem, github,
    google-calendar, memory, notion, linear and slack. Named here rather than counted, and the schema
    says the same eight: a list that drifts short of the allowlist is a server the model never offers."""
    from kotoba.core.mcp.known import build_cfg

    return build_cfg((args.get("name") or "").strip())


def _unmet_setup(name: str) -> str:
    """The server's `setup` sentence when a declared `env:` need is not satisfied, '' otherwise.

    Both halves are required. The variable alone says nothing about whether a person can supply it from
    where they are standing, and `setup_note` is the flag for the ones who cannot — it exists because
    "needs an API token, set X in the backend" is true of github and false of google-calendar in every
    word. Read through `resolve_env_need`, the single source the header injector and the endpoint's
    precheck already share, so an aliased variable name cannot make the three disagree."""
    from kotoba.core.mcp.known import resolve_env_need, resolve_known, setup_note

    found = resolve_known(name)
    note = setup_note(name)
    if not found or not note:
        return ""
    for need in (found[1].get("needs") or []):
        if str(need).startswith("env:") and not resolve_env_need(str(need).split(":", 1)[1]):
            return note
    return ""


async def execute(args: dict, ctx) -> str:
    from kotoba.core.mcp.client import MCPBusy, _AuthRequired

    args = args or {}
    if ctx.mcp is None:
        return None

    resolved = _resolve(args)
    if resolved is None:
        from kotoba.core.loop import note_tool_refusal

        note_tool_refusal(ctx)
        return (
            f"I don't recognize an MCP server called '{args.get('name')}'. The ones I can install are: "
            "browser, filesystem, github, google calendar, memory, notion, linear, or slack. Which would "
            "you like?"
        )
    name, cfg = resolved

    from kotoba.core import mcp_active

    # Already connected (pre-warmed / hydrated-but-deferred) → just activate it for this session.
    if name in getattr(ctx.mcp, "server_tools", {}):
        mcp_active.activate(getattr(ctx, "session_id", None), name)
        tools = ctx.mcp.server_tools.get(name) or []
        pretty = ", ".join(t.split("__", 1)[-1] for t in tools[:8])
        return f"'{name}' is connected — its tools are available now ({pretty}). Just use them."

    _missing = _unmet_setup(name)
    if _missing:
        from kotoba.core.loop import note_tool_refusal

        note_tool_refusal(ctx)
        return (f"I can't connect '{name}' yet — it needs something set up outside Kotoba first, so I "
                f"didn't install anything. Tell the user this, in their language: {_missing}")

    # Approve before installing — even a curated server runs third-party code on the host.
    from kotoba.core import interaction as _interaction

    _spec = cfg.get("command") and f"`{cfg['command']} {' '.join(str(a) for a in cfg.get('args', []))}`".strip()
    _spec = _spec or (cfg.get("url") and f"connect to {cfg['url']}") or "connect it"
    _verdict = await _interaction.ask_approval(
        ctx, f"Install the “{name}” MCP server ({_spec}) and connect it?",
        family="",  # nothing here persists an "always" — the card must not offer the button
        notice={
            "head": f"Install the “{name}” MCP server?",
            "facts": [["Runs", _spec.strip("`")],
                      ["Source", "Kotoba's own list of known servers — not a registry search"]],
        },
    )
    if _verdict != _interaction.APPROVED:
        return _interaction.refusal_note(_verdict, f"installing the “{name}” MCP server")
    try:
        new_tools = await ctx.mcp.connect(name, cfg)
    except _AuthRequired as auth:
        # Must catch BEFORE the generic except below — that one swallows it as a silent "couldn't connect".
        from kotoba.core.mcp import auth_flow
        from kotoba.core.mcp.known import resolve_known

        resolved = resolve_known(name)
        desc = (resolved[1].get("description") if resolved else "") or ""
        return await auth_flow.handle_auth(ctx, name, cfg, desc, auth)
    except MCPBusy:
        # Still queued, not empty: it usually lands seconds later. Don't persist or tear it down here.
        # Witnessed as a failure all the same — the connect was submitted and nothing came back, so the
        # call installed nothing. Unwitnessed it wore the green ✓ of a server that was never there.
        from kotoba.core.loop import note_tool_failure

        note_tool_failure(f"{name} was still queued when the connect window ran out")
        return (f"'{name}' is taking longer than usual to come up — another server is ahead of it in the "
                "queue. Tell the user it's still starting and try using it again in a moment.")
    except Exception:
        return None  # in-character FAIL (never leak the raw npm/connection error)

    import logging

    from kotoba.core.mcp.config import save_server

    persisted = True
    try:
        save_server(name, cfg)  # strips headers/env itself (config.strip_secrets): no PAT reaches the YAML
    except Exception:
        persisted = False
        logging.getLogger("kotoba.mcp").warning(
            "MCP: failed to persist server %r — it will not reconnect after restart", name, exc_info=True
        )

    if not new_tools:
        # Ran, and did not work: the server's process was spawned and save_server was attempted two
        # lines up. Both are traces, so this keeps its audit row rather than the ⊘ of a refusal.
        from kotoba.core.loop import note_tool_failure

        note_tool_failure(f"{name} connected but exposed no usable tools")
        return f"Connected '{name}', but it didn't offer any tools I could use."
    mcp_active.activate(getattr(ctx, "session_id", None), name)  # installed on demand → usable immediately
    pretty = ", ".join(t.split("__", 1)[-1] for t in new_tools[:8])
    reply = f"Connected '{name}'. I can now: {pretty}."
    if not persisted:
        # The reply carries the persistence outcome — the session works; the restart will not.
        reply += (" One heads-up to pass on: I couldn't save this connection, so it's for THIS session "
                  "only — after a restart it will need installing again.")
    return reply
