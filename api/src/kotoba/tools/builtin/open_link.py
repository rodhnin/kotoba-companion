"""open_link — offer to open a URL in a new browser tab, with the user's explicit confirmation.

Opens a card showing the link and a one-line reason; the user accepts (the tab opens client-side) or
declines. RETURNS IMMEDIATELY — it never blocks the voice turn and never opens a tab without consent.

The card is only claimed when it was DRAWN. The frame is dropped when nothing is listening, and the
guidance said "I put a card on screen offering to open that link" regardless, so she pointed the user
at a card that was never there. A voice turn also cannot speak the URL (the spoken filter cuts it), so
with no screen the honest move is to name the site and offer to leave the link in their files."""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "open_link",
    "description": (
        "Offer to open a web link in a NEW TAB for the user. Shows a card with the link and your reason; "
        "the user confirms before anything opens. Use it to hand them a source/page you found (e.g. the "
        "principal source after researching). Always pass a clear `why` — what the link is and why it's "
        "worth opening. Don't use this to 'read' a page yourself (that's web_extract/browser); this is for "
        "sending the USER to a page."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full URL to open (must start with http:// or https://)."},
            "why": {"type": "string", "description": "One line: what the link is and why it's worth opening."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "core"
RISK = "read"

ANNOUNCE = ""
HEARTBEAT: list[str] = []
COMPLETE = ""
FAIL = "Hmm, that one didn't go through — but I can tell you what it says instead."
EXPRESSIONS = {"focus": "happy", "done": "happy", "fail": "embarrassed"}


async def execute(args: dict, ctx) -> str:
    from kotoba.core import interaction

    url = ((args or {}).get("url") or "").strip()
    why = ((args or {}).get("why") or "").strip()
    # Only real web links — a voice agent must never be tricked into opening file:// or javascript: URIs.
    if not (url.startswith("http://") or url.startswith("https://")):
        # Witnessed as a REFUSAL, not returned as None: None is graded `failed`, and `failed` speaks
        # FAIL — "that one didn't go through" — over a link nothing ever tried to open. The same trade
        # the branch below already makes: say what happened, so a missing scheme can be corrected.
        from kotoba.core.loop import note_tool_refusal

        note_tool_refusal(ctx)
        return ("NOTHING was shown and NO card is on screen: that is not a web address I can offer — "
                "only http:// and https:// links open. If you have the real address, call open_link "
                "again with the full URL including the scheme; otherwise name the site in words.")
    drawn = await interaction.open_link_card(ctx.session_id, url, why)
    if not drawn:
        interaction.note_no_run(ctx, interaction.UNREACHABLE)
        return ("There is no screen to put that card on, so NOTHING was shown and the user has no link to "
                "accept. Do NOT tell them a card is waiting. Name the site in words instead, or offer to "
                "save the link into their files, and carry on.")
    return ("The link is in front of them now — a card to accept on a screen, the address itself in a "
            "chat. Say in one short line what it is, then wrap up this turn — do NOT wait here, and "
            "do NOT describe a card: you cannot see which of the two they got.")
