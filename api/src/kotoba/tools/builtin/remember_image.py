"""remember_image — save an image into Kotoba's DURABLE visual memory (the visual twin of memory_write).

For something worth remembering long-term, tied to who or what it is about; the image is COPIED into
her own store, separate from the work Files. `source` resolves 'attachment' (the commonest case — she
is shown base64 she cannot quote back), an http(s) URL (SSRF-guarded, WORK mode only, since a fetch
inside a voice turn stalls the call), a filename, a data: URL, or 'camera'.

A save is reported from the STORE's answer, never from a search. `add()` returns None for five things
and only the exact duplicate means the image is kept; asking whether any entry existed under that name
announced a too-big image as kept. Anything else is witnessed as a save that did NOT happen."""
from __future__ import annotations

import asyncio
import mimetypes

_MAX_FETCH_BYTES = 12_000_000
_IMG_CT = ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif")

SCHEMA = {
    "type": "function",
    "name": "remember_image",
    "description": (
        "KEEP AN IMAGE. This is the ONLY way to save a picture — memory_write saves words and keeps no "
        "image, so never use it for \"save/remember this photo\". Use this whenever the user SENDS you an "
        "image and asks you to keep it, remember it, or not lose it (their avatar, their pet, a person, a "
        "product): pass source=\"attachment\" and it saves the exact image they just shared. Also for "
        "anything else worth remembering long-term that you can see. `source`: \"attachment\" (the image "
        "the user shared with you), OR the image's direct URL (a profile photo's CDN link — saves the clean "
        "ORIGINAL; only while working, not mid-call), OR a screenshot filename from your Files, OR a data: "
        "URL. `about`: the entity it's about (a person's name, 'the user', a product) — reuse the SAME name "
        "you'd use in memory_write so facts and photos link up. `note`: a short description ('his profile "
        "photo; he looks like…', 'the user's chibi: white bow, neutral face'). `kind`: person | self | "
        "product | post | place | scene | other."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "'attachment' for the image the user shared with "
                       "you, or a filename, or a data: URL, or an image URL (work mode). ('camera' is NOT available yet — never offer it.)"},
            "about": {"type": "string", "description": "Who/what the image is about (entity name)."},
            "note": {"type": "string", "description": "Short description of what it shows / why it matters."},
            "kind": {"type": "string", "enum": ["person", "self", "product", "post", "place", "scene", "other"]},
        },
        "required": ["source", "about"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "memory"   # app-safe write to her own store; available in companion + work
RISK = "write"

ANNOUNCE = "Oh, I want to remember this — let me keep it."
HEARTBEAT: list[str] = []
COMPLETE = "Saved to my visual memory~"
FAIL = "I couldn't keep that image just now."
EXPRESSIONS = {"focus": "determined", "done": "happy", "fail": "embarrassed"}


def _bytes_from_files(name: str) -> tuple[bytes, str] | None:
    """Read a capture from the Files library → (raw, ext). Reuses view_capture's resolver so it finds
    captures in SUBDIRS (screenshots/<source>/…), not just the library root — the root-only lookup here
    meant remember_image could NEVER resolve a screenshot (they're all saved under screenshots/)."""
    from kotoba.core import file_library
    from kotoba.tools.builtin.view_capture import _resolve_in_library

    target = _resolve_in_library(name)
    if target is None:
        return None
    try:
        raw = target.read_bytes()
    except Exception:
        return None
    if not file_library.media_type_for(target).startswith("image/"):
        return None
    ext = target.suffix.lstrip(".").lower() or "png"
    return raw, ext


def _dethumbnail(url: str) -> str | None:
    """A higher-res candidate of an image CDN URL by dropping resize/blur transforms — e.g. Facebook's
    `stp=dst-jpg_fb50_s320x320` (a 320px BLURRED thumbnail). Returns the stripped URL, or None if there's
    no transform to strip. Best-effort: the caller tries this first and falls back to the original."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if k.lower() != "stp"]  # `stp` = the FB resize/blur transform
    if len(kept) == len(pairs):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


async def _fetch_one(client, url: str):
    """One SSRF-checked GET (manual redirects) → (raw, ext) if it's a usable image, else None."""
    from kotoba.core.ssrf import check_redirect, url_block_reason

    if url_block_reason(url):
        return None
    headers = {"User-Agent": "Mozilla/5.0 (KotobaBot)"}
    current = url
    for _ in range(5):
        r = await client.get(current, headers=headers)
        if r.status_code in (301, 302, 303, 307, 308) and "location" in r.headers:
            target, reason = check_redirect(current, r.headers["location"])
            if reason:
                return None
            current = target
            continue
        break
    else:
        return None
    if r.status_code >= 400:
        return None
    ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    raw = r.content
    if not raw or len(raw) > _MAX_FETCH_BYTES:
        return None
    if ct not in _IMG_CT and raw[:3] not in (b"\xff\xd8\xff", b"\x89PN"[:3]):
        if not any(url.lower().split("?")[0].endswith(e) for e in (".png", ".jpg", ".jpeg", ".webp", ".gif")):
            return None
    ext = (mimetypes.guess_extension(ct) or "").lstrip(".") or "jpg"
    return (raw, "jpg" if ext == "jpe" else ext)


async def _fetch_image(url: str):
    """Download an image URL into (raw_bytes, ext), SSRF-guarded + size-capped. Tries the FULL-RES version
    first (drops a CDN resize/blur transform like FB's `stp=...fb50_s320x320`, which gives a tiny blurred
    thumbnail), then falls back to the original URL. Prefers the LARGER result so we keep a clean photo, not
    a blurred avatar. None if blocked / not an image."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as c:
            best = None
            stripped = _dethumbnail(url)
            for cand in ([stripped, url] if stripped else [url]):
                got = await _fetch_one(c, cand)
                if got and (best is None or len(got[0]) > len(best[0])):
                    best = got
            return best
    except Exception:
        return None


async def _not_saved(about: str, note: str, kind: str, ctx) -> str:
    """What to tell the model when add() answered None — the ONE reason that means "already kept", or a
    plain "nothing was saved". Only the first is a success; the other leaves a RAN-AND-FAILED witness.

    Not a refusal, which is the witness this used to leave: `refused` means nothing ran, so it drops the
    audit row and draws ⊘ under a sentence about somebody's decision. Nobody decided anything here — the
    bytes were in hand (fetched over the network, in the URL case) and the store was asked to keep them
    and could not. `visual_memory.add` can also fail with the image file already written, so there is a
    trace, and a trace is precisely what `failed` keeps and `refused` throws away."""
    from kotoba.core import visual_memory

    dup = await asyncio.to_thread(visual_memory.duplicate_of, about, note, kind)
    if dup is not None and await asyncio.to_thread(visual_memory.has_image, dup):
        return (f"Nothing new was saved: you ALREADY keep that exact image under '{about}'"
                + (f" ({dup.get('note')})" if dup.get("note") else "")
                + " — same note, same kind. It IS kept and recall_image brings it back.")
    from kotoba.core.loop import note_tool_failure

    note_tool_failure(f"visual memory would not keep the image for '{about}'")
    return ("NOTHING WAS SAVED — I could not keep that image. It may be too big for my store, a format I "
            "can't read, or the write failed. Do NOT tell the user it's kept or that you can recall it "
            "later: say plainly that it didn't save, and ask them to send it again if they want to retry.")


async def execute(args: dict, ctx) -> str:
    from kotoba.core import attachments, visual_memory

    args = args or {}
    source = (args.get("source") or "").strip()
    about = (args.get("about") or "").strip()
    note = (args.get("note") or "").strip()
    kind = (args.get("kind") or "other").strip()
    from kotoba.core.loop import note_tool_failure, note_tool_refusal

    session_id = getattr(ctx, "session_id", None)
    if not about:
        return None

    if source.lower() == "camera":
        note_tool_refusal(ctx)
        return ("I can't grab a camera frame yet — that's coming. For now point me at a saved capture "
                "(a screenshot filename) or take one first, then I'll remember it.")

    # The image the user handed her wins over every other reading of `source` — it's in hand, and a
    # filename she half-remembers must not send us hunting the Files library instead.
    shared = None
    if not source.startswith(("data:", "http://", "https://")):
        shared = attachments.resolve_image(session_id, source)
    if shared is not None:
        entry = await asyncio.to_thread(
            visual_memory.add_data_url, about, note, kind, shared["data_url"], f"attachment:{shared['name']}"
        )
        if entry is None:
            return await _not_saved(about, note, kind, ctx)
        attachments.mark_kept(session_id, shared["name"])
        return (f"KEPT THE IMAGE the user shared ({shared['name']}) in your durable visual memory under "
                f"'{about}'" + (f": {note}" if note else "") + ". You can bring it back with recall_image.")

    if not source:
        note_tool_refusal(ctx)
        return ("I don't have an image in hand to keep. If the user just sent one, ask them to share it "
                "again; otherwise give me a filename or a link.")

    entry = None
    if source.startswith("data:"):
        # Cap the decoded size like the URL fetch path — a giant data: URL would blow memory / the store.
        # Estimate bytes from the base64 payload length (×3/4) without decoding the whole thing twice.
        _b64 = source.split(",", 1)[1] if "," in source else ""
        if (len(_b64) * 3) // 4 > _MAX_FETCH_BYTES:
            note_tool_refusal(ctx)
            return "That image is too big to keep (over my size limit). Try a smaller one or give me a link."
        entry = await asyncio.to_thread(visual_memory.add_data_url, about, note, kind, source, "data-url")
    elif source.startswith(("http://", "https://")):
        # A fetch inside the voice turn is the stall this tool was made work-only for; an image already
        # in hand (attachment / data URL / a saved capture) is instant, so only THIS path is refused.
        if getattr(ctx, "mode", "companion") != "work":
            note_tool_refusal(ctx)
            return ("I can't go and fetch an image off the web in the middle of our call. Ask me to get "
                    "to work on it and I'll open the page and keep the real image from there.")
        # Download the ORIGINAL image (e.g. a CDN profile photo) — clean, not a viewport screenshot.
        fetched = await _fetch_image(source)
        if fetched is None:
            note_tool_failure(f"no usable image came back from {source[:80]}")
            return (f"I couldn't fetch a usable image from that URL. If you have it on screen, take a "
                    f"browser_take_screenshot and remember THAT file instead.")
        raw, ext = fetched
        entry = await asyncio.to_thread(visual_memory.add, about, note, kind, raw, ext, source)
    else:
        loaded = await asyncio.to_thread(_bytes_from_files, source)
        if loaded is None:
            names = [e["name"] for e in attachments.shared_images(session_id)]
            hint = (f" The image the user shared with you is '{names[-1]}' — call me again with "
                    "source=\"attachment\" to keep THAT one.") if names else ""
            note_tool_refusal(ctx)
            return f"I couldn't find a capture called '{source}' to remember — give me the exact filename.{hint}"
        raw, ext = loaded
        entry = await asyncio.to_thread(visual_memory.add, about, note, kind, raw, ext, source)

    if entry is None:
        return await _not_saved(about, note, kind, ctx)
    return f"Saved to my visual memory under '{about}'" + (f": {note}" if note else "") + "."
