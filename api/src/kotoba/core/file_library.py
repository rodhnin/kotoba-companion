"""Kotoba's persistent file LIBRARY on disk — the durable home of every file she creates or edits.

Lives at ~/.kotoba/files (override KOTOBA_FILES_DIR), OUTSIDE the repo and independent of any session,
so the Files panel always shows everything across turns, restarts and reloads. Text goes under its
relative path, images are decoded to real bytes, and a `.index.json` sidecar records new/edited, which
the filesystem cannot tell apart. Paths are jailed and writes size-capped. Pruning by file count runs
ONLY when the library mirrors a separate workdir; by default the library IS the workdir, so never.

`library_dir()` is RESOLVED on purpose: unresolved, any symlink component makes relative_to raise and
`_rel_of` falls back to the basename, so writers index "x.html" while readers look up "reports/x.html"."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from contextlib import contextmanager
from pathlib import Path

from kotoba.core import atomic_file
from typing import Optional

from kotoba.core.path_security import PathSecurityError, validate_within_dir
from kotoba.paths import home_dir

_MAX_TEXT = 200_000
_MAX_IMAGE_BYTES = 12_000_000
_MAX_FILES = 500
_INDEX_NAME = ".index.json"
_LOCK_NAME = _INDEX_NAME + ".lock"   # the index's mutual-exclusion sidecar — never content


def library_dir() -> Path:
    return Path(
        os.getenv("KOTOBA_FILES_DIR", str(home_dir() / "files"))
    ).expanduser().resolve()


def _library_is_workdir() -> bool:
    """True in the DEFAULT setup, where tools work directly in the library instead of a mirror."""
    from kotoba.core.workspace import resolve_workdir

    try:
        return resolve_workdir(None).resolve() == library_dir().resolve()
    except Exception:
        return True  # unknown → treat the files as originals, never as a disposable cache


def _ensure_dir() -> Path:
    d = library_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return library_dir() / _INDEX_NAME


def _load_index() -> dict:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(idx: dict) -> None:
    try:
        atomic_file.write_text(_index_path(), json.dumps(idx, ensure_ascii=False))
    except Exception:
        pass


@contextmanager
def _index_update():
    """Load-modify-save under a CROSS-PROCESS lock, yielding the index to mutate.

    There was no lock at all, and `_save_index` swallows every exception — so two writers simply lost
    each other's updates with nothing in any log. Measured with two processes: 79% of index updates
    gone. Only metadata (the new/edited badge, created_at, `seen`) — no file ever disappears, since
    list_all() reads the directory — which is exactly why nobody would have noticed."""
    with atomic_file.exclusive(_index_path()):
        idx = _load_index()
        yield idx
        _save_index(idx)


def _safe_target(rel_path: str) -> Optional[Path]:
    """Resolve a model-supplied path to a real file inside the library, jailed (no traversal). Falls back
    to the basename when the path escapes the jail, so a file is never lost OR written out."""
    d = _ensure_dir()
    rel = (rel_path or "").strip().lstrip("/")
    if not rel:
        return None
    try:
        return validate_within_dir(rel, d)
    except PathSecurityError:
        # Jail the fallback too: `d / name` unchecked hands back the very symlink just rejected.
        name = Path(rel).name
        if not name:
            return None
        try:
            return validate_within_dir(name, d)
        except PathSecurityError:
            return None


def _mark(idx: dict, rel: str, kind: str, now: float) -> str:
    """Stamp one file's row in an ALREADY-OPEN index, and say which tag it now wears. Split out so a
    batch (note_changes) can stamp many under one lock and one write."""
    entry = idx.get(rel)
    if entry is None:
        idx[rel] = {"action": "created", "kind": kind, "created_at": now, "updated_at": now, "seen": False}
    else:
        entry["action"] = "edited"
        entry["kind"] = kind
        entry["updated_at"] = now
        entry["seen"] = False  # a fresh write → un-see it so 'edited' shows again until reopened
    return idx[rel]["action"]


def _touch_index(rel: str, kind: str) -> None:
    with _index_update() as idx:
        _mark(idx, rel, kind, time.time())


def _rel_of(target: Path) -> str:
    """A library-relative name is an IDENTITY, not a local path: it keys the index, rides a URL, and is
    what the model is shown and hands back. Built with str() it came out backslashed on Windows, so the
    same file had two names — one the writer stored and one every reader spelled."""
    try:
        return target.relative_to(library_dir()).as_posix()
    except Exception:
        return target.name


def mark_seen(rel_path: str) -> bool:
    """Mark a file opened/seen so the panel drops its new/edited tag. Creates the index entry if it didn't
    have one (a file made/moved by raw shell never went through _touch_index) so the 'seen' STICKS across
    reloads. Persisted in the sidecar."""
    # Jailed and required to EXIST: reachable from POST /api/files/seen, so a raw string would let
    # '../../x' become a permanent index key and answer ok:true for a file that is not there.
    target = _safe_target(rel_path)
    if target is None or not target.is_file():
        return False
    rel = _rel_of(target)
    with _index_update() as idx:
        entry = idx.get(rel)
        if entry is None:
            now = time.time()
            entry = idx[rel] = {"action": "created", "kind": "text", "created_at": now, "updated_at": now}
        entry["seen"] = True
    return True


def note_changes(workdir, prev_mtimes: dict) -> list[tuple[str, str]]:
    """When the workdir IS the library, files made/moved by raw shell are already on disk — they don't need
    copying, but they DO need an index entry so they show their new/edited tag (and can later be marked
    seen). Stamp every file changed since `prev_mtimes`; return [(rel, action)]. Skips junk.

    ONE lock and ONE write for the whole batch. It used to `_touch_index` per file — flock, full read,
    full `json.dumps`, fsync, rename — and then `_load_index()` again just to read the tag back, which
    is quadratic in a directory that is, by default, the user's entire workspace. `git clone` or
    `npm install` inside it is exactly this call: measured 3.4s at 1000 files, 13.4s at 2000, in a
    thread that runs AFTER the tool returned, so no timeout, heartbeat or spinner covers the freeze.
    `_prune` was hardened for this same scenario; this was its other half."""
    out: list[tuple[str, str]] = []
    changed: list[tuple[str, str]] = []
    try:
        base = Path(workdir)
        for p in base.rglob("*"):
            if not p.is_file() or p.name in _SKIP_NAMES or p.name.startswith("."):
                continue
            try:
                rel = p.relative_to(base).as_posix()
                mt = p.stat().st_mtime
            except Exception:
                continue
            if prev_mtimes.get(rel) == mt:
                continue
            changed.append((rel, "image" if p.suffix.lower() in _IMG_EXT else "text"))
        if changed:
            now = time.time()
            with _index_update() as idx:
                out = [(rel, _mark(idx, rel, kind, now)) for rel, kind in changed]
    except Exception:
        pass
    return out


def touch(rel_path: str, kind: str = "text") -> None:
    """Update the panel index for a file already on disk without rewriting its bytes.

    Used when the workdir IS the library (default setup): the tool already wrote the correct bytes;
    the mirror should only update the 'new/edited' tag, never overwrite the file and risk hitting
    _MAX_TEXT truncation."""
    target = _safe_target(rel_path)
    if target is None:
        return
    rel = _rel_of(target)
    _touch_index(rel, kind)


def save_text(rel_path: str, content: Optional[str]) -> Optional[str]:
    """Write a text/code file into the library (truncated if huge). Returns the stored relative path."""
    if content is None:
        return None
    target = _safe_target(rel_path)
    if target is None:
        return None
    if len(content) > _MAX_TEXT:
        content = content[:_MAX_TEXT] + "\n… (truncated)"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, errors="replace", encoding="utf-8")
    except Exception:
        return None
    rel = _rel_of(target)
    _touch_index(rel, "text")
    _prune()
    return rel


def save_image(rel_path: str, data_url: Optional[str]) -> Optional[str]:
    """Decode a data: URL (e.g. a screenshot) to real image bytes in the library. Returns the stored path."""
    if not data_url or not data_url.startswith("data:") or "," not in data_url:
        return None
    target = _safe_target(rel_path)
    if target is None:
        return None
    try:
        b64 = data_url.split(",", 1)[1]
        raw = base64.b64decode(b64)
    except Exception:
        return None
    if len(raw) > _MAX_IMAGE_BYTES:
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    except Exception:
        return None
    rel = _rel_of(target)
    _touch_index(rel, "image")
    _prune()
    return rel


_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}


def media_type_for(path: Path) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        return mt
    # Serve unknown/extensionless files as text so the viewer/new-tab shows source, not a download prompt.
    return "text/plain; charset=utf-8"


def _is_sidecar(p: Path) -> bool:
    """Is this the index or its lock? Asked of the RESOLVED path, which is the only spelling that
    settles it: `./.index.json`, `sub/../.index.json` and a symlink pointing at the index are all the
    same file, and only one of them looks like it."""
    return p.name in (_INDEX_NAME, _LOCK_NAME)


def _jailed(rel_path: str) -> Optional[Path]:
    """The library file `rel_path` names — inside the jail, and never a sidecar. None otherwise.

    ONE gate for both readers, because they had two and they did not agree. `resolve` compared
    `Path(rel).name`; `delete` compared the raw string it was handed, so a single leading `./` walked
    past it — and getting past it did not cost one file: `_index_update` re-reads the index it has just
    unlinked, `_load_index` answers `{}` on failure, and the whole thing is written back empty, so every
    file's new/edited/seen state goes with it. `./.index.json.lock` took the mutual-exclusion sidecar two
    processes coordinate through. In the default setup the workdir IS the library, so the model reaches
    these files without going near the HTTP route. The check now runs AFTER validate_within_dir, so it
    is the name of the file that will actually be opened."""
    rel = (rel_path or "").strip().lstrip("/")
    if not rel:
        return None
    try:
        p = validate_within_dir(rel, library_dir())
    except PathSecurityError:
        return None
    return None if _is_sidecar(p) else p


def resolve(rel_path: str) -> Optional[Path]:
    """Jailed absolute path for serving a library file, or None if it escapes the jail / is missing.
    Sidecars are not content: never served, never deletable, never pruned."""
    p = _jailed(rel_path)
    return p if p is not None and p.is_file() else None


def delete(rel_path: str) -> bool:
    """Delete a library file (jailed) + drop its .index.json entry. Returns True if a file was removed.
    Refuses traversal/escape and the index sidecars (`_jailed`)."""
    p = _jailed(rel_path)
    if p is None:
        return False
    removed = False
    try:
        if p.is_file():
            p.unlink()
            removed = True
    except OSError:
        return False
    with _index_update() as idx:
        # The key the index is written under, not the spelling the caller used: `./notes.txt` unlinked
        # the file and left its row standing, listing something that is gone.
        idx.pop(_rel_of(p), None)
    try:
        parent = p.parent
        if parent != library_dir() and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
    return removed


def list_all() -> list[dict]:
    """The library's visible files, newest-first, with new/edited tags + timestamps (session-independent)."""
    d = library_dir()
    if not d.exists():
        return []
    idx = _load_index()
    out: list[dict] = []
    for p in d.rglob("*"):
        if not p.is_file() or p.name in (_INDEX_NAME, _LOCK_NAME) or p.name in _SKIP_NAMES or p.name.startswith("."):
            continue
        rel = _rel_of(p)
        meta = idx.get(rel, {})
        try:
            st = p.stat()
            mtime = st.st_mtime
            size = st.st_size
        except Exception:
            mtime, size = 0.0, 0
        kind = "image" if p.suffix.lower() in _IMG_EXT else meta.get("kind", "text")
        out.append({
            "path": rel,
            "name": p.name,
            "kind": kind,
            "action": meta.get("action", "created"),
            # No entry → a pre-existing file with no "new" signal → already seen, so the panel does not flare it.
            "seen": bool(meta.get("seen", True)),
            "size": size,
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at", mtime),
        })
    out.sort(key=lambda f: f.get("updated_at") or 0, reverse=True)
    return out


def _prune() -> None:
    """Keep at most _MAX_FILES files; delete the oldest (by mtime) beyond the cap. Best-effort.

    Only ever runs when the library is a MIRROR of a separate workdir, where every file is a copy and
    the cap bounds a cache. In the default setup the library IS the working directory, so these are the
    user's only originals — a `git clone` there would push the count over the cap and pruning would
    delete their work. Nothing in the library is safe to delete on a file COUNT."""
    if _library_is_workdir():
        return
    d = library_dir()
    try:
        files = [p for p in d.rglob("*") if p.is_file() and p.name not in (_INDEX_NAME, _LOCK_NAME)]
    except Exception:
        return
    if len(files) <= _MAX_FILES:
        return
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
    with _index_update() as idx:
        for p in files[: len(files) - _MAX_FILES]:
            try:
                rel = _rel_of(p)
                p.unlink(missing_ok=True)
                idx.pop(rel, None)
            except Exception:
                pass


def snapshot_mtimes(workdir) -> dict:
    """{rel_path: mtime} for the files currently in a working dir — a baseline so import_workdir can tell
    which files a shell/execute_code command actually created or changed."""
    out: dict[str, float] = {}
    try:
        base = Path(workdir)
        for p in base.rglob("*"):
            if p.is_file():
                try:
                    out[p.relative_to(base).as_posix()] = p.stat().st_mtime
                except Exception:
                    pass
    except Exception:
        pass
    return out


_SKIP_NAMES = {"_run.py"}


def import_workdir(workdir, prev_mtimes: dict) -> list[tuple[str, str]]:
    """Adopt into the library every file in `workdir` that is NEW or CHANGED since `prev_mtimes` — so a
    file Kotoba makes with `shell`/`execute_code` (which writes to the sandbox workdir, not the library)
    still shows up in the Files panel, exactly like write_file does. Returns [(rel, action)] for each
    imported file (action 'created'|'edited' from the library index). Best-effort, capped, skips junk."""
    imported: list[tuple[str, str]] = []
    try:
        base = Path(workdir)
        if not base.exists():
            return imported
        base_real = base.resolve()
        for p in base.rglob("*"):
            if not p.is_file() or p.name in _SKIP_NAMES or p.name.startswith("."):
                continue
            # rglob yields symlinks and the reads follow them, so a link planted in the workdir would be
            # copied INTO the library and then served by /api/files/raw.
            try:
                if not p.resolve().is_relative_to(base_real):
                    continue
            except OSError:
                continue
            try:
                rel = p.relative_to(base).as_posix()
                mt = p.stat().st_mtime
            except Exception:
                continue
            if prev_mtimes.get(rel) == mt:
                continue
            ext = p.suffix.lower()
            saved: Optional[str] = None
            if ext in _IMG_EXT:
                try:
                    raw = p.read_bytes()
                except Exception:
                    continue
                if len(raw) > _MAX_IMAGE_BYTES:
                    continue
                tgt = _safe_target(rel)
                if tgt is None:
                    continue
                try:
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    tgt.write_bytes(raw)
                except Exception:
                    continue
                saved = _rel_of(tgt)
                _touch_index(saved, "image")
            else:
                try:
                    text = p.read_text(errors="replace", encoding="utf-8")
                except Exception:
                    continue
                saved = save_text(rel, text)
            if saved:
                action = _load_index().get(saved, {}).get("action", "created")
                imported.append((saved, action))
        if imported:
            _prune()
    except Exception:
        pass
    return imported


def clear() -> None:
    """Wipe the whole library (used by tests). Best-effort."""
    import shutil

    try:
        shutil.rmtree(library_dir(), ignore_errors=True)
    except Exception:
        pass
