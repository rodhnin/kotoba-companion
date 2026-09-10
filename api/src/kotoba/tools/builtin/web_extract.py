"""web_extract — fetch a URL and return readable text (httpx + readability-lxml)."""
from __future__ import annotations

import logging

import asyncio

log = logging.getLogger("kotoba.tools")

SCHEMA = {
    "type": "function",
    "name": "web_extract",
    "description": (
        "Read the full text of ONE specific web page. Use it whenever a real http(s) link is in play: "
        "one the user just gave you, or one they gave a moment ago and are now asking you to open "
        "(\"ábrelo\", \"read it\", \"what does it say\") — reading the link they handed you beats "
        "searching for it. Do NOT use it to search or to answer general questions (web_search does "
        "that), and never invent a URL to pass here."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full http(s) URL the user provided."}
        },
        "required": ["url"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "web"
RISK = "read"

ANNOUNCE = "Let me actually read that page for you."
HEARTBEAT = ["There's quite a bit here...", "Reading through it...", "Pulling out the part that matters..."]
COMPLETE = "Okay — so what this page actually says is:"
FAIL = "That page didn't load properly. I can search for it another way instead."

_MAX_CHARS = 6000

_THIN_CHARS = 600

_PLACEHOLDER_SIGNALS = (
    "this domain is for use in illustrative examples",
    "buy this domain",
    "domain is parked",
    "domain may be for sale",
    "under construction",
)


def _cut_note(text: str) -> str:
    """Say when the page was longer than the budget. The schema calls this "the full text", and a
    silent cut is what lets the model assert a page "doesn't mention X" off its first 6000 chars.

    A note rather than a suffix: the page comes back quoted, and a sentence appended inside those
    markers reads as the page admitting it was cut — which the page never said."""
    if len(text) <= _MAX_CHARS:
        return ""
    return (f"Page truncated here — {len(text) - _MAX_CHARS} more characters were not returned. "
            "Do not conclude anything is absent from the page on the strength of this excerpt.")


def _thin_note(text: str) -> str:
    """A thin SUCCESS must arrive as a fact, not a bare string the model has to interpret.

    Measured cross-model on the same turn: this tool returned example.com's 203 real characters and BOTH
    gpt-5.4-mini and grok-4.6 opened with "no pude abrir la página" — then described the page correctly.
    Three prompt rules against that family moved nothing, and the Grok run refuted the training-prior
    theory: what both models do is interpret a bare thin result as an access failure. So the fix is in
    what the TOOL hands back — the fetch outcome and the exact size, stated as facts. 600 is comfortably
    above the measured misread and below any real article; being wrong either way costs one sentence."""
    if len(text) >= _THIN_CHARS:
        return ""
    note = (f"web_extract: the fetch SUCCEEDED. This page's entire readable text is {len(text)} "
            "characters — shown complete below.")
    if any(s in text.lower() for s in _PLACEHOLDER_SIGNALS):
        note += " It reads like a domain placeholder page."
    return note + " A thin page is not an access failure: do not say it could not be opened or read."


# Signals of a block page / login wall / anti-bot shell — must FAIL so the model falls back to web_search.
_JUNK_SIGNALS = (
    "blocked by network security",
    "security verification",
    "verify you are human",
    "verifying you are human",
    "enable javascript",
    "log in to continue",
    "are you a robot",
    "access denied",
    "captcha",
)


# A block page is a stub. Match the signals against the page shell, not the extracted article text: a
# long article that merely DISCUSSES captchas or access denials is not a block page.
_JUNK_MAX_CHARS = 1500


def _is_junk(text: str) -> bool:
    low = text.lower()
    if len(text) < _JUNK_MAX_CHARS and any(s in low for s in _JUNK_SIGNALS):
        return True
    # Pages that start with "skip to content" and are mostly nav links = chrome only, no article.
    if low.lstrip().startswith("skip to content") and len(text) < 2500:
        return True
    return False

# Real browser UA — many sites (Cloudflare etc.) 403 non-browser requests.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


_MAX_REDIRECTS = 4


async def _get_safe(client, url: str):
    """GET with redirects followed MANUALLY so every hop is SSRF-checked (an external page must not be
    able to redirect us to an internal address). Returns the final httpx Response, or None if any hop
    is unsafe / too many redirects."""
    from kotoba.core.ssrf import check_redirect

    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        resp = await client.get(current, headers=_HEADERS)
        if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
            target, reason = check_redirect(current, resp.headers["location"])
            if reason:
                return None
            current = target
            continue
        return resp
    return None


_MAX_ERROR_BODY = 400


_GONE = "that page does not exist"


def _is_definitive(reason: str) -> bool:
    """404/410 is a verdict, not an obstacle. Jina renders the site's own "no such article" shell and
    returns it as a 1.4k-char success (measured on the es.wikipedia Live2D 404) — which the model then
    quotes as "the page says…". For a gone page the honest answer is the error body, not a proxy retry."""
    return reason.startswith(_GONE)


def _status_reason(code: int) -> str:
    if code in (404, 410):
        return f"{_GONE} (HTTP {code})"
    if code in (401, 403):
        return f"the site refused to serve it (HTTP {code})"
    if code >= 500:
        return f"the site is down or erroring (HTTP {code})"
    return f"the site answered HTTP {code}"


def _error_body(html: str) -> str:
    """A short excerpt of a NON-2xx body. A Wikipedia 404 says, in words worth quoting, that no such
    article exists — discarding it threw away the only real answer available. Readability
    first (it finds the message under the site chrome), raw tag-strip as fallback for JSON/plain errors."""
    import re

    if not html:
        return ""
    text = _readable_from_html(html[:40000]) or ""
    if not text:
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html[:40000])
        text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    if len(text) > _MAX_ERROR_BODY:
        text = text[:_MAX_ERROR_BODY].rstrip() + "…"
    return text


async def _direct(client, url: str) -> tuple[str | None, str]:
    """Fast path: fetch the page ourselves and extract the readable article.
    Returns (text, reason) — reason is "" on success and a short human phrase on failure."""
    try:
        resp = await _get_safe(client, url)
        if resp is None:
            return None, "that link redirected somewhere unsafe"
        if resp.status_code >= 400:
            reason = _status_reason(resp.status_code)
            body = await asyncio.to_thread(_error_body, resp.text)
            if body:  # rides the FAILURE path, labelled — quotable, never mistakable for the article
                reason += f'. The site\'s own error page said: "{body}"'
            return None, reason
        # readability-lxml is CPU-bound + SYNC — a blocked event loop stalls voice turns (WS 1006).
        text = await asyncio.to_thread(_readable_from_html, resp.text)
        return (text, "") if text else (None, "that page had no readable text in it")
    except Exception:
        return None, "that site didn't respond"


def _readable_from_html(html: str) -> str | None:
    """Sync, CPU-bound: extract the readable article (title + text) from raw HTML. Offloaded via
    asyncio.to_thread by _direct so the parse can't block the event loop."""
    import re

    try:
        from readability import Document
    except ImportError:
        # Optional dep — its absence must NOT surface as "that site didn't respond"; Jina still works.
        log.info("readability-lxml is not installed — falling back to the reader service")
        return None

    try:
        doc = Document(html)
        title = doc.short_title()
        text = re.sub(r"<[^>]+>", " ", doc.summary())
        text = re.sub(r"\s+", " ", text).strip()
        return f"{title}\n\n{text}" if text else None
    except Exception:
        return None


_JINA_MIN_CHARS = 40


async def _jina(client, url: str) -> str | None:
    """Fallback: Jina Reader renders the page server-side and returns clean text, getting through many
    sites that block a plain request. Free, no key; a hard Cloudflare challenge still falls to web_search.

    The size floor is a SHELL DETECTOR, not a quality bar: a render this small is a bare title or a
    challenge stub, never the page. It used to be 200, which also swallowed genuinely short legitimate
    pages (example.com's body is 187 chars once the duplicated title is gone) and manufactured "no
    readable text" — the exact false sentence this tool exists to prevent. Thin-but-real text now passes
    and is labelled a thin success, so the honest floor is only what no real content can fit under."""
    import re

    try:
        resp = await client.get(
            "https://r.jina.ai/" + url,
            headers={"User-Agent": _HEADERS["User-Agent"], "X-Return-Format": "text"},
        )
        resp.raise_for_status()
        text = re.sub(r"\s+", " ", resp.text).strip()
        if not text or len(text) < _JINA_MIN_CHARS or _is_junk(text):
            return None
        return text
    except Exception:
        return None


def _fail(ctx, reason: str) -> None:
    """Record WHY the fetch failed so core.loop can put it in the model's fail note.

    Live QA: es.wikipedia.org/wiki/Live2D is a 404 (no such article). The tool returned a
    bare "nothing", the model fell back to web_search as designed — and then answered "the page
    explains that…", presenting search snippets as a reading of a page it never opened. The reason is
    what lets her say the honest thing instead."""
    try:
        ctx.web_extract_error = reason
    except Exception:
        pass


async def execute(args: dict, ctx):
    """Return readable page text, or None on failure (then the loop narrates the fail line and the
    model falls back to web_search — never an endless retry). Tries a direct fetch first, then the
    Jina Reader proxy which gets through most sites a plain request can't."""
    import re

    import httpx

    from kotoba.core.ssrf import url_block_reason

    _fail(ctx, "")
    url = (args or {}).get("url", "").strip()
    if not url or not re.match(r"^https?://", url, re.I):
        _fail(ctx, "that wasn't a usable http(s) link")
        return None  # no/invalid URL → treated as a failure (not a fake "success" string)

    # SSRF guard: never fetch internal/loopback/link-local/private targets (cloud metadata, the LAN).
    if url_block_reason(url) is not None:
        _fail(ctx, "that address is internal, so it can't be opened")
        return None

    # follow_redirects=False on purpose — _get_safe follows manually so EVERY hop is SSRF-checked.
    async with httpx.AsyncClient(follow_redirects=False, timeout=25.0) as client:
        text, reason = await _direct(client, url)
        if not text or _is_junk(text):
            if text and _is_junk(text):
                reason = "the site served a verification/block page instead of the article"
            if not _is_definitive(reason):
                text = await _jina(client, url)
    if not text or _is_junk(text):
        _fail(ctx, reason or "that page couldn't be read")
        return None

    from kotoba.core.quoted import fence

    notes = " ".join(n for n in (_thin_note(text), _cut_note(text)) if n)
    return fence(f"web_extract read {url}", text[:_MAX_CHARS], note=notes)
