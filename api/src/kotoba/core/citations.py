"""Capture web_search source URLs from the Responses API `url_citation` annotations.

When web_search runs, OpenAI injects opaque Private-Use-Area citation markers into the text (stripped
by stream.strip_citation_markers — they are metadata, not URLs). The REAL URLs arrive as
`url_citation` annotations, collected per turn-context so a research report can list clickable sources.

Stored on the ToolContext so they persist across the iterations of one agentic_loop run: a background
job researches across several turns, then writes the report. Two nets put them into a markdown report —
append_sources() at write time, and complete_reports() at turn end, for the common case where the model
writes the report BEFORE its citations have arrived."""
from __future__ import annotations

import re

_LINK_RE = re.compile(r"https?://")
_SOURCES_HEADING_RE = re.compile(r"##\s+(?:sources?|fuentes?|references?)\b", re.IGNORECASE)


def collect_from_annotation(ctx, annotation) -> None:
    """Record one annotation if it's a url_citation. Accepts an SDK object or a dict; fails closed."""
    if ctx is None or annotation is None:
        return
    try:
        get = (lambda k: annotation.get(k)) if isinstance(annotation, dict) else (lambda k: getattr(annotation, k, None))
        if (get("type") or "") != "url_citation":
            return
        url = (get("url") or "").strip()
        title = (get("title") or "").strip()
    except Exception:
        return
    if not url.startswith(("http://", "https://")):
        return
    store = getattr(ctx, "_sources", None)
    if store is None:
        store = ctx._sources = {}
    # First title wins (later duplicates are usually empty); never overwrite a good title with "".
    if url not in store or (not store[url] and title):
        store[url] = title


def merge_from_child(parent, child) -> int:
    """Lift a helper's captured sources into its parent; returns how many were new.

    A no-op TODAY: `ToolContext.child()` hands the parent's `_sources` dict to the child by reference, so
    a helper's URLs are already in the parent and this returns 0. It stays as the explicit guarantee for
    the case `child()` stops sharing — delegate() returns only the helper's TEXT, so without one of the
    two mechanisms every URL it fetched dies with the child and a delegated report cites nothing."""
    if parent is None or child is None or parent is child:
        return 0
    src = getattr(child, "_sources", None) or {}
    if not src:
        return 0
    store = getattr(parent, "_sources", None)
    if store is None:
        store = parent._sources = {}
    added = 0
    for url, title in src.items():
        if url not in store:
            store[url] = title
            added += 1
        elif not store[url] and title:
            store[url] = title
    return added


def pending_note(ctx) -> str | None:
    """A developer note listing sources captured but not yet shown to the model (marks them shown). Lets
    the model cite REAL URLs in a report instead of the opaque inline markers. None when nothing new.

    The wording must stay unusable as prose — an instruction ending in a colon above a URL list reads
    as a report lead-in, and gets reproduced verbatim into the deliverable."""
    store = getattr(ctx, "_sources", None) or {}
    items = list(store.items())
    shown = getattr(ctx, "_sources_shown", 0)
    if len(items) <= shown:
        return None
    new = items[shown:]
    ctx._sources_shown = len(items)
    lines = "\n".join(f"- {title or url}: {url}" for url, title in new)
    return (
        "[internal] Real URLs captured from this turn's web searches — link these from a report's "
        "source list, never the inline citation markers. This note is instruction, not content; it "
        "must never appear in a file or a reply.\n" + lines
    )


def append_sources(body: str, sources: dict) -> str | None:
    """`body` with a markdown source list appended, or None when it needs none.

    Gates on whether the body carries LINKS, not on whether it has a sources HEADING: the model often
    writes "## Sources" listing titles in prose with no URL, which a heading-only check would read as
    already cited. Only URLs really captured this run are used — nothing is invented or looked up."""
    if not sources or not isinstance(body, str) or _LINK_RE.search(body):
        return None
    lines = ["", ""] if _SOURCES_HEADING_RE.search(body) else ["", "", "## Sources", ""]
    for url, title in sources.items():
        lines.append(f"- [{title}]({url})" if title else f"- {url}")
    return body.rstrip() + "\n".join(lines) + "\n"


def note_markdown_write(ctx, rel_path: str) -> None:
    """Remember a markdown file this run wrote, so complete_reports() can revisit it at turn end."""
    if ctx is None or not rel_path:
        return
    store = getattr(ctx, "_md_writes", None)
    if store is None:
        try:
            store = ctx._md_writes = []
        except AttributeError:
            return
    if rel_path not in store:
        store.append(rel_path)


def complete_reports(ctx) -> list[str]:
    """Turn-end net: fill in the sources of markdown files this run wrote that still have no links.

    The write-time append can only use what had arrived by then, and the model routinely writes the
    report before its url_citation annotations land. Each file is re-read (never patched from the
    remembered content) so one that has since gained links, or been deleted, is left alone.

    Synchronous on purpose: it runs in agentic_loop's finally, where awaiting during cancellation
    would skip the sandbox teardown that follows."""
    sources = dict(getattr(ctx, "_sources", None) or {})
    paths = list(getattr(ctx, "_md_writes", None) or [])
    workdir = getattr(ctx, "workdir", None)
    if not sources or not paths or workdir is None:
        return []

    from kotoba.core.path_security import PathSecurityError, validate_within_dir

    done: list[str] = []
    for rel in paths:
        try:
            p = validate_within_dir(rel, workdir)  # the write's own jail, not a wider one
            if not p.is_file():
                continue
            completed = append_sources(p.read_text(errors="replace", encoding="utf-8"), sources)
            if completed is None:
                continue
            p.write_text(completed, encoding="utf-8")
            done.append(rel)
        except (PathSecurityError, OSError, ValueError):
            continue
    return done
