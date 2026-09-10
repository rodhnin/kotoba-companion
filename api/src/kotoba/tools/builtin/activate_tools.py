"""activate_tools — load a connected MCP server's tools on demand (progressive disclosure).

Connected MCP servers other than the browser are DEFERRED: their tools aren't in the work toolset until the
model needs them, so 44 unused github tools don't bloat/confuse the toolset (selection accuracy drops past
~30-50 tools). The work prompt lists the AVAILABLE TOOLSETS; when a task needs one, the model calls
activate_tools('<server>') and that server's tools appear on the next step (the loop re-reads schemas_for
with the now-larger active set each iteration). Activation lasts the SESSION.
"""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "activate_tools",
    "description": (
        "Load the tools of a connected capability you need but don't currently have in your toolset (see "
        "the AVAILABLE TOOLSETS list — e.g. 'github', 'notion'). After you call this, that server's tools "
        "appear on your NEXT step and you can use them. Only activate what the task actually needs. For a "
        "capability that ISN'T connected yet, use mcp_find instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "The connected server name to load (e.g. 'github')."},
        },
        "required": ["server"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "mcp"   # management toolset (work-only, never deferred — like mcp_find/mcp_install)
RISK = "read"

ANNOUNCE = "One sec — pulling up those tools..."
HEARTBEAT: list[str] = []
COMPLETE = "Got them — ready to use."
FAIL = "I don't have that one connected — let me find it instead."
EXPRESSIONS = {"focus": "thinking", "done": "happy", "fail": "confused"}


async def execute(args: dict, ctx) -> str:
    from kotoba.core import mcp_active

    server = ((args or {}).get("server") or "").strip()
    if not server:
        return None
    mcp = getattr(ctx, "mcp", None)
    server_tools = getattr(mcp, "server_tools", {}) if mcp is not None else {}
    # Accept a close match (the model might say 'GitHub' or 'github mcp') — by TOKEN, not substring, so
    # 'git' does NOT wrongly activate a connected 'github' (a bare-substring match did exactly that).
    import re as _re

    def _tokens(s: str) -> set[str]:
        return {t for t in _re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) >= 2 and t != "mcp"}

    match = None
    low = server.lower()
    q = _tokens(server)
    for name in server_tools:
        if name.lower() == low or (_tokens(name) & q):
            match = name
            break
    from kotoba.core.loop import note_tool_refusal

    if match is None:
        connected = ", ".join(server_tools.keys()) or "none"
        note_tool_refusal(ctx)
        return (f"I don't have a connected server matching '{server}'. Connected: {connected}. "
                "If it's a NEW capability, use mcp_find to discover and install it.")
    tools = server_tools.get(match) or []
    if not tools:
        note_tool_refusal(ctx)
        return f"'{match}' is connected but exposes no tools right now."
    mcp_active.activate(getattr(ctx, "session_id", None), match)
    pretty = ", ".join(t.split("__", 1)[-1] for t in tools[:10]) + ("…" if len(tools) > 10 else "")
    return f"Activated '{match}' — its {len(tools)} tools are available from your next step: {pretty}."
