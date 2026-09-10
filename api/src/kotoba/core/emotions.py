"""extract_emotion — the FALLBACK face, for a reply that carried no audio tag.

The face normally comes from the tag she actually speaks, so voice and face have one source; this runs
only when there is no tag to read.

It goes through llm.utility_extract, i.e. the Responses API and a generous output budget. The old
`max_tokens=8`, sized so "embarrassed" would fit, was two bugs at once: gpt-5.x `chat.completions`
400s on `max_tokens` at all, and a reasoning model spends its budget before the answer. Falls back to
'neutral' on any error — which is what a starved call returned, silently, on every turn."""
from __future__ import annotations

from kotoba.core.llm import utility_extract

VALID_EMOTIONS: frozenset[str] = frozenset(
    {
        "neutral", "happy", "excited", "sad", "crying", "angry", "surprised",
        "embarrassed", "thinking", "sleepy", "affectionate", "confused", "scared", "determined",
    }
)


async def extract_emotion(text: str) -> str:
    if not text.strip():
        return "neutral"

    prompt = (
        f'Given this assistant response: "{text[:200]}"\n\n'
        "Choose the single most fitting emotion from:\n"
        "neutral, happy, excited, sad, crying, angry, surprised, "
        "embarrassed, thinking, sleepy, affectionate, confused, scared, determined\n\n"
        "Respond with exactly one word."
    )
    # Responses API via utility_extract (chat.completions max_tokens 400s on the default gpt-5.x). Take the
    # LAST word so a reasoning model's stray prose can't hide the answer; fall back to neutral.
    out = (await utility_extract(prompt, max_output_tokens=256)).strip().lower()
    emotion = out.split()[-1].strip(".!,\"'") if out else ""
    return emotion if emotion in VALID_EMOTIONS else "neutral"
