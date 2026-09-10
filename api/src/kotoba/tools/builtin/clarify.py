"""clarify — ask the user a single question and get the answer.

In a COMPANION turn the question IS the output: she reads it back and their reply is the next turn, so
echoing it to the model is the whole mechanism.

WORK MODE has no next turn. The job runs to completion against a throwaway queue nobody reads, so the
same echo asked into the void: the question came back as the tool result, the model wrote it out as
its final answer, and a run ended 4.7 s in carrying "Which page should I open?" as its SUMMARY —
announced as a finished result. So here it BLOCKS on the same on-screen card, and a question that
cannot be put on a screen is refused before it is asked rather than lost after."""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "clarify",
    "description": (
        "Ask the user a single clarifying question when their intent is genuinely ambiguous, and wait "
        "for their answer. Never ask for something the request already tells you."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The clarifying question to ask."}
        },
        "required": ["question"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "core"
RISK = "read"
INTERACTIVE = True    # in work mode it waits for a human — don't compute-cancel it at 30s

# The question itself is the output, so announce/complete stay empty.
ANNOUNCE = ""
HEARTBEAT: list[str] = []
COMPLETE = ""
FAIL = "Let me ask that a different way."


async def execute(args: dict, ctx) -> str:
    question = (args or {}).get("question", "").strip() or "Could you tell me a bit more?"
    if getattr(ctx, "mode", "companion") != "work":
        return question

    from kotoba.core import interaction
    from kotoba.core.events import has_listener

    if not has_listener(getattr(ctx, "session_id", None)):
        interaction.note_no_run(ctx, interaction.UNREACHABLE)
        return ("There is no screen to put that question on, so it was NOT asked and nobody will answer "
                "it. Do the most reasonable thing with what the request already gives you, or stop and "
                "report plainly what you were missing. Do not ask again.")
    card: dict = {}
    answer = await interaction.request_input(ctx.session_id, question, "text", card=card)
    if not answer:
        # Four endings, and only one of them is a decision they made. A bare None collapsed them into
        # one canned failure, so a card nobody saw was reported the same as a card they answered.
        verdict = card.get("verdict") or interaction.UNANSWERED
        interaction.note_no_run(ctx, verdict)
        return interaction.refusal_note(verdict, f"asking “{question}”")
    return f'The user answered: "{answer}". Continue from that now — don\'t ask again.'
