"""recall_image — search Kotoba's DURABLE visual memory and bring matching images back into view.

The visual twin of memory_recall: she searches her keepsakes and the top matches are RE-OPENED as
images, so she answers from what she actually saw. Recall has TWO audiences and used to serve one — the
images went to the vision model while the screen stayed empty — so each also rides the events channel
as a `recalled_image` frame carrying its ID only, keeping hundreds of KB out of the card queue.

The instruction is written from what was ATTACHED, never from what was found. An entry whose bytes are
gone still said "analyze the image(s)" and handed her, as the thing to analyse, her OWN old note — the
obvious completion being "here he is, dark hair and glasses", said as if looking."""
from __future__ import annotations

import asyncio

SCHEMA = {
    "type": "function",
    "name": "recall_image",
    "description": (
        "Look back at images you saved in your durable visual memory. Pass `query` — a person's name, the "
        "user, a product, or any keyword — and you'll SEE the matching saved image(s) again plus their "
        "notes, so you can answer about them. Use this for a follow-up about something you remembered "
        "(remember_image) instead of asking the user to resend it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Who/what to recall (entity name or keyword)."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "memory"
RISK = "read"
_MAX_IMAGES = 3  # bring back at most a few to keep the vision request sane

ANNOUNCE = "Let me picture that again..."
HEARTBEAT: list[str] = []
COMPLETE = "Got it — I can see it again:"
FAIL = "I don't have an image saved for that yet."
EXPRESSIONS = {"focus": "thinking", "done": "happy", "fail": "confused"}


async def execute(args: dict, ctx):
    from kotoba.tools import ToolResult
    from kotoba.core import visual_memory
    from kotoba.core.events import emit_task

    query = ((args or {}).get("query") or "").strip()
    if not query:
        return None
    hits = await asyncio.to_thread(visual_memory.search, query)
    if not hits:
        return None  # graceful FAIL line

    session_id = getattr(ctx, "session_id", None)
    seen, unseen, images = [], [], []
    for e in hits:
        line = f"- {e.get('about','?')} ({e.get('kind','other')}): {e.get('note','') or '(no note)'}"
        data_url = None
        if len(images) < _MAX_IMAGES:
            data_url = await asyncio.to_thread(visual_memory.image_data_url, e)
        if data_url:
            images.append(data_url)
            seen.append(line)
            # Only what she is actually looking at — the transcript must show her view, not the index.
            await emit_task(session_id, "recalled_image", id=e.get("id", ""),
                            about=e.get("about", ""), run_id=getattr(ctx, "run_id", ""))
        else:
            unseen.append(line)

    if not images:
        return (
            f"From your visual memory for '{query}': these entries are saved, but their images did NOT "
            "come back, so you are NOT looking at anything. The notes below are your own old words, not a "
            "picture — do NOT describe the image as if you could see it. Say plainly you can't bring it "
            "up right now, and answer only from what the note says you wrote.\n" + "\n".join(unseen)
        )
    text = (f"From your visual memory for '{query}' — {len(images)} image(s) attached below; analyze them "
            "and answer:\n" + "\n".join(seen))
    if unseen:
        text += ("\nALSO saved under this query but NOT attached (no image in front of you — old note "
                 "only, never describe these as if you were looking at them):\n" + "\n".join(unseen))
    return ToolResult(text=text, images=images)
