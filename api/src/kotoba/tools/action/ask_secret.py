"""ask_secret — ask the user to type ONE sensitive thing (a password) in a MASKED secure box, ONE TIME.

Unlike request_credential (which stores Kotoba's OWN reusable .env creds in the DB), this is for the USER's
sensitive password used right now and NOT kept: the value goes to the ephemeral in-memory store (never
the DB, never the transcript, never the model). The model gets back only a `{{secret:NAME}}` placeholder;
type that token into the field and the MCP layer swaps it for the real value at the browser, then it's
cleared when the work finishes. Work-mode only (it blocks for the user to type — fine off the voice turn).
"""
from __future__ import annotations

from kotoba.core import ephemeral_secrets, interaction

SCHEMA = {
    "type": "function",
    "name": "ask_secret",
    "description": (
        "Ask the user to type a SENSITIVE one-time value (a password) into a masked secure box, used right "
        "now and NOT saved anywhere. Use this to log into the USER'S OWN account in the browser. You get "
        "back a placeholder token {{secret:NAME}} — NOT the value; type THAT token into the password field "
        "(e.g. browser_type text=\"{{secret:NAME}}\") and it becomes the real secret only at the browser, "
        "never shown to you. `name` is a short id you choose (e.g. 'facebook')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short id for this one-time secret, e.g. 'facebook'."},
            "prompt": {"type": "string", "description": "What you're asking the user to type."},
        },
        "required": ["name", "prompt"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "browser"   # work-only (not in COMPANION_TOOLSETS) → never blocks the voice turn
RISK = "read"
INTERACTIVE = True    # wait quietly for the user to type; not cancelled by the 30s compute timeout

ANNOUNCE = ""
HEARTBEAT: list[str] = []
COMPLETE = ""
FAIL = "I couldn't get that — let's try that part again."
EXPRESSIONS = {"focus": "thinking", "done": "happy", "fail": "neutral"}


async def execute(args: dict, ctx) -> str:
    name = ((args or {}).get("name") or "").strip()
    prompt = ((args or {}).get("prompt") or "").strip()
    if not name or not prompt:
        return None
    sid = getattr(ctx, "session_id", None)
    # Idempotent: if we already collected this one-time secret, reuse it — do NOT open another box.
    # (Without this, a retried login made the model re-ask, popping form after form.)
    if sid and ephemeral_secrets.get(sid, name) is not None:
        return (
            f"Already have it — type the placeholder {{{{secret:{name}}}}} into the field, not the real "
            f"value. Don't ask for it again."
        )
    if sid:
        try:
            from kotoba.core import work_state

            work_state.set_step(sid, f"waiting for you to type {name} securely")
        except Exception:
            pass

    # "secret" kind → masked, one-time card on the frontend (no name field, "not saved" note).
    card: dict = {}
    value = await interaction.request_input(sid, prompt, "secret", card=card)
    if not value:
        verdict = card.get("verdict") or interaction.UNANSWERED
        interaction.note_no_run(ctx, verdict)
        return interaction.refusal_note(verdict, f"entering “{name}”")

    # Hold it ONLY in memory for the proxy to substitute; never DB, never returned to the model.
    ephemeral_secrets.put(sid, name, value)
    return (
        f"Got it securely. Type the placeholder {{{{secret:{name}}}}} into the field — exactly that text, "
        f"NOT the real value (which you never see, repeat, or save). It's used once for this login and then "
        f"discarded."
    )
