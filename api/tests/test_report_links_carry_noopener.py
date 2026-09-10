"""Report citations are the one link path in the product that does not go through the shared markdown
renderer, which decided once what a link does everywhere else: `target="_blank"` so a click cannot
navigate the app away and drop the call, and `rel="noopener noreferrer"` so the opened page gets no
`window.opener` handle back. A test holds every other renderer to that rule, but these anchors are
built in Python and rendered inside an iframe with popups escaping the sandbox — the links point at
whatever the web gave her, the least trustworthy source in the system, so the rule matters most
exactly here and was pinned by nothing. These render a real report and read the emitted HTML, the live
copy the viewer gets, rather than matching the template, since the template is not what a browser
receives.
"""
from __future__ import annotations

import asyncio
import re

import kotoba.tools.action.make_report as mr
from kotoba.core import citations, reports
from kotoba.tools import ToolContext

_ANCHOR_RE = re.compile(r"<a\b[^>]*>")

_SOURCES = [
    {"title": "PixiJS releases", "url": "https://github.com/pixijs/pixijs/releases"},
    {"title": "", "url": "https://nextjs.org/blog", "note": "the changelog"},
]


def _render(sid: str, args: dict, captured: dict | None = None) -> str:
    ctx = ToolContext(db=None, session_id=sid, mode="work")
    for url, title in (captured or {}).items():
        citations.collect_from_annotation(ctx, {"type": "url_citation", "url": url, "title": title})
    asyncio.run(mr.execute(args, ctx))
    return reports.get_report(sid) or ""


def _assert_every_anchor_is_safe(html: str, where: str) -> None:
    anchors = _ANCHOR_RE.findall(html)
    assert anchors, f"{where}: no anchors at all — this is looking at nothing"
    for tag in anchors:
        assert 'target="_blank"' in tag, f"{where}: a citation opens in the app's own tab — {tag}"
        assert 'rel="noopener noreferrer"' in tag, (
            f"{where}: a citation without noopener — the opened page keeps a handle on this one — {tag}"
        )


def test_a_rendered_reports_citations_all_carry_target_and_rel():
    html = _render("noopener-a", {"title": "T", "summary": "S", "sources": _SOURCES})
    _assert_every_anchor_is_safe(html, "the live report")
    assert html.count("<a ") == len(_SOURCES), "every source got exactly one anchor"


def test_a_backfilled_citation_is_held_to_the_same_rule():
    """She cited nothing, so `ctx._sources` filled it — a second construction site for the same anchor."""
    html = _render("noopener-b", {"title": "T", "summary": "Informe sobre el SDK."},
                   {"https://github.com/Live2D/CubismWebFramework": "CubismWebFramework"})
    _assert_every_anchor_is_safe(html, "the backfilled report")


def test_a_bare_url_string_is_held_to_the_same_rule():
    """The lenient parser path: "Title — https://…" becomes an anchor too, and it is the same anchor."""
    html = _render("noopener-c", {"title": "T", "summary": "S",
                                  "sources": ["PixiJS — https://pixijs.com/versions"]})
    _assert_every_anchor_is_safe(html, "the parsed-string report")


def test_the_html_saved_to_files_carries_the_rule_too(tmp_path, monkeypatch):
    """The artifact outlives the session: it is downloaded, printed and reopened from the Files panel."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    _render("noopener-d", {"title": "Saved one", "summary": "S", "sources": _SOURCES})
    saved = list((tmp_path / "files" / "reports").glob("*.html"))
    assert len(saved) == 1
    _assert_every_anchor_is_safe(saved[0].read_text(encoding="utf-8"), "the saved .html")


def test_the_template_itself_contributes_no_anchor_of_its_own():
    """If a link is ever hand-written into the template, it bypasses `_source_items` and this rule."""
    tpl = mr._template() or ""
    assert tpl, "the template is missing — the rest of this file proves nothing"
    assert not _ANCHOR_RE.findall(tpl), "the template grew an anchor; hold it to the rule or route it here"
