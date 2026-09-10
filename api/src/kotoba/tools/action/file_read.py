"""read_file — read a file from the jailed working dir, paginated by line."""
from __future__ import annotations

import asyncio

from kotoba.core.path_security import PathSecurityError, validate_within_dir

SCHEMA = {
    "type": "function",
    "name": "read_file",
    "description": (
        "Read a text file from your working folder. Returns the content (paginated by line). "
        "Use `start`/`limit` for big files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the working folder."},
            "start": {"type": "integer", "description": "First line (0-based). Default 0."},
            "limit": {"type": "integer", "description": "Max lines to return. Default 400."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "file"
RISK = "read"

ANNOUNCE = "Let me open that file~"
HEARTBEAT = ["Skimming through it...", "Almost got it..."]
COMPLETE = "Okay, here's what's inside:"
FAIL = "Hmm, that file wouldn't open for me — want to double-check the path?"
# Face per tool phase; the frontend maps these names onto .exp3 files.
EXPRESSIONS = {"focus": "thinking", "done": "happy", "fail": "sad"}

_MAX_CHARS = 8000


def _paginate(text: str, args: dict) -> tuple[str, str]:
    """The requested line window, and — when something was left out — a note SAYING so.

    They come back apart rather than joined because the window is quoted under a fence and the note is
    ours: a note printed inside the markers is a sentence the file appears to have said.

    Two cuts happen here and only one used to speak: the character cut sliced the joined text with
    nothing appended, so a file of few long lines came back mid-word reading as complete — and the
    line tail is empty precisely when that cut bites. The remainder is counted from the window
    actually taken, because a negative limit made the arithmetic ADD and promised more than the file
    had. Under 1 is clamped, not trusted."""
    lines = text.splitlines()
    start = max(int((args or {}).get("start", 0) or 0), 0)
    limit = max(int((args or {}).get("limit", 400) or 400), 1)
    window = lines[start : start + limit]
    chunk = "\n".join(window)
    left = max(len(lines) - (start + len(window)), 0)
    if not chunk.strip():
        return chunk, ""   # nothing to show → the caller says WHY, via _blank_fact
    if len(chunk) > _MAX_CHARS:
        dropped = len(chunk) - _MAX_CHARS
        note = f"cut here: {dropped} more characters in these lines"
        note += f", plus {left} more lines" if left else ""
        note += ". Read on with a larger `start` — do not treat this as the end of the file."
        return chunk[:_MAX_CHARS], note
    return chunk, (f"{left} more lines follow." if left else "")


def _blank_fact(path: str, text: str, args: dict) -> str:
    """A successful read with nothing visible in it must say so as a fact.

    A bare "" rides the loop's "returned nothing" path — not ok, plus a fail note — and the model then
    reports a file it READ as one it could not open, asking the user to check a path that exists. A thin
    unlabelled result forces the model to interpret, and it interprets emptiness as an access failure.
    State what happened instead: the read worked, and here is why there is nothing to show."""
    if not text:
        return f"Read {path} — the file exists and is empty (0 bytes)."
    total = len(text.splitlines())
    start = max(int(args.get("start", 0) or 0), 0)
    if start >= total:
        return f"Read {path} — it has only {total} lines, so start={start} is past the end of the file."
    return f"Read {path} — that range contains only whitespace (the file has {total} lines)."


async def execute(args: dict, ctx) -> str:
    raw = (args or {}).get("path", "").strip()
    if not raw:
        return None

    from kotoba.core.loop import note_tool_refusal

    # Host workdir (local / Docker bind-mount) — Kotoba runs on the user's machine; files are on disk.
    if ctx.workdir is None:
        return None  # no workspace in this mode → treated as failure
    try:
        p = validate_within_dir(raw, ctx.workdir)
    except PathSecurityError:
        note_tool_refusal(ctx)
        return "That path is outside my workspace, so I can't open it."
    if not p.exists() or not p.is_file():
        note_tool_refusal(ctx)
        return f"There's no file at {raw} yet."

    def _read() -> str:
        from kotoba.core.quoted import fence

        text = p.read_text(errors="replace", encoding="utf-8")
        chunk, note = _paginate(text, args or {})
        if not chunk.strip():
            return _blank_fact(raw, text, args or {})
        return fence(f"read_file opened {raw}", chunk, note=note)

    return await asyncio.to_thread(_read)
