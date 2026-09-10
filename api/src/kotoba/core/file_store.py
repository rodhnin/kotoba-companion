"""In-memory snapshot of the TEXT files Kotoba touched this session — used to REHYDRATE a fresh sandbox so
cross-turn edits survive a sandbox recreate/reap (`text_items()`). It is NOT the Files panel's source: the
durable, browsable copy of every file lives in the on-disk library (core/file_library, ~/.kotoba/files).
We stash content at write time (capped), keyed by session + path.

Images are stashed too (`stash_image`), but NOTHING READS THEM BACK: `text_items` filters to text, and
`get`/`get_entry` have no production caller. It is a write-only sink today — bounded per session by
_MAX_PER_SESSION/_MAX_IMAGE, and never freed, because `clear()` has no caller either. The persistent home
for images is the on-disk library; the panel is served from there, not from here.
"""
from __future__ import annotations

_MAX_FILE = 200_000        # don't stash a text file bigger than ~200 KB
_MAX_IMAGE = 12_000_000    # ~12 MB data URL cap for an image
_MAX_PER_SESSION = 60      # cap how many files we remember per session

# session_id -> {path: {"kind": "text"|"image", "content": str}}
_files: dict[str, dict[str, dict]] = {}


def stash(session_id: str | None, path: str, content: str | None) -> None:
    """Stash a TEXT file's content (back-compat signature)."""
    if not session_id or not path or content is None:
        return
    if len(content) > _MAX_FILE:
        content = content[:_MAX_FILE] + "\n… (truncated)"
    bucket = _files.setdefault(session_id, {})
    if path not in bucket and len(bucket) >= _MAX_PER_SESSION:
        return
    bucket[path] = {"kind": "text", "content": content}


def stash_image(session_id: str | None, path: str, data_url: str | None) -> None:
    """Stash an image as a data URL (e.g. a browser screenshot). The Files panel does NOT read this —
    it is served from the on-disk library — and nothing else reads it either (module docstring)."""
    if not session_id or not path or not data_url:
        return
    if len(data_url) > _MAX_IMAGE:
        return
    bucket = _files.setdefault(session_id, {})
    if path not in bucket and len(bucket) >= _MAX_PER_SESSION:
        return
    bucket[path] = {"kind": "image", "content": data_url}


def get(session_id: str | None, path: str) -> str | None:
    """Text content for a stashed text file (back-compat). Images → use get_entry."""
    e = get_entry(session_id, path)
    return e["content"] if e and e["kind"] == "text" else None


def get_entry(session_id: str | None, path: str) -> dict | None:
    if not session_id:
        return None
    return _files.get(session_id, {}).get(path)


def text_items(session_id: str | None) -> list[tuple[str, str]]:
    """(path, content) for every stashed TEXT file — used to rehydrate a fresh sandbox."""
    if not session_id:
        return []
    return [(p, e["content"]) for p, e in _files.get(session_id, {}).items() if e["kind"] == "text"]


def clear(session_id: str | None) -> None:
    if session_id:
        _files.pop(session_id, None)
