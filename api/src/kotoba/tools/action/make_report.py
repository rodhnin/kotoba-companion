"""make_report — fill the report template with content and present it in the call.

The model supplies ONLY content; the design is fixed and she never writes HTML/CSS. `sources` is
first-class because the schema once had none and every report promised links it could not hold: a
BACKFILL from the URLs this run really surfaced, and a REFUSAL when the prose promises what it lacks.

That refusal is narrow and WITNESSED. Narrow, because judging the whole text refused five ordinary
reports about menus and broken pages, each asking her to delete a TRUE sentence. Witnessed, because a
refusal is a non-empty string, so the loop scored a green ✓ for no report. "I've opened it for you"
needed a witness too: `report_ready` is dropped when nothing is listening, so the two halves separate."""
from __future__ import annotations

import html as _html
import re
from pathlib import Path
from kotoba.paths import DATA_DIR, REPO_ROOT

SCHEMA = {
    "type": "function",
    "name": "make_report",
    "description": (
        "OPTIONAL end-of-work report. Use it ONLY when ALL of these hold: (1) the task is genuinely DONE "
        "(everything actually executed and verified — never before the work, never mid-task), and (2) it "
        "was a LONG, multi-part job whose outcome is worth a written summary the user will revisit (deep "
        "research, a build, a multi-step scrape/automation). For a short or simple task (a couple of steps, "
        "a quick lookup, one file) do NOT make a report — just finish and say the result in a sentence. "
        "Calling this is a judgement: most tasks do NOT need one. Never call it twice for the same work. "
        "EXCEPTION — always use this when the USER explicitly asks for a report / a downloadable / formal / "
        "PDF write-up of a substantial job: that is exactly what this is for (it opens a polished viewer and "
        "offers a one-click PDF). For a research task, still save the findings file, THEN call this to "
        "present them. Provide the content fields; the layout is handled for you. "
        "SOURCES ARE MANDATORY when the report rests on anything you looked up (web_search, a page you "
        "opened, a link you showed): fill `sources` with the REAL URLs you actually saw — the ones from the "
        "internal 'Real URLs captured' note, an open_link you made, or a page you read. They render as "
        "clickable links in the report and the PDF. NEVER write that the report includes links / sources / "
        "URLs in `summary`, `results` or anywhere else without filling `sources` — a report that promises "
        "citations and has none is a lie to the user, and that call is refused. Equally, never invent, "
        "guess or reconstruct a URL you did not see: if you have none, say nothing about links."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short report title."},
            "summary": {"type": "string", "description": "One short paragraph: what this report covers."},
            "steps": {"type": "array", "items": {"type": "string"}, "description": "What you did, in order."},
            "results": {"type": "array", "items": {"type": "string"}, "description": "Outcomes / findings."},
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Name of the source (site, outlet, page or repo)."},
                        "url": {"type": "string", "description": (
                            "The full real URL, copied EXACTLY as you were given it. Never invented, never "
                            "reconstructed from memory, never a search-result placeholder."
                        )},
                        "note": {"type": "string", "description": "Optional: one short line on what it contributed."},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
                "description": (
                    "Every source the report rests on, as clickable citations. Required whenever you used "
                    "web_search / opened a page / showed a link. Only URLs you really saw."
                ),
            },
            "files": {"type": "array", "items": {"type": "string"}, "description": "Files produced (name + note)."},
            "next_steps": {"type": "array", "items": {"type": "string"}, "description": "Suggested next steps."},
        },
        "required": ["title", "summary"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "report"
RISK = "write"  # produces an artifact; offered in BOTH modes — the schema's own rules gate when to call it

ANNOUNCE = "Let me put together a little report for you..."
HEARTBEAT = ["Laying it out...", "Almost ready to show you..."]
COMPLETE = "Here — I made you a report. Let me walk you through it."
FAIL = "I couldn't put the report together — let me just tell you instead."
EXPRESSIONS = {"focus": "determined", "done": "excited", "fail": "embarrassed"}

_chibi_uri_cache: str | None = None


def _asset(name: str) -> Path:
    """The clone's soul/ copy when it exists, else the one packaged inside the wheel."""
    p = REPO_ROOT / "soul" / name
    return p if p.exists() else DATA_DIR / "soul" / name


def _chibi_data_uri() -> str:
    """The cover chibi, EMBEDDED as a base64 data URI so the report is fully self-contained — the in-app
    viewer, /api/files/raw and the printed PDF all render it with no network fetch. It is a small WebP so
    the finished HTML still fits under file_library's per-file text cap."""
    global _chibi_uri_cache
    if _chibi_uri_cache is None:
        try:
            import base64

            raw = _asset("report-chibi.webp").read_bytes()
            _chibi_uri_cache = "data:image/webp;base64," + base64.b64encode(raw).decode("ascii")
        except Exception:
            _chibi_uri_cache = ""  # missing asset → empty src (alt text shows); report still renders
    return _chibi_uri_cache


def _template() -> str | None:
    p = _asset("report_template.html")
    return p.read_text(errors="replace", encoding="utf-8") if p.exists() else None


def _items(values, fallback: str) -> str:
    vals = [v for v in (values or []) if isinstance(v, str) and v.strip()]
    if not vals:
        return f"<li>{_html.escape(fallback)}</li>"
    return "".join(f"<li>{_html.escape(v.strip())}</li>" for v in vals)


_SOURCES_SECTION_RE = re.compile(r"[ \t]*<!--SOURCES:START-->.*?<!--SOURCES:END-->\n?", re.DOTALL)
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
# A promise of CITATIONS: the object is a source, a reference or a URL, whatever verb carries it.
_PROMISES_CITATIONS_RE = re.compile(
    r"\benlaces?\s+(?:a|de)\s+(?:las?\s+|sus\s+)?fuentes\b|\blinks?\s+to\s+(?:the\s+)?sources?\b"
    r"|\bsource\s+links?\b|\benlaces?\s+de\s+(?:las?\s+)?referencias\b"
    r"|\b(?:con|incluye|incluyendo|adjunto|adjuntando)\s+(?:las\s+|los\s+|sus\s+|todas\s+las\s+)?"
    r"(?:urls?|fuentes|citas|referencias)\b"
    r"|\b(?:with|including|includes|plus)\s+(?:the\s+|all\s+the\s+)?"
    r"(?:urls?|sources?|citations?|references?)\b",
    re.IGNORECASE,
)
# A promise of plain LINKS — on its own it says nothing; _promises_links asks WHAT carries them.
_PROMISES_LINKS_RE = re.compile(
    r"\b(?:con|incluye|incluyendo|adjunto|adjuntando)\s+(?:los\s+|las\s+|sus\s+|todos\s+los\s+)?"
    r"(?:enlaces?|links?)\b"
    r"|\b(?:with|including|includes|plus)\s+(?:the\s+|all\s+the\s+)?links?\b"
    r"|\b(?:dej[oaé]\w*|aqu[íi]\s+tienes|here\s+are)\s+(?:aqu[íi]\s+)?(?:los\s+|las\s+|the\s+)"
    r"(?:enlaces?|links?)\b",
    re.IGNORECASE,
)
# Names for the thing being written — the subject that makes a link promise a claim about the artifact.
_DELIVERABLE_RE = re.compile(
    r"\b(?:informes?|reportes?|reports?|write-?ups?|documentos?|pdf|resumen(?:es)?)\b", re.IGNORECASE)
# Having LOOKED SOMETHING UP. Not `consulté` ("con el cliente") or `estudio` (a design studio).
_RESEARCH_RE = re.compile(
    r"\b(?:investigaci[óo]n|investigu[ée]|investigad[oa]s?|b[úu]squeda|busqu[ée]|buscando|"
    r"fuentes?|bibliograf[íi]a|referencias?|citas?|"
    r"research(?:ed)?|searched|looked\s+up|browsed|sources?|citations?|references?)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"[.;!?\n]+")
_WORD_RE = re.compile(r"\w+")
_MAX_SOURCES = 20


def _clean_url(raw: str) -> str:
    """An http(s) URL safe to put in an href, or "". Blocks javascript:/data: — this is the only
    attribute in the whole report the model gets to fill."""
    url = (raw or "").strip().strip("<>()[]").rstrip(".,;")
    if not url.lower().startswith(("http://", "https://")) or len(url) > 2000:
        return ""
    if any(c in url for c in "<>\"'"):
        return ""
    return "" if any(c.isspace() or ord(c) < 32 for c in url) else url


def _one_source(item) -> dict | None:
    """Normalize one entry. Objects are the declared shape; a bare "Title — https://…" string is
    accepted too, because a dropped citation is worse than a lenient parser."""
    if isinstance(item, str):
        m = _URL_RE.search(item)
        if not m:
            return None
        item = {"url": m.group(0), "title": item[: m.start()].strip(" -—–:·|")}
    if not isinstance(item, dict):
        return None
    url = _clean_url(str(item.get("url") or ""))
    if not url:
        return None
    return {"url": url, "title": str(item.get("title") or "").strip(),
            "note": str(item.get("note") or "").strip()}


def _sources(args: dict, ctx) -> list[dict]:
    """The citations to render: hers when she gave any, else the URLs this run's searches really
    surfaced (ctx._sources, see core.citations). Nothing here is ever invented or looked up."""
    out: list[dict] = []
    seen: set[str] = set()
    for item in (args.get("sources") or []):
        s = _one_source(item)
        if s and s["url"] not in seen:
            seen.add(s["url"])
            out.append(s)
    if out:
        return out[:_MAX_SOURCES]
    for url, title in (getattr(ctx, "_sources", None) or {}).items():
        clean = _clean_url(str(url))
        if clean and clean not in seen:
            seen.add(clean)
            out.append({"url": clean, "title": str(title or "").strip(), "note": ""})
    return out[:_MAX_SOURCES]


def _source_items(sources: list[dict]) -> str:
    li = []
    for s in sources:
        label = _html.escape(s["title"] or s["url"])
        note = f'<span class="n">{_html.escape(s["note"])}</span>' if s["note"] else ""
        li.append(
            f'<li><a href="{_html.escape(s["url"], quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{label}</a>'
            f'<span class="u">{_html.escape(s["url"])}</span>{note}</li>'
        )
    return "".join(li)


def _carrier(chunk: str, at: int) -> list[str]:
    """The last two words before a link promise — what is actually said to carry the links."""
    return _WORD_RE.findall(chunk[:at])[-2:]


def _promises_links(args: dict) -> bool:
    """Does this report's own text advertise citations it has not got? Judged SENTENCE by sentence.

    Naming a source is the refusable claim. "Con los enlaces" is not: it is what any job about a website
    says about a menu, a footer or three broken pages, and matching it anywhere refused five ordinary
    reports in a row and told her to delete a true sentence. So a bare link promise has to be about the
    DELIVERABLE to be a lie, decided by what the phrase hangs off.

    Attachment is read from the two words BEFORE the promise, not from the sentence as a whole, because
    the sentence-wide version keyed on the word `informe` — which opens every Spanish report summary
    ever written, so naming the deliverable was enough to refuse it."""
    parts = [str(args.get(k) or "") for k in ("title", "summary")]
    for key in ("steps", "results", "files", "next_steps"):
        parts += [str(v) for v in (args.get(key) or []) if isinstance(v, str)]
    for chunk in _SENTENCE_SPLIT_RE.split(" \n".join(parts)):
        if _PROMISES_CITATIONS_RE.search(chunk):
            return True
        for m in _PROMISES_LINKS_RE.finditer(chunk):
            if _RESEARCH_RE.search(chunk):
                return True
            if any(_DELIVERABLE_RE.fullmatch(w) for w in _carrier(chunk, m.start())):
                return True
    return False


async def execute(args: dict, ctx) -> str:
    import asyncio
    from datetime import datetime, timezone

    from kotoba.core.events import emit_task
    from kotoba.core.reports import already_made, note_made, set_report

    args = args or {}
    title = (args.get("title") or "").strip()
    summary = (args.get("summary") or "").strip()
    if not title or not summary:
        return None

    # Double-call guard: the prompt says "never twice", but nothing enforced it → a reflexive second call
    # made two files + two memory notes. If we JUST made a report with this same title, don't redo it.
    if already_made(ctx.session_id, title):
        return f"I already put together “{title}” — it's saved in your Files. No need to redo it."

    sources = _sources(args, ctx)
    if not sources and _promises_links(args):
        from kotoba.core.loop import note_tool_refusal

        note_tool_refusal(ctx)
        return (
            "I did NOT make that report — its text promises sources it hasn't got.\n"
            "Nothing was produced, nothing opened, nothing saved: no `sources` were given and no real URL "
            "was captured this turn, so the report would advertise citations it doesn't have. Call "
            "make_report again either WITH `sources` (title + the exact URL you really saw for each) or "
            "with that claim removed from the text. Never write a URL you didn't see."
        )

    tpl = _template()
    if tpl is None:
        return None

    if sources:
        tpl = tpl.replace("{{SOURCES}}", _source_items(sources))
    else:
        tpl = _SOURCES_SECTION_RE.sub("", tpl).replace("{{SOURCES}}", "")
    html_out = (
        tpl.replace("{{TITLE}}", _html.escape(title))
        .replace("{{DATE}}", datetime.now(timezone.utc).strftime("%B %d, %Y"))
        .replace("{{SUMMARY}}", _html.escape(summary))
        .replace("{{STEPS}}", _items(args.get("steps"), "—"))
        .replace("{{RESULTS}}", _items(args.get("results"), "—"))
        .replace("{{FILES}}", _items(args.get("files"), "No files this time."))
        .replace("{{NEXT}}", _items(args.get("next_steps"), "—"))
        .replace("{{CHIBI}}", _chibi_data_uri())
    )
    set_report(ctx.session_id, html_out)
    note_made(ctx.session_id, title)  # remember it so an immediate second call for the same report is a no-op
    from kotoba.core.events import draws_cards

    # Not "did the frame reach anyone" — a surface that consumes frames and paints no viewer would
    # have her announce a report that opened nowhere.
    opened = draws_cards(ctx.session_id)
    await emit_task(ctx.session_id, "report_ready", title=title, run_id=getattr(ctx, "run_id", ""))

    # Persist so it survives the session: a real .html in the Files panel plus a durable memory note
    # (set_report above is only the transient in-memory copy the live viewer reads).
    saved_note = ""
    try:
        from kotoba.core import file_library, user_memory
        from kotoba.core.skill_docs import _slug

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rel = f"reports/{_slug(title) or 'report'}-{date_str}.html"
        stored = await asyncio.to_thread(file_library.save_text, rel, html_out)
        if stored:
            await emit_task(ctx.session_id, "artifact", path=stored, action="created",
                            run_id=getattr(ctx, "run_id", ""))
            await asyncio.to_thread(
                user_memory.append_fact,
                f"Produced a work report titled “{title}” ({summary[:140]}); saved in Files.",
                "reports",
            )
            saved_note = " Saved it to the user's Files (reports folder) and noted it in memory."
    except Exception:
        pass  # persistence is best-effort; the live report already opened

    cited = f" It cites {len(sources)} source{'s' if len(sources) > 1 else ''} as clickable links." if sources else ""
    shown = (
        " It's in front of them now — a viewer on a screen, a numbered row in a terminal — so do not "
        "describe how it opened." if opened else
        " It did NOT open on screen (no viewer is connected), so do not tell the user it's open" + (
            " — tell them where it's saved instead." if saved_note else
            ", and it could not be saved to their Files either — say so plainly; there is nothing to "
            "point them at.")
    )
    return f"Report ready: {title}.{shown}{cited}{saved_note}"
