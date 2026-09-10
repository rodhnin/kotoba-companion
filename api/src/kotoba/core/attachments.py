"""Pending user attachments per session (in-memory) and the images she has already been shown.

ElevenLabs' uploadFile does not work with a custom LLM, but WE own the model context — so the frontend
uploads to OUR backend, we stash it here, and load_context injects it as an `input_image`/`input_file`
content part on the next user turn, reaching the vision model directly.

Two stores. `_pending` is the one-shot queue load_context drains. `_shared` KEEPS the images, because
the model is shown base64 it can never quote back: with no handle `remember_image` had no source for
something just attached, so "save this picture" became a line of text while she said the image was
kept. Bounded by count AND bytes — the endpoint accepts 14 MB, so an unbounded keep is a leak."""
from __future__ import annotations

import time

# session_id -> list of content parts ready for the Responses API (input_image / input_file dicts).
_pending: dict[str, list[dict]] = {}
# session_id -> [{name, data_url, ts}] images shared this session, most recent LAST (least recent session first)
_shared: dict[str, list[dict]] = {}

MAX_PER_SESSION = 4
MAX_SHARED = 3
_MAX_SHARED_BYTES = 40_000_000
_MAX_NAME_CHARS = 60

# What the model may call the image it was just shown.
_REFS = {
    "attachment", "attachments", "attached", "attached image", "attached_image", "the attachment",
    "shared", "shared image", "user image", "user_image", "last", "last image", "last_image", "latest",
    "latest image", "this image", "that image", "the image", "image", "photo", "picture", "upload",
    "uploaded", "uploaded image", "camera roll", "it",
}


def add(session_id: str | None, part: dict, name: str = "") -> bool:
    """Stash one part for the next turn. True when it was kept, False when the cap refused it.

    The verdict IS the fix. This returned nothing, so the fifth file was dropped in silence and every
    front door reported success over it — and the two kinds fail differently: an image at least reached
    `_keep_shared`, while a PDF left no trace and she answered as if it had never been sent. The cap
    bounds ONE message, not the session, which is why the honest answer is "send this one first".

    A refused part is kept out of `_shared` too: the caller is about to say the file did not attach, and
    an image `resolve_image("attachment")` could still hand to `remember_image` contradicts that."""
    if not session_id or not part:
        return False
    bucket = _pending.setdefault(session_id, [])
    if len(bucket) >= MAX_PER_SESSION:
        return False
    bucket.append(part)
    url = part.get("image_url") if isinstance(part, dict) and part.get("type") == "input_image" else None
    if isinstance(url, str) and url:
        _keep_shared(session_id, name, url)
    return True


def take(session_id: str | None) -> list[dict]:
    """Return and CLEAR the pending parts for this session (consumed by the turn that uses them)."""
    if not session_id:
        return []
    return _pending.pop(session_id, [])


def has(session_id: str | None) -> bool:
    return bool(session_id) and session_id in _pending


def _clean_name(name: str, fallback: str) -> str:
    """One bounded line: the name comes from the client and is interpolated into a developer block."""
    out = " ".join((name or "").replace("\\", "/").split("/")[-1].split())
    return out[:_MAX_NAME_CHARS].strip() or fallback


def _keep_shared(session_id: str, name: str, data_url: str) -> None:
    lst = _shared.pop(session_id, [])
    lst = [e for e in lst if e["data_url"] != data_url]
    lst.append({"name": _clean_name(name, f"image-{len(lst) + 1}"), "data_url": data_url, "ts": time.time()})
    _shared[session_id] = lst[-MAX_SHARED:]
    _trim_shared()


def _trim_shared() -> None:
    """Bound the retained bytes, least-recently-used session first. The newest image is never evicted —
    it is the one "save this" refers to, and a single photo can exceed any budget on its own."""
    total = sum(len(e["data_url"]) for lst in _shared.values() for e in lst)
    while total > _MAX_SHARED_BYTES and _shared:
        newest = next(reversed(_shared))
        victim = next((s for s in _shared if s != newest or len(_shared[s]) > 1), None)
        if victim is None:
            return
        total -= len(_shared[victim].pop(0)["data_url"])
        if not _shared[victim]:
            _shared.pop(victim)


def shared_images(session_id: str | None) -> list[dict]:
    """Images the user shared this session, most recent last. Survives the turn that consumed them."""
    return list(_shared.get(session_id or "", []))


def mark_kept(session_id: str | None, name: str) -> None:
    """This picture is in visual memory now. It STAYS in the list — she may want to keep it again under
    another name, or point at it — but nothing should go on warning that it was never stored."""
    for entry in _shared.get(session_id or "", []):
        if entry.get("name") == name:
            entry["kept"] = True


def unkept_images(session_id: str | None) -> list[dict]:
    """The shared images she has NOT stored yet: what a warning about losing one may talk about."""
    return [e for e in _shared.get(session_id or "", []) if not e.get("kept")]


def resolve_image(session_id: str | None, ref: str) -> dict | None:
    """The shared image `ref` names — a keyword ('attachment'), the file's name, or nothing → the most
    recent one. None when this session was shown no image, or when `ref` looks like some other file, so
    the caller can go on to search the Files library."""
    imgs = shared_images(session_id)
    if not imgs:
        return None
    r = " ".join((ref or "").split()).strip().strip("\"'").lower()
    if not r or r in _REFS:
        return imgs[-1]
    base = r.replace("\\", "/").split("/")[-1]
    for e in reversed(imgs):
        low = e["name"].lower()
        if base == low or base == low.rsplit(".", 1)[0] or base in low:
            return e
    if any(k in r for k in ("attach", "shared", "just sent", "you sent", "upload")):
        return imgs[-1]
    return None


def prompt_note(session_id: str | None) -> str:
    """Developer block naming the images she's been shown and the ONE tool that actually keeps one. ''
    when this session has none."""
    imgs = shared_images(session_id)
    if not imgs:
        return ""
    names = ", ".join(e["name"] for e in imgs)
    return (
        f"IMAGES THE USER SHARED WITH YOU (this session): {names}. If they ask you to KEEP one — save it, "
        "remember it, don't lose it, \"it's yours now\" — call remember_image(source=\"attachment\", "
        "about=<who/what it is>, note=<what it shows>) RIGHT HERE in this turn; that stores the actual "
        "picture in your durable visual memory and recall_image brings it back later. You may pass the "
        "file's name as `source` instead. memory_write stores WORDS ONLY and keeps no picture: never say "
        "you saved the image after one, and never claim it's kept before remember_image has returned."
    )
