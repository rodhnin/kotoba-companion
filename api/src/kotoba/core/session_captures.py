"""Session captures — the TRANSIENT log of screenshots Kotoba took while WORKING this session.

Distinct from durable visual memory: these are work-scratch screenshots saved to the
user's Files so she can recall what she just saw within the session (after elision drops the image) and
re-open one with view_capture. They are NOT keepsakes — for things worth remembering forever (a person's
photo, the user's face/preferences, a product they like) she explicitly calls remember_image → visual_memory.

In-memory, keyed by session_id. The images persist on disk.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("kotoba")

_log: dict[str, list[dict]] = {}  # session_id -> [{file, caption, ts}] (most recent last)
_CAP = 30


def record(session_id: str | None, file: str, caption: str = "") -> None:
    if not session_id or not file:
        return
    lst = _log.setdefault(session_id, [])
    lst[:] = [e for e in lst if e["file"] != file]  # de-dupe by filename (re-capture replaces)
    lst.append({"file": file, "caption": (caption or "").strip(), "ts": time.time()})
    del lst[:-_CAP]


def set_caption(session_id: str | None, file: str, caption: str) -> None:
    for e in _log.get(session_id or "", []):
        if e["file"] == file:
            e["caption"] = (caption or "").strip()
            return


def entries(session_id: str | None) -> list[dict]:
    return list(_log.get(session_id or "", []))


def has(session_id: str | None, file: str) -> bool:
    return any(e["file"] == file for e in _log.get(session_id or "", []))


def clear(session_id: str | None) -> None:
    _log.pop(session_id or "", None)


def prompt_block(session_id: str | None, limit: int = 12) -> str:
    """Lists this session's images so the model can recall/re-open them (view_capture) instead of asking
    the user to resend one it already has. '' when none.

    Two provenances share the list: screenshots she took, and images the user handed her (ctrl-v or
    /attach in the CLI). Saying "you took" of the second kind would have her claim a capture she never
    made, so the block names both."""
    es = entries(session_id)
    if not es:
        return ""
    lines = [f"- {e['file']}" + (f" — {e['caption']}" if e["caption"] else "") for e in es[-limit:]]
    return (
        "RECENT CAPTURES — screenshots you took, and images the user shared with you, THIS session (saved "
        "in the user's Files). Re-open any with view_capture(file) to look again; don't ask the user to "
        "resend one you already have. (If one is worth remembering long-term — a person, the user, a "
        "product — save it with remember_image.)\n"
        + "\n".join(lines)
    )


async def caption_image(client, data_url: str) -> str:
    """Best-effort one-line description of a screenshot, for the capture log. Cheap and never raises —
    returns '' on any error, including a rate limit.

    The pacer decides whether this runs at all. `ratelimit.throttle` returns the seconds still owed to
    the TPM window, and this caller is the case where the answer is to give up: nothing awaits it, the
    caption is pure enrichment, and the entry keeps the empty caption it already has. Sending anyway
    would spend a round trip on a near-certain 429 and take TPM from the very turn that produced the
    screenshot; waiting it out would hold a coroutine per capture far past the 15s cap this caller
    chose precisely to say it will not stall."""
    if client is None or not data_url or not data_url.startswith("data:"):
        return ""
    try:
        from kotoba.core.llm import model_call_kwargs, model_name
        from kotoba.core import ratelimit

        if await ratelimit.throttle(max_wait=15.0):
            return ""
        # Gate reasoning kwargs by the model (model_call_kwargs): a non-reasoning model (e.g. gpt-4o-mini)
        # 400s on reasoning/store/include — same bug class as the loop's. Don't hardcode them here.
        r = await client.responses.create(
            model=model_name(),
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": (
                    "In ONE short sentence, describe the main content visible in this screenshot (e.g. the "
                    "post's text/topic, or what page it is). No preamble, just the sentence.")},
                {"type": "input_image", "image_url": data_url, "detail": "low"},
            ]}],
            **model_call_kwargs(),
        )
        return (getattr(r, "output_text", "") or "").strip().replace("\n", " ")[:200]
    except Exception:
        return ""
