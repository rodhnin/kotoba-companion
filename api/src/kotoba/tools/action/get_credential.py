"""get_credential — use or list Kotoba's OWN saved credentials (.env-style, stored via request_credential).

These belong to her, but the raw value is NEVER returned to the model (it would land in the work context /
spoken summary / logs). Instead, with a `name` we load the saved value into the per-session ephemeral
store and hand back the SAME {{secret:NAME}} placeholder the one-time secrets use: type that token into a
browser field and the MCP layer swaps in the real value at the last moment — the value never touches the
model, the transcript, or our logs. With no `name` it lists what she has saved (names only). Work-mode only.
(The USER's sensitive one-time password is NOT here; that's ask_secret, ephemeral and never saved.)
"""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "get_credential",
    "description": (
        "Use one of YOUR OWN saved credentials (stored with request_credential). Give a `name` and you get "
        "back a placeholder token {{secret:NAME}} — NOT the value; type THAT token into the field (e.g. "
        "browser_type text=\"{{secret:NAME}}\") and it becomes the real credential only at the browser, "
        "never shown to you. Omit `name` to list what you have saved (names only)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Which saved credential to use; omit to list names."},
        },
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "browser"   # work-only
RISK = "read"

ANNOUNCE = ""
HEARTBEAT: list[str] = []
COMPLETE = ""
FAIL = "I couldn't find your saved credentials just now."


# User credentials (request_credential) are stored under this prefix. get_credential ONLY ever touches keys
# in this namespace — so it can never list or load system secrets (provider API keys `llm:*`, MCP tokens
# `mcp:*`/`mcp_oauth:*`), which would otherwise be exfiltratable via the browser-substituted placeholder.
_CRED_PREFIX = "cred:"


async def execute(args: dict, ctx) -> str:
    db = getattr(ctx, "db", None)
    if db is None:
        return None
    name = ((args or {}).get("name") or "").strip()
    if not name:
        rows = await db.list_key_names()
        raw = [r.get("name") if isinstance(r, dict) else str(r) for r in (rows or [])]
        # ONLY expose user credentials — strip the cred: prefix, hide everything else (system keys).
        names = [n[len(_CRED_PREFIX):] for n in raw if n and n.startswith(_CRED_PREFIX)]
        if not names:
            return "You don't have any saved credentials yet."
        return "Your saved credentials: " + ", ".join(names) + "."
    # A model-supplied name is ALWAYS namespaced; a name that tries to reach a system key can't (the prefix
    # is prepended, so 'llm:openai:api_key' becomes 'cred:llm:openai:api_key', which doesn't exist).
    value = await db.get_key(_CRED_PREFIX + name)
    if value is None:
        from kotoba.core.loop import note_tool_refusal

        note_tool_refusal(ctx)
        return f"You don't have a saved credential named '{name}'."
    # Load it into the ephemeral store (per session) and return ONLY the placeholder — exactly like
    # ask_secret. The MCP proxy resolves {{secret:NAME}} at the browser; the value never reaches the model.
    from kotoba.core import ephemeral_secrets

    ephemeral_secrets.put(getattr(ctx, "session_id", None), name, value)
    return (
        f"Ready — type the placeholder {{{{secret:{name}}}}} into the field, exactly that text, NOT the real "
        f"value (which you never see, repeat, or save). It fills the real credential only at the browser."
    )
