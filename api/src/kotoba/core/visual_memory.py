"""Visual memory — Kotoba's DURABLE store of images she chose to remember (the visual twin of USER.md).

Not the transient work screenshots, which live in session_captures and the Files library: here she
keeps things worth remembering forever, each tied to an ENTITY plus a note, so "Alex Doe" can have
facts in USER.md AND photos here. Source-agnostic — a work screenshot, a data URL, a camera frame.
Jailed under KOTOBA_VISUAL_MEMORY_DIR: an index.json catalog beside images/<id>.<ext>, copied IN so
they persist independently of Files.

A keepsake is addressed by its `id` and nothing else: an opaque token minted here, so the route that
shows a recalled image never takes a path from its caller — the index filename is basenamed and rejailed."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path

from kotoba.core import atomic_file
from kotoba.paths import home_dir

log = logging.getLogger("kotoba")

_lock = threading.RLock()
_MAX_IMAGE_BYTES = 12_000_000
_MAX_ABOUT_CHARS = 120
_MAX_NOTE_CHARS = 300
_MAX_ENTRIES = 300
_KINDS = {"person", "self", "product", "post", "place", "scene", "other"}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# What may go back to a BROWSER as itself. The picker takes any image/*, so a keepsake can be an SVG — a
# scriptable document, served from our origin at a URL that carries the gate token in its query.
_SERVABLE_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
}


def memory_dir() -> Path:
    return Path(
        os.getenv("KOTOBA_VISUAL_MEMORY_DIR", str(home_dir() / "visual-memory"))
    ).expanduser()


def images_dir() -> Path:
    return memory_dir() / "images"


def _index_path() -> Path:
    return memory_dir() / "index.json"


def _normalize(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _load() -> list[dict]:
    p = _index_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        log.exception("failed to read visual-memory index %s", p)
        return []


def _save(entries: list[dict]) -> None:
    atomic_file.write_text(_index_path(), json.dumps(entries, ensure_ascii=False, indent=2))


@contextmanager
def _guard():
    """Both locks. The RLock keeps this process's threads honest; the file lock keeps a SECOND process
    (the CLI) from interleaving its own load-modify-save — which cost 72% of the index and left
    177 images on disk that nothing would ever read again."""
    with _lock, atomic_file.exclusive(_index_path()):
        yield


def _decode_data_url(data_url: str) -> tuple[bytes, str] | None:
    """(raw_bytes, ext) from a data: URL, or None. ext like 'png'/'jpg'."""
    if not data_url or not data_url.startswith("data:") or "," not in data_url:
        return None
    try:
        header, b64 = data_url.split(",", 1)
        raw = base64.b64decode(b64)
    except Exception:
        return None
    if not raw or len(raw) > _MAX_IMAGE_BYTES:
        return None
    m = re.search(r"image/([a-zA-Z0-9.+-]+)", header)
    ext = (m.group(1) if m else "png").lower().replace("jpeg", "jpg")
    return raw, ext


def _one_line(s: str, cap: int) -> str:
    """`about` and `note` are model-supplied while she is reading an attacker-controlled page; `about`
    is interpolated into a DEVELOPER block on every later turn (prompt_block) and `note` is echoed back
    by recall_image. Collapse to one bounded line so page text cannot open its own section there."""
    out = " ".join((s or "").split())
    return out[:cap].rstrip() if len(out) > cap else out


def duplicate_of(about: str, note: str, kind: str) -> dict | None:
    """The kept entry `add()` would refuse this one as a duplicate of, or None.

    `add()` answers None for five different things (exact duplicate, empty `about`, empty bytes, over the
    size cap, a failed write) — six through `add_data_url`, which adds an undecodable data URL and is the
    door remember_image comes through — and only ONE of them means the image is already kept. The
    dedup key lives here so a caller can ask which it was instead of guessing from the entity name —
    remember_image guessed by searching the name and told the model an image it had just refused was
    safely kept. Normalizes its arguments exactly as add() does, so both ask the same question."""
    about = _one_line(about, _MAX_ABOUT_CHARS)
    note = _one_line(note, _MAX_NOTE_CHARS)
    kind = (kind or "other").strip().lower()
    if kind not in _KINDS:
        kind = "other"
    if not about:
        return None
    key = (_normalize(about), note, kind)
    for e in _load():
        if (_normalize(e.get("about", "")), e.get("note", ""), e.get("kind", "")) == key:
            return e
    return None


def has_image(entry: dict | None) -> bool:
    """True when this entry's bytes are still readable on disk. "Kept" is a claim about the image, and an
    index row whose file is gone is not one (the same gap recall_image reports as "not attached")."""
    return bool(entry) and _entry_file(entry) is not None


def add(about: str, note: str, kind: str, raw: bytes, ext: str = "png", source: str = "") -> dict | None:
    """Persist an image into durable visual memory under `about`. Returns the stored entry, or None —
    including when the same about+note+kind is already kept, since saving the same thing twice is the
    common case and silent duplicates would accumulate forever and inflate the count the prompt reports.

    A duplicate is one only while its BYTES are there. The reporting half already knew that, and this
    half did not, so an index row whose file was gone vetoed the save that would have replaced it: a
    good image refused for good under an honest "it didn't save, ask them to send it again" that then
    failed identically every time, with nothing to reap the row. The ghost is REPLACED rather than left
    beside its successor — left in place it sorts first and would be met again on the next save."""
    about = _one_line(about, _MAX_ABOUT_CHARS)
    note = _one_line(note, _MAX_NOTE_CHARS)
    if not about or not raw or len(raw) > _MAX_IMAGE_BYTES:
        return None
    kind = (kind or "other").strip().lower()
    if kind not in _KINDS:
        kind = "other"
    ext = (ext or "png").lower().lstrip(".") or "png"
    with _guard():
        ghost = duplicate_of(about, note, kind)
        if ghost is not None and has_image(ghost):
            return None
        eid = uuid.uuid4().hex[:10]
        images_dir().mkdir(parents=True, exist_ok=True)
        fname = f"{eid}.{ext}"
        try:
            (images_dir() / fname).write_bytes(raw)
        except Exception:
            log.exception("visual memory: failed to write image")
            return None
        entry = {
            "id": eid, "file": fname, "about": about, "note": note,
            "kind": kind, "source": (source or "").strip(), "ts": time.time(),
        }
        entries = _load()
        if ghost is not None:
            entries = [e for e in entries if e.get("id") != ghost.get("id")]
        entries.append(entry)
        if len(entries) > _MAX_ENTRIES:
            # Oldest first. Unlike the file library (where the workdir holds the user's only originals),
            # every entry here is a keepsake COPY of something she chose to remember, so a bounded store
            # is correct — an unbounded one grows the index re-parsed on every turn.
            # Basenamed like every other reader of `file`: the index is written from model-supplied
            # data, and this was the one writer that joined it raw — a doctored row pruned outside.
            for stale in entries[: len(entries) - _MAX_ENTRIES]:
                try:
                    (images_dir() / Path(str(stale.get("file") or "")).name).unlink(missing_ok=True)
                except OSError:
                    pass
            entries = entries[len(entries) - _MAX_ENTRIES:]
        _save(entries)
        return entry


def add_data_url(about: str, note: str, kind: str, data_url: str, source: str = "") -> dict | None:
    dec = _decode_data_url(data_url)
    if dec is None:
        return None
    raw, ext = dec
    return add(about, note, kind, raw, ext, source)


def _keywords(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", _normalize(s)) if len(w) > 1}


def search(query: str, limit: int = 8) -> list[dict]:
    """Entries whose about/note/kind match the query (accent-insensitive keyword overlap), best first."""
    qk = _keywords(query)
    if not qk:
        return []
    scored: list[tuple[int, dict]] = []
    for e in _load():
        hay = _keywords(f"{e.get('about','')} {e.get('note','')} {e.get('kind','')}")
        inter = len(qk & hay)
        # also catch a direct substring of the `about` (e.g. full name) even if tokenization differs
        if inter or _normalize(query) in _normalize(e.get("about", "")):
            scored.append((inter + (5 if _normalize(query) in _normalize(e.get("about", "")) else 0), e))
    scored.sort(key=lambda t: (-t[0], -t[1].get("ts", 0)))
    return [e for _s, e in scored[:limit]]


def all_entries() -> list[dict]:
    return _load()


def about_list() -> list[tuple[str, int]]:
    """(about, count) per entity, most-remembered first — for the prompt summary.

    Grouped on the NORMALIZED name, keeping the first spelling seen: search() already matches
    accent- and case-insensitively, so grouping on the raw string listed "Fulano Pérez" and
    "fulano perez" as two entities of one that a single query then matched."""
    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    for e in _load():
        raw = e.get("about", "")
        if not raw:
            continue
        key = _normalize(raw)
        counts[key] = counts.get(key, 0) + 1
        display.setdefault(key, raw)
    return sorted(((display[k], n) for k, n in counts.items()), key=lambda t: (-t[1], t[0]))


def _entry_file(entry: dict) -> Path | None:
    """An entry's image on disk, or None. `file` comes from the index, which is written from model-supplied
    data, so it is basenamed and re-checked against the jail before anything reads it."""
    fname = entry.get("file") if isinstance(entry, dict) else None
    if not fname:
        return None
    root = images_dir().resolve()
    target = (root / Path(fname).name).resolve()
    if target != root and root not in target.parents:
        return None
    return target if target.is_file() else None


def find(entry_id: str) -> dict | None:
    """The keepsake with this id, or None. The id is the ONLY handle a caller may hold."""
    if not isinstance(entry_id, str) or not _ID_RE.match(entry_id):
        return None
    for e in _load():
        if e.get("id") == entry_id:
            return e
    return None


def servable_image(entry_id: str) -> tuple[Path, str] | None:
    """(file, media type) for a keepsake id — what GET /api/visual-memory/{id} hands the browser.
    None when the id is unknown or its bytes are gone. A non-raster keepsake gets octet-stream."""
    entry = find(entry_id)
    if entry is None:
        return None
    target = _entry_file(entry)
    if target is None:
        return None
    ext = target.suffix.lstrip(".").lower()
    return target, _SERVABLE_TYPES.get(ext, "application/octet-stream")


def image_data_url(entry: dict) -> str | None:
    """Read a stored entry's image back as a data: URL (to re-open it into the vision model)."""
    target = _entry_file(entry)
    if target is None:
        return None
    try:
        raw = target.read_bytes()
    except Exception:
        return None
    import mimetypes

    mt = mimetypes.guess_type(str(target))[0] or "image/png"
    return f"data:{mt};base64,{base64.b64encode(raw).decode()}"


def delete(entry_id: str) -> bool:
    with _guard():
        entries = _load()
        keep = [e for e in entries if e.get("id") != entry_id]
        if len(keep) == len(entries):
            return False
        for e in entries:
            if e.get("id") == entry_id and e.get("file"):
                try:
                    (images_dir() / Path(e["file"]).name).unlink(missing_ok=True)
                except Exception:
                    pass
        _save(keep)
        return True


def prompt_block(limit: int = 12) -> str:
    """A context block naming the entities Kotoba has visual memories of, so she knows what she can recall
    (recall_image) — e.g. she won't ask the user to send a photo she already keeps. '' when empty."""
    al = about_list()
    if not al:
        return ""
    items = ", ".join(f"{a} ({n})" for a, n in al[:limit])
    return (
        "VISUAL MEMORY (durable, yours) — images you've chosen to remember, by who/what they're about: "
        f"{items}. Use recall_image(query) to bring one back into view and answer about it (a person's face, "
        "the user's likes, a product, a post). Save a new one with remember_image when you see something "
        "worth keeping. An entity may also have textual facts — check memory_recall too."
    )
