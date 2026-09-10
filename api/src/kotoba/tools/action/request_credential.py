"""request_credential — SAVE one of Kotoba's OWN reusable credentials (.env-style) to the database.

These belong to her: her own service logins and API keys she may reuse later. The tool opens a masked
secure box, stores the value under the `cred:` namespace, and confirms. It is idempotent — a name
already saved gets a confirmation, never a second box.

Reusable does NOT mean readable. `get_credential` hands the model the same `{{secret:NAME}}`
placeholder `ask_secret` does and resolves it at the browser, so nothing here ever puts a raw value in
front of the model. What separates the two tools is LIFETIME, not access. Work-mode only, since it
blocks for the user to type."""
from __future__ import annotations

from kotoba.core import interaction

SCHEMA = {
    "type": "function",
    "name": "request_credential",
    "description": (
        "SAVE one of your OWN reusable credentials (a login or API key you'll keep and reuse, stored like "
        "an .env value). Opens a masked secure box, stores it under `name`, and confirms. You USE it later "
        "with get_credential, which hands you the {{secret:NAME}} placeholder to type into a field — never "
        "the value, which you never see. Do NOT use this for the user's sensitive one-time "
        "password (use ask_secret for that). `name` is a short id, e.g. 'my_openai'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short id to store it under, e.g. 'my_openai'."},
            "prompt": {"type": "string", "description": "What you're asking the user to type."},
        },
        "required": ["name", "prompt"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "browser"   # work-only (not in COMPANION_TOOLSETS) → never blocks the voice turn
RISK = "read"
INTERACTIVE = True

ANNOUNCE = ""
HEARTBEAT: list[str] = []
COMPLETE = ""
FAIL = "I couldn't save that — let's try that again."
EXPRESSIONS = {"focus": "thinking", "done": "happy", "fail": "neutral"}


_CRED_PREFIX = "cred:"  # user credentials are namespaced so get_credential can never reach system keys
                        # (llm:* provider API keys, mcp:* / mcp_oauth:* tokens).


async def execute(args: dict, ctx) -> str:
    name = ((args or {}).get("name") or "").strip()
    prompt = ((args or {}).get("prompt") or "").strip()
    if not name or not prompt:
        return None
    sid = getattr(ctx, "session_id", None)
    db = getattr(ctx, "db", None)
    if db is None:
        # Production always sets ctx.db; this is the code-level guard. Never open the box for a value
        # that has nowhere to live — the old path collected the secret and then said "Saved" over nothing.
        interaction.note_no_run(ctx, interaction.UNREACHABLE)
        return ("I can't reach my credential store right now, so I didn't ask for anything and nothing "
                "was saved — tell the user saving credentials isn't possible at the moment.")
    key = _CRED_PREFIX + name
    # Idempotent: if it's already saved, don't pop another box — just confirm (kills the re-ask storm).
    if await db.get_key(key):
        return f"You already have '{name}' saved — you can use it; no need to enter it again."
    card: dict = {}
    value = await interaction.request_input(sid, prompt, "key", card=card)
    if not value:
        # Nothing was saved, and WHY decides everything downstream: without a witness the ending was
        # graded a tool failure, so a person who chose not to hand over a key was told she had failed
        # and invited to try again — the one ending the rules here call not an error.
        verdict = card.get("verdict") or interaction.UNANSWERED
        interaction.note_no_run(ctx, verdict)
        return interaction.refusal_note(verdict, f"saving “{name}”")
    await db.save_key(key, value)
    return f"Saved as '{name}' — it's stored with your credentials and you can use it anytime."
