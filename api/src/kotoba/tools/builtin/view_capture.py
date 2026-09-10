"""view_capture — re-open a screenshot Kotoba saved earlier and look at it again (true visual recall).

Screenshots she takes (browser_take_screenshot) persist in the user's Files. The work turn that captured
them is gone by the time the user asks a follow-up ("what was in that post?"), and elision drops old images
mid-turn. This tool reads a saved capture back from the Files library and returns it as an image, so the
vision model SEES it again and can answer in detail — instead of asking the user to resend it. Companion-safe
(read-only, jailed to the library), so it works in a normal voice follow-up too.
"""
from __future__ import annotations

import asyncio
import base64

SCHEMA = {
    "type": "function",
    "name": "view_capture",
    "description": (
        "Re-open a screenshot you saved earlier (it's in the user's Files) and look at it again, so you can "
        "answer about what's in it. Pass `file` — the capture's filename, which is listed in your RECENT "
        "CAPTURES block (e.g. 'screenshot-3-ad8d25.png'); the VISUAL MEMORY block lists entities, not "
        "filenames, so use recall_image for those. Use this for a follow-up about something you captured INSTEAD "
        "of asking the user to send it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "Filename of the saved capture to re-open."},
        },
        "required": ["file"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "core"   # companion-safe: a voice follow-up may need to look again
RISK = "read"

ANNOUNCE = "Let me look at that again..."
HEARTBEAT: list[str] = []
COMPLETE = "Okay, I'm looking at it now~"
FAIL = "I couldn't find that capture to look at again."
EXPRESSIONS = {"focus": "thinking", "done": "happy", "fail": "confused"}


def _resolve_in_library(file: str):
    """Find a saved capture in the library, returning its path or None. Jailed to the library root.

    Captures live in SUBDIRS (screenshots/browser/, screenshots/blender/, …), not just the root, so the old
    basename-only-in-root lookup missed them (the live "view_capture ok=False" bug → she couldn't see the
    shot → confabulated). Resolution order, all jailed: (1) the path as given relative to the library
    (handles 'screenshots/browser/x.png'); (2) the basename in the root; (3) a recursive search for the
    basename anywhere under the library (newest match wins). Symlink/.. escapes are rejected."""
    from kotoba.core import file_library

    raw = (file or "").strip().replace("\\", "/").lstrip("/")
    if not raw:
        return None
    root = file_library.library_dir().resolve()

    def _jailed(p):
        try:
            rp = p.resolve()
        except Exception:
            return None
        return rp if (rp == root or root in rp.parents) and rp.is_file() else None

    hit = _jailed(root / raw)
    if hit:
        return hit
    name = raw.split("/")[-1]
    hit = _jailed(root / name)
    if hit:
        return hit
    try:
        matches = sorted(
            (m for m in root.rglob(name) if _jailed(m)),
            key=lambda m: m.stat().st_mtime, reverse=True,
        )
    except Exception:
        matches = []
    return matches[0] if matches else None


def _load(file: str):
    """Return (data_url, name) for a saved image in the library, or (None, None). Jailed to the library."""
    from kotoba.core import file_library

    target = _resolve_in_library(file)
    if target is None:
        return None, None
    try:
        raw = target.read_bytes()
    except Exception:
        return None, None
    mt = file_library.media_type_for(target)
    if not mt.startswith("image/"):
        return None, None
    return f"data:{mt};base64,{base64.b64encode(raw).decode()}", target.name


async def execute(args: dict, ctx):
    from kotoba.tools import ToolResult

    # It resolves a name by recursive glob over her person's workspace and hands the bytes to the
    # vision model, which then describes them wherever the turn is. Being withheld is not a door:
    # dispatch resolves by name, and a read-risk tool draws no card either. This is the door.
    from kotoba.discord import state

    who = state.actor()
    if who is not None and not who.is_owner:
        return "Those are my person's pictures, not mine to open for anybody else."

    file = (args or {}).get("file", "")
    data_url, name = await asyncio.to_thread(_load, file)
    if not data_url:
        return None  # graceful FAIL line
    return ToolResult(text=f"[Re-opened your saved capture {name} — analyze the image and answer.]",
                      images=[data_url])
