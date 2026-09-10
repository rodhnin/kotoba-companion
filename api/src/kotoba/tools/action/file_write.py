"""write_file — create/overwrite a file inside the jailed working dir."""
from __future__ import annotations

import asyncio

from kotoba.core import citations
from kotoba.core.path_security import PathSecurityError, validate_within_dir

SCHEMA = {
    "type": "function",
    "name": "write_file",
    "description": (
        "Create or overwrite a text file in your working folder with the given content. "
        "Parent folders are created as needed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the working folder."},
            "content": {"type": "string", "description": "Full file content to write."},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "file"
RISK = "write"

# Cap a single write so a runaway/abusive call can't fill the jail's disk. Generous for real source
# files; a model that needs more is doing something wrong.
_MAX_BYTES = 5 * 1024 * 1024

ANNOUNCE = "Writing that out now~"
HEARTBEAT = ["Putting it together...", "Almost saved..."]
COMPLETE = "Saved it!"
FAIL = "I couldn't write that file — let me try a different spot."
EXPRESSIONS = {"focus": "determined", "done": "happy", "fail": "embarrassed"}


async def execute(args: dict, ctx) -> str:
    raw = (args or {}).get("path", "").strip()
    content = (args or {}).get("content")
    if not raw or content is None:
        return None

    from kotoba.core.loop import note_tool_refusal

    data = str(content)
    if len(data.encode("utf-8", errors="replace")) > _MAX_BYTES:
        note_tool_refusal(ctx)
        return f"That file is too big to write here (limit is {_MAX_BYTES // (1024 * 1024)} MB)."

    # Host workdir (local / Docker bind-mount): write directly, jailed.
    if ctx.workdir is None:
        return None
    try:
        p = validate_within_dir(raw, ctx.workdir)
    except PathSecurityError:
        note_tool_refusal(ctx)
        return "That path is outside my workspace, so I won't write there."

    # Markdown reports get the URLs actually fetched this turn (ctx._sources) — but only the ones that
    # have arrived by now; citations.complete_reports() revisits the file at turn end for the rest.
    is_md = raw.endswith(".md")
    if is_md:
        completed = citations.append_sources(data, getattr(ctx, "_sources", None) or {})
        if completed is not None:
            data = completed

    def _write() -> int:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding="utf-8")
        return len(data)

    n = await asyncio.to_thread(_write)
    if is_md:
        citations.note_markdown_write(ctx, raw)
    return f"Wrote {n} characters to {raw}."
