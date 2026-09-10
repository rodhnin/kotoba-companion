"""search_files — find text across files in the jailed working dir (ripgrep, walk fallback).

The ripgrep path is skipped on `sandbox: none`, which is the one backend that promises no child
process on this machine. Keeping the promise costs nothing here because the walk fallback is a
complete second implementation the Docker image already depends on; it costs regex, which the schema
already warns about. `docker` deliberately keeps ripgrep: the whole file toolset reads the host
workdir directly on every backend (file_read does, file_write does), so the container was never this
tool's boundary — only `none` is a claim about processes rather than about isolation.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time

from kotoba.core.path_security import PathSecurityError, validate_within_dir

SCHEMA = {
    "type": "function",
    "name": "search_files",
    "description": "Search the working folder for a text pattern and return matching file:line results.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": (
                "Text to search for. Prefer a LITERAL substring: regex is honoured only on the "
                "ripgrep path, and where that is unavailable a regex simply finds nothing rather "
                "than erroring."
            )},
            "path": {"type": "string", "description": "Subfolder to search (default: whole workspace)."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "file"
RISK = "read"

ANNOUNCE = "Let me search through the files~"
HEARTBEAT = ["Looking everywhere...", "Almost done scanning..."]
COMPLETE = "Here's what I found:"
FAIL = "I couldn't find anything matching that."
EXPRESSIONS = {"focus": "thinking", "done": "happy", "fail": "confused"}

_MAX_RESULTS = 80

# The only file tool whose cost scales with the TREE, so the only one given a live-turn budget: 20s suits a
# background task, far too long mid-conversation. Measured ~5ms over the real library, so it rarely bites.
_WORK_BUDGET = 20.0
_VOICE_BUDGET = 5.0
_NARROW = "That search was taking too long — give me a subfolder or a more specific pattern and I'll retry."


def _capped(lines: list[str], total: str) -> str:
    """The first _MAX_RESULTS hits, and a line saying the list was cut.

    Both scans stopped at 80 and returned the 80 with nothing appended, so a query with 200 matches came
    back looking exactly like a query with 80 — the model then counts them, or reports the last file it
    can see as the last one there is. Same contract this module already keeps for a partial scan, where
    "No matches found." would be a lie; a partial LIST is the same lie one step further along."""
    return "\n".join(lines[:_MAX_RESULTS]) + (
        f"\n… (list cut at {_MAX_RESULTS} matches; {total} — narrow it with `path` or a longer pattern.)"
    )


def _budget(ctx) -> float:
    return _WORK_BUDGET if getattr(ctx, "mode", "companion") == "work" else _VOICE_BUDGET


async def execute(args: dict, ctx) -> str:
    query = (args or {}).get("query", "").strip()
    if not query:
        return None
    sub = (args or {}).get("path", "").strip() or "."

    from kotoba.core.loop import note_tool_refusal

    # Host workdir (local / Docker bind-mount).
    if ctx.workdir is None:
        return None
    try:
        root = validate_within_dir(sub, ctx.workdir)
    except PathSecurityError:
        note_tool_refusal(ctx)
        return "That folder is outside my workspace."
    if not root.exists():
        note_tool_refusal(ctx)
        return f"There's nothing at {sub} to search."

    from kotoba.core.sandbox import backend_name

    rg = shutil.which("rg") if backend_name() != "none" else None
    if rg:
        proc = await asyncio.create_subprocess_exec(
            rg, "--line-number", "--no-heading", "--color", "never", "-e", query, ".",
            cwd=str(root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=_budget(ctx))
        except asyncio.TimeoutError:
            proc.kill()
            return _NARROW
        except asyncio.CancelledError:
            # Cancelling a coroutine never kills the process it spawned: without this, "stop" left ripgrep
            # walking the library. Same shape the sandbox had (core/sandbox/local.run).
            proc.kill()
            raise
        lines = out.decode(errors="replace").splitlines()
        if not lines:
            return "No matches found."
        if len(lines) > _MAX_RESULTS:
            return _capped(lines, f"{len(lines) - _MAX_RESULTS} more were found and are not shown")
        return "\n".join(lines)

    # Fallback (no ripgrep): LITERAL substring search — deliberately not Python `re`, which would let
    # a catastrophic model-supplied pattern (e.g. "(a+)+$") ReDoS this worker thread with no way to
    # cancel it. ripgrep (the primary path) is the regex-capable, linear-time engine; the fallback
    # trades regex power for safety. A wall-clock deadline bounds very large trees too.
    needle = query
    deadline = time.monotonic() + _budget(ctx)

    root_real = os.path.realpath(root)

    def _in_jail(path: str) -> bool:
        """os.walk yields symlinks and open() follows them, so the fallback read OUT of the jail — it
        returned a private key that read_file correctly refuses. ripgrep (the primary path) skips symlinks
        by default, which is why the two disagreed; and the Docker image ships no ripgrep, so the fallback
        IS the deployed path there."""
        real = os.path.realpath(path)
        return real == root_real or real.startswith(root_real + os.sep)

    def _walk() -> str:
        hits: list[str] = []
        timed_out = False
        for dirpath, dirs, files in os.walk(root):
            if time.monotonic() > deadline:
                timed_out = True
                break
            dirs[:] = [d for d in dirs if _in_jail(os.path.join(dirpath, d))]
            for fn in files:
                fp = os.path.join(dirpath, fn)
                if not _in_jail(fp):
                    continue
                try:
                    with open(fp, "r", errors="replace", encoding="utf-8") as fh:
                        for i, line in enumerate(fh, 1):
                            if needle in line:
                                rel = os.path.relpath(fp, root)
                                hits.append(f"{rel}:{i}:{line.rstrip()}")
                                if len(hits) >= _MAX_RESULTS:
                                    # The walk STOPS here, so the rest was never counted — say that,
                                    # rather than the number the rg path can give.
                                    return _capped(hits, "the scan stopped there, so there may be more")
                except (OSError, UnicodeError):
                    continue
            if time.monotonic() > deadline:
                timed_out = True
                break
        if hits:
            return "\n".join(hits)
        # "No matches found." after a partial scan would be a lie — same contract as the rg path.
        return _NARROW if timed_out else "No matches found."

    return await asyncio.to_thread(_walk)
