"""memory_write — persist a long-term fact about the user (USER.md, Memory v2).

Only facts still true in about seven days. A CORRECTION is written and the stale version retired; when
the store still answers "duplicate" the reply QUOTES the fact it matched, so she can never tell the
user "I already knew that" about a new value she just failed to save.

The schema demands ENGLISH and ONE ATOMIC FACT, and both are CHECKED here and refused, because a
description the model ignores is not enforcement: the store ended up with six contradictory favourite
colours, one in Spanish, two with a second fact bolted on, and every one permanently uncorrectable.
Nothing is translated or split automatically. The reply states what it REPLACED and what CONTRADICTS."""
from __future__ import annotations

import asyncio
import re
import unicodedata

SCHEMA = {
    "type": "function",
    "name": "memory_write",
    "description": (
        "Save a DURABLE fact worth remembering long-term — about the user OR their projects, plans, or "
        "important context (identity, preferences, ongoing projects, relationships). Write `fact` as a "
        "SHORT third-person statement IN ENGLISH — English is the memory store's canonical language "
        "(translate if the user spoke another one; you'll naturally answer back in theirs), and the "
        "duplicate detector only works within one language. Save ONE atomic fact per call: a compound "
        "statement ('likes X and their color is Y') is separate calls, one per fact. Both rules are "
        "ENFORCED: a non-English or compound fact is refused and must be rewritten. Never save a fact "
        "already in your memory context, even reworded. Do NOT save transient task state or step-by-step "
        "actions (creating/moving/deleting a file, running a command, 'trying to sign in', a one-off page "
        "you just made) — those are not memories. Reuse an EXISTING topic when one fits (check what you "
        "already have) instead of inventing a near-synonym; otherwise pick a short free-form `topic` "
        "(e.g. 'preferences', 'work', or a project name like 'aurora-website')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "One concise third-person fact IN ENGLISH, e.g. 'Building Aurora, a "
                "Next.js photography portfolio site' — NOT the user's verbatim message, NOT several "
                "facts joined into one sentence.",
            },
            "topic": {
                "type": "string",
                "description": "Short free-form category (one or two words), e.g. 'work', 'preferences', "
                "or a project name. Defaults to 'general' if truly unsure.",
            },
        },
        "required": ["fact"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "memory"
RISK = "write"

ANNOUNCE = "Oh, that's worth remembering — I'll keep it."
HEARTBEAT = ["Writing it down..."]
COMPLETE = "Got it. I won't forget that~"
FAIL = "I couldn't save that just now — say it once more and I'll get it down."

# Spanish is the only non-English this store has ever received; anything else passes as before.
_ES_MARKED = {"favorito", "favorita", "favoritos", "favoritas", "preferido", "preferida",
              "preferidos", "preferidas", "cumpleanos"}
_ES_VERBS = {"es", "esta", "estan", "tiene", "tienen", "gusta", "gustan", "vive", "viven", "trabaja",
             "trabajan", "prefiere", "prefieren", "quiere", "quieren", "llama", "estudia", "hablan"}
_ES_FUNC = {"el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "con", "para", "por",
            "que", "su", "sus", "le", "les", "al", "en", "se", "mi", "tu", "ademas"}
def _looks_spanish(fact: str) -> bool:
    """Evidence-weighted, deliberately hard to trigger: a Spanish-only word, or a Spanish verb plus a
    second signal. Capitalized tokens are skipped ('Lives in El Paso', 'Uses ES modules') and quoted
    spans dropped, because a title's language is not the fact's."""
    from kotoba.core import user_memory

    marked = strong = weak = 0
    for i, w in enumerate(re.findall(r"[^\W\d_]+", user_memory.unquoted(fact))):
        # The capital that opens a sentence says nothing about proper nouns — and a fact here is
        # written verb-first, so skipping it threw away the only strong signal Spanish had.
        if i and w[:1].isupper():
            continue
        t = "".join(c for c in unicodedata.normalize("NFKD", w.lower()) if not unicodedata.combining(c))
        marked += t in _ES_MARKED
        strong += t in _ES_VERBS
        weak += t in _ES_FUNC
    return bool(marked) or strong >= 2 or (strong >= 1 and weak >= 1)


def _refusal(fact: str) -> str | None:
    from kotoba.core import user_memory

    if user_memory.is_compound(fact):
        return (
            "NOT SAVED — that reads as more than one fact, and a compound fact can never be corrected "
            "later without destroying its other half. Call memory_write once per fact, dropping none of "
            "it; if it really is a single fact, say it again without the second clause. You wrote: "
            f'"{fact}"'
        )
    if _looks_spanish(fact):
        return (
            "NOT SAVED — memory is stored in ENGLISH; a fact saved in another language can never be "
            "matched or corrected later. Translate it into one short third-person English statement and "
            f'call memory_write again. You wrote: "{fact}"'
        )
    return None


def _already(stored: str, fact: str) -> str:
    if stored.strip().lower() == fact.strip().lower():
        return f"Already remembered (nothing new to add): {fact}"
    return (
        f'Already remembered as: "{stored}" — nothing new was saved. If the user just stated '
        "something DIFFERENT from that stored fact, do not claim you already knew the new "
        "version: acknowledge what you have stored, and save the new statement reworded as a "
        "complete standalone fact."
    )


def _outcome(res: dict) -> str:
    msg = ""
    if res["retired"]:
        old = "; ".join(f'"{f}"' for f in res["retired"][:3])
        msg += (f" | REPLACED and deleted from memory: {old} — the old version is gone, say so plainly "
                "if the user asks what happened to it.")
    if res["conflicts"]:
        kept = "; ".join(f'"{f}"' for f in res["conflicts"][:3])
        msg += (f" | STILL STORED and contradicting this: {kept} — it bundles other facts (or names "
                "someone else) so it was kept on purpose. Do not claim it is gone; if it is wrong, save "
                "its other half as its own fact first.")
    return msg


def _text_only_note(ctx) -> str:
    """Appended only while the user has an image on the table: a note ABOUT a picture is not the picture.

    The success line is the last thing she reads before answering, and it cannot tell a note about a
    picture from the picture. Said here rather than in the prompt because this is the moment she is
    wrong. Silent once the picture really is in visual memory, or the warning becomes the lie.

    It rides EVERY answer that sounds like the fact is now held, not just the one that wrote it: an
    "already remembered" reads to her exactly like a save, and she repeats it about the image just the
    same. Only the two that claim nothing — an empty fact, and a refusal with no stored twin — go bare."""
    from kotoba.core import attachments

    if not attachments.unkept_images(getattr(ctx, "session_id", None)):
        return ""
    return (" | TEXT ONLY — no image was stored. The user shared a picture; if they asked you to keep IT, "
            'call remember_image(source="attachment") now, and do not say the image is saved until that '
            "returns.")


async def execute(args: dict, ctx) -> str:
    from kotoba.core import user_memory

    from kotoba.core.loop import note_tool_refusal

    fact = (args or {}).get("fact", "").strip()
    if not fact:
        note_tool_refusal(ctx)
        return "There was nothing specific to remember."
    topic = (args or {}).get("topic", "").strip() or "general"
    refusal = _refusal(fact)
    if refusal:
        stored = await asyncio.to_thread(user_memory.duplicate_of, fact)
        if not stored:
            note_tool_refusal(ctx)
            return refusal
        return _already(stored, fact) + _text_only_note(ctx)
    res = await asyncio.to_thread(user_memory.write_fact, fact, topic)
    if res["written"]:
        return f"Saved under '{topic}': {res['fact']}" + _outcome(res) + _text_only_note(ctx)
    stored = res["duplicate_of"]
    if res["reason"] == "restated":
        # Same value in other words: ordering a reworded save here would spend a call to be told this
        # again. Only the stale siblings it cleared are news.
        return (f'Already remembered in other words: "{stored}" — same value, nothing to add.'
                + _outcome(res) + _text_only_note(ctx))
    # The only remaining reason is a genuine duplicate — the ephemeral filter is off on this path, so
    # this line can no longer claim something is stored that was thrown away.
    msg = _already(stored, fact) if stored else f"Already remembered (nothing new to add): {fact}"
    return msg + _outcome(res) + _text_only_note(ctx)
