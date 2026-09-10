"""What someone attached to a Discord message, handed to the same eyes the web uses.

She has vision everywhere else; on Discord she was simply never given the bytes, so she said she
could not see an image that was plainly there. The web's path is the one to copy: a data URL stashed
for the next turn, which `load_context` injects as an image part.
"""
from __future__ import annotations

import base64
import logging

log = logging.getLogger("kotoba.discord")

# Under the 14 MB the data-URL front door accepts, with room for base64's third.
MAX_BYTES = 8_000_000

PDF = "application/pdf"


def _kind(content_type: str, filename: str) -> str:
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return "image"
    if ct == PDF or filename.lower().endswith(".pdf"):
        return "pdf"
    return ""


async def stash(msg, session_id: str) -> list[str]:
    """Keep what she can look at. Returns the names she has to say she could NOT take.

    A refusal has to reach her words: telling someone their file arrived and then answering as if it
    had not is the failure this returns a list to avoid.
    """
    from kotoba.core import attachments

    refused: list[str] = []
    for att in getattr(msg, "attachments", ()) or ():
        name = getattr(att, "filename", "file")
        kind = _kind(getattr(att, "content_type", "") or "", name)
        if not kind:
            continue
        if getattr(att, "size", 0) > MAX_BYTES:
            refused.append(f"{name} (too big)")
            continue
        try:
            raw = await att.read()
        except Exception:
            log.debug("could not read a Discord attachment", exc_info=True)
            refused.append(name)
            continue
        mime = getattr(att, "content_type", "") or ("application/pdf" if kind == "pdf"
                                                    else "image/png")
        data_url = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
        part = ({"type": "input_image", "image_url": data_url} if kind == "image"
                else {"type": "input_file", "filename": name, "file_data": data_url})
        if not attachments.add(session_id, part, name=name):
            refused.append(name)
    return refused
