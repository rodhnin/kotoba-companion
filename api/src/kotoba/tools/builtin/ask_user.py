"""ask_user — let Kotoba request the user to TYPE something specific (a link, a name, an exact value).

Opens the input card in the call and RETURNS IMMEDIATELY: it does not block the voice turn, since a
long wait inside the ElevenLabs turn kills the WebSocket. The typed entry comes back as their next
message; a 'key' is stored backend-only and never enters the conversation.

What comes back to the model is decided by `open_input_card`'s answer, not by the call returning. The
frame is dropped when nothing is listening, and the guidance still said "I opened a secure field on
screen", so she told the user to type an API key into a field that does not exist and then waited for
a message nobody could send."""
from __future__ import annotations

import re

SCHEMA = {
    "type": "function",
    "name": "ask_user",
    "description": (
        "Ask the user to TYPE something and wait for it (opens a text box in the call). Use for a link, "
        "an exact value, a name, or a secret. `kind`: 'text' (default), 'link', or 'key' (secret — saved "
        "securely, not shown back to you). When kind='key' you MUST also supply `name` (a short id such as "
        "'my_openai_key') so the value can be stored and retrieved — omitting it is an error."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "The LABEL of the input box — like the label printed next to a form field. It is NOT "
                    "a message to the user and NOT an explanation: no reasons, no apologies, no 'I'll use "
                    "it to…'. One line, under 60 characters. "
                    "GOOD: 'Your postal code'. GOOD: 'Paste the link here'. "
                    "BAD: 'Write your postal code here please. I'll use it only to adapt the information "
                    "to your area, such as showing you options, times or more accurate prices.' "
                    "Reasoning like that goes in `detail`, or is simply left out."
                ),
            },
            "detail": {
                "type": "string",
                "description": (
                    "Optional. Extra context shown only if the user taps to expand it. "
                    "Use for anything longer than the question itself."
                ),
            },
            "kind": {"type": "string", "enum": ["text", "link", "key"], "description": "Kind of input."},
            "name": {
                "type": "string",
                "description": (
                    "Required when kind='key'. Short id to store the credential under, e.g. 'my_openai_key'. "
                    "Must not contain ':'. You retrieve it later with get_credential(name=<this value>)."
                ),
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "core"
RISK = "read"
INTERACTIVE = True    # work mode blocks for the human to type — don't compute-cancel at 30s
_WORK_INPUT_TIMEOUT = 300.0  # captcha time; the compute budget EXCLUDES this wait and the EL call is kept alive

ANNOUNCE = ""
HEARTBEAT: list[str] = []
COMPLETE = ""
FAIL = "No worries — we can do that part later."
EXPRESSIONS = {"focus": "thinking", "done": "happy", "fail": "neutral"}


_TITLE_MAX = 60
# Below this, a "sentence" is an abbreviation, not a question ("Dr." in "Dr. Smith email address").
_TITLE_MIN = 12


def _sentence_splits(text: str):
    """Every (head, tail) split at a sentence end, shortest first — the caller takes the first head long
    enough to be a real label, so an abbreviation just moves the cut to the next boundary."""
    for m in re.finditer(r"(?<=[.!?])\s+", text):
        yield text[: m.start()], text[m.end():]


def _as_field_label(prompt: str, detail: str | None) -> tuple[str, str | None]:
    """Reduce `prompt` to a field label, moving whatever else it carries into the detail.

    The schema asks for a label and the model still writes a paragraph explaining why it needs the value.
    A card title has no room for one, so the shape is enforced here rather than requested: first sentence,
    clipped, and every word cut off survives in the expandable detail."""
    text = " ".join((prompt or "").split())
    title, rest = text, ""
    for candidate, tail in _sentence_splits(text):
        if len(candidate) >= _TITLE_MIN:
            title, rest = candidate, tail
            break
    if len(title) > _TITLE_MAX:
        head = title[:_TITLE_MAX].rsplit(" ", 1)[0] or title[:_TITLE_MAX]
        rest = f"{title[len(head):].strip()} {rest}".strip()
        title = head.rstrip(" ,;:—-") + "…"
    extra = " ".join(p for p in (rest, detail) if p).strip()
    return title, (extra or None)


async def execute(args: dict, ctx) -> str:
    from kotoba.core import interaction

    prompt = (args or {}).get("prompt", "").strip()
    if not prompt:
        return None
    kind = (args or {}).get("kind", "text").strip() or "text"
    detail = (args or {}).get("detail", "").strip() or None
    name = (args or {}).get("name", "").strip() or None
    prompt, detail = _as_field_label(prompt, detail)
    from kotoba.core.loop import note_tool_refusal

    # BLOCK when no "next user turn" can carry the value back (work mode, text channel — the CLI answers
    # the card in place). Only a voice turn stays non-blocking: a long wait inside the EL turn kills the WS.
    if getattr(ctx, "mode", "companion") == "work" or getattr(ctx, "channel", "voice") == "text":
        # NEVER echo a secret into the model context — THIS branch hands the typed value straight back to
        # the model; the non-blocking card below posts to /input, backend-only, so a key is safe only there.
        if kind in ("key", "secret"):
            note_tool_refusal(ctx)
            return ("Don't collect secrets with ask_user — nothing was kept, to keep it secure. For a "
                    "one-time password use ask_secret (it gives you a {{secret:NAME}} placeholder, never "
                    "the value); for your own reusable credential use request_credential.")
        card: dict = {}
        value = await interaction.request_input(
            ctx.session_id, prompt, kind, timeout=_WORK_INPUT_TIMEOUT, detail=detail, card=card,
        )
        if not value:
            # A bare None is graded a failure and speaks ONE canned line over four endings, so a box
            # nobody could draw and a box left empty on purpose sounded the same — the second is their
            # decision, the first never reached them. The witness is what keeps the row off a clean mark.
            verdict = card.get("verdict") or interaction.UNANSWERED
            interaction.note_no_run(ctx, verdict)
            return interaction.refusal_note(verdict, f"typing “{prompt}”")
        return f'The user typed: "{value}". Continue now (re-snapshot if the page changed, then proceed). Don\'t ask again or stop.'

    # COMPANION OVER VOICE: NON-blocking (see above). A `secret` card would render blocking with nothing
    # persisting it — the typed password was destroyed while the model was told it would arrive.
    if kind == "secret":
        note_tool_refusal(ctx)
        return ("Don't collect secrets with ask_user — use ask_secret, which gives you a "
                "{{secret:NAME}} placeholder and never shows you the value.")

    # A 'key' card with no `name` silently drops the typed value (no id to store under) — refuse before opening.
    if kind == "key" and not name:
        note_tool_refusal(ctx)
        return ("I need a `name` to store that credential — retry with a short id in `name`, "
                "e.g. name='my_openai_key'. Nothing was saved and no card was opened.")

    drawn = await interaction.open_input_card(ctx.session_id, prompt, kind, detail=detail, name=name)
    if not drawn:
        # Nothing was drawn, so nothing ran: without the witness the turn draws a clean ✓ over a card
        # that reached nobody, and the audit row says the same.
        interaction.note_no_run(ctx, interaction.UNREACHABLE)
        if kind == "key":
            return ("There is no screen to put that secure field on, so NO box was opened and nothing can "
                    "be typed into it. Do NOT tell the user a field is waiting for their key. Say plainly "
                    "that you can't take a key right now, and ask them to add it in Settings instead.")
        return ("There is no screen to put that box on, so NOTHING was opened and nobody can type into it. "
                "Do NOT tell the user a box is waiting. Ask them for the value in your own words, out "
                "loud, or carry on without it.")
    if kind == "key":
        return ("I opened a secure field on screen for the key — it's stored securely and never shown to "
                "me. Tell the user to type it there; I'll get their confirmation as their next message. "
                "Wrap up this turn now in one short line — do NOT wait here.")
    return ("I opened a text box on screen. Ask the user to type it there — their entry will come back as "
            "their next message. Wrap up this turn now in one short line — do NOT wait here.")
