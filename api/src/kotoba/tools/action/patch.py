"""patch — edit a file by replacing an exact (or whitespace-fuzzy) snippet.

Simpler and more reliable than parsing unified diffs: find `old_string`
and replace it with `new_string`. If the exact text isn't found, retry on a whitespace-normalized basis
so minor indentation drift still applies — the "fuzzy" matching the design calls for.
"""
from __future__ import annotations

import asyncio
import re

from kotoba.core.path_security import PathSecurityError, validate_within_dir

SCHEMA = {
    "type": "function",
    "name": "patch",
    "description": (
        "Edit a file in your working folder by replacing an exact snippet with new text. "
        "`old_string` must uniquely identify the spot."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the working folder."},
            "old_string": {"type": "string", "description": "Exact text to find and replace."},
            "new_string": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "file"
RISK = "write"

ANNOUNCE = "Let me tweak that file~"
HEARTBEAT = ["Finding the right spot...", "Applying the change..."]
COMPLETE = "Done — updated it!"
FAIL = "I couldn't find that part to change — can you show me the exact text?"
EXPRESSIONS = {"focus": "determined", "done": "happy", "fail": "confused"}


def _apply_patch(text: str, old: str, new: str) -> tuple[str, str]:
    """Replace `old` with `new`. Returns (new_text, status): 'ok' | 'empty' | 'notfound' | 'notunique'.
    Requires `old` to identify EXACTLY ONE spot — an ambiguous snippet is refused, not applied to the first
    occurrence (the schema promises uniqueness; a silent first-match edits the wrong place)."""
    # An empty / whitespace-only `old` matches everywhere → would silently PREPEND `new`. Refuse.
    if not old or not old.strip():
        return text, "empty"
    n = text.count(old)
    if n == 1:
        return text.replace(old, new, 1), "ok"
    if n > 1:
        return text, "notunique"  # ambiguous exact match — don't guess which one
    # Exact not found → whitespace-normalized fallback, but ALSO require a single match.
    tokens = old.split()
    if not tokens:
        return text, "notfound"
    pattern = re.compile(r"\s+".join(re.escape(tok) for tok in tokens))
    matches = list(pattern.finditer(text))
    if len(matches) == 1:
        m = matches[0]
        return text[: m.start()] + new + text[m.end() :], "ok"
    if len(matches) > 1:
        return text, "notunique"
    return text, "notfound"


async def execute(args: dict, ctx) -> str:
    raw = (args or {}).get("path", "").strip()
    old = (args or {}).get("old_string")
    new = (args or {}).get("new_string")
    if not raw or old is None or new is None:
        return None

    from kotoba.core.loop import note_tool_refusal

    # Host workdir (local / Docker bind-mount).
    if ctx.workdir is None:
        return None
    try:
        p = validate_within_dir(raw, ctx.workdir)
    except PathSecurityError:
        note_tool_refusal(ctx)
        return "That path is outside my workspace."
    if not p.exists() or not p.is_file():
        note_tool_refusal(ctx)
        return f"There's no file at {raw} to edit."

    def _apply():
        text = p.read_text(errors="replace", encoding="utf-8")
        updated, status = _apply_patch(text, str(old), str(new))
        if status == "ok":
            p.write_text(updated, encoding="utf-8")
        return status

    status = await asyncio.to_thread(_apply)
    if status == "ok":
        if raw.endswith(".md"):
            from kotoba.core import citations

            citations.note_markdown_write(ctx, raw)
        return f"Updated {raw}."
    if status == "notunique":
        note_tool_refusal(ctx)
        return (f"That snippet appears more than once in {raw}, so I didn't want to change the wrong spot — "
                "give me a longer, unique piece of text (include a line or two around it) and I'll patch it.")
    return None  # 'notfound' / 'empty' → graceful FAIL line ("couldn't find that part to change")
