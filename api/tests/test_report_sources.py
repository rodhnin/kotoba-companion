"""A report that rests on research must be able to carry its sources — and must not claim it does.

Asked for a written report with the links inside it, she called `make_report` and the artifact came out
with exactly one URL — the font stylesheet. Every HTML report this tool ever produced had the same
zero-citation shape, since the schema had no field for a source, while its own summary claimed the
report came with links to the official sources she had actually opened that same turn. The contrast
that isolated it: a delegated turn's markdown report carried real URLs — the hole was `make_report`
specifically. These tests pin the whole path: the field exists, real anchors reach the HTML, captured
URLs are used when she supplies none, an unciteable promise of links is refused, and no href can be
smuggled in."""
from __future__ import annotations

import asyncio
import re

import pytest

import kotoba.tools.action.make_report as mr
from kotoba.core import citations, reports
from kotoba.tools import ToolContext


def _ctx(sid: str, sources: dict | None = None) -> ToolContext:
    ctx = ToolContext(db=None, session_id=sid, mode="work")
    for url, title in (sources or {}).items():
        citations.collect_from_annotation(ctx, {"type": "url_citation", "url": url, "title": title})
    return ctx


def _run(args: dict, ctx) -> tuple[str | None, str | None]:
    out = asyncio.run(mr.execute(args, ctx))
    return out, reports.get_report(ctx.session_id)


def _anchors(html: str) -> list[tuple[str, str]]:
    return re.findall(r'<a href="([^"]+)"[^>]*>([^<]*)</a>', html or "")


# --- the field exists at all -------------------------------------------------------------------------

def test_schema_exposes_a_sources_field_of_title_url_objects():
    props = mr.SCHEMA["parameters"]["properties"]
    assert "sources" in props, "no field for a citation — a report can never carry one"
    item = props["sources"]["items"]
    assert item["type"] == "object"
    assert set(item["properties"]) == {"title", "url", "note"}
    assert item["required"] == ["url"], "a citation without a URL is not a citation"


def test_the_description_forbids_promising_sources_without_filling_them():
    d = mr.SCHEMA["description"].lower()
    assert "sources are mandatory" in d
    assert "never write that the report includes links" in d
    assert "never invent" in d or "never invent, guess" in d


# --- real anchors reach the HTML, and the PDF renders that same HTML ----------------------------------

def test_sources_render_as_clickable_anchors_with_the_url_visible():
    out, html = _run(
        {
            "title": "Cubism SDK",
            "summary": "State of the web SDK.",
            "results": ["R5 is the current line."],
            "sources": [
                {"title": "CubismWebSamples releases", "url": "https://github.com/Live2D/CubismWebSamples/releases",
                 "note": "official release list"},
                {"title": "Cubism docs", "url": "https://docs.live2d.com/en/cubism-sdk-manual/top/"},
            ],
        },
        _ctx("src-a"),
    )
    assert "Report ready" in out and "cites 2 sources" in out
    hrefs = [h for h, _ in _anchors(html)]
    assert "https://github.com/Live2D/CubismWebSamples/releases" in hrefs
    assert "https://docs.live2d.com/en/cubism-sdk-manual/top/" in hrefs
    # the raw URL is also visible text, so a renderer that drops link annotations still shows the address
    assert html.count("https://docs.live2d.com/en/cubism-sdk-manual/top/") >= 2
    assert "official release list" in html
    assert ">Sources<" in html and "{{" not in html


def test_a_source_with_no_title_still_gets_a_usable_link():
    _, html = _run(
        {"title": "T", "summary": "S", "sources": [{"url": "https://pixijs.com/versions"}]},
        _ctx("src-b"),
    )
    assert ("https://pixijs.com/versions", "https://pixijs.com/versions") in _anchors(html)


def test_a_bare_url_string_is_accepted_rather_than_dropped():
    """The schema asks for objects; a dropped citation is worse than a lenient parser."""
    _, html = _run(
        {"title": "T", "summary": "S",
         "sources": ["https://nextjs.org/blog", "PixiJS releases — https://github.com/pixijs/pixijs/releases"]},
        _ctx("src-c"),
    )
    assert _anchors(html) == [
        ("https://nextjs.org/blog", "https://nextjs.org/blog"),
        ("https://github.com/pixijs/pixijs/releases", "PixiJS releases"),
    ]


def test_the_saved_html_file_carries_the_links_too(tmp_path, monkeypatch):
    """The user downloads the artifact from Files — the citation has to be IN the file, not just live."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    _run(
        {"title": "Saved one", "summary": "S", "sources": [{"title": "P", "url": "https://pixijs.com/versions"}]},
        _ctx("src-d"),
    )
    saved = list((tmp_path / "files" / "reports").glob("*.html"))
    assert len(saved) == 1
    assert 'href="https://pixijs.com/versions"' in saved[0].read_text(encoding="utf-8")


# --- the net: URLs this run really captured, when she supplies none -----------------------------------

def test_captured_search_urls_backfill_an_empty_sources_field():
    """The live shape: she had real URLs in context (url_citation annotations) and cited none of them."""
    ctx = _ctx("src-e", {"https://github.com/Live2D/CubismWebFramework": "CubismWebFramework"})
    _, html = _run({"title": "T", "summary": "Informe sobre el SDK."}, ctx)
    assert ("https://github.com/Live2D/CubismWebFramework", "CubismWebFramework") in _anchors(html)


def test_her_own_curated_list_wins_over_the_captured_set():
    ctx = _ctx("src-f", {"https://seo-aggregator.example/live2d": "SEO page"})
    _, html = _run(
        {"title": "T", "summary": "S", "sources": [{"title": "Official", "url": "https://www.live2d.com/en/sdk/about/"}]},
        ctx,
    )
    assert [h for h, _ in _anchors(html)] == ["https://www.live2d.com/en/sdk/about/"]


def test_nothing_is_invented_when_there_is_nothing_to_cite():
    _, html = _run({"title": "Built a page", "summary": "Made the site.", "results": ["it works"]}, _ctx("src-g"))
    assert _anchors(html) == []
    assert ">Sources<" not in html, "an empty Sources heading advertises citations that don't exist"
    assert "{{" not in html


# --- the promise/afford mismatch, which is what shipped ------------------------------------------------

@pytest.mark.parametrize("field, value", [
    ("summary", "Informe breve del SDK, con enlaces a las fuentes oficiales."),
    ("summary", "A short write-up, with links to the sources."),
    ("results", ["Preparé el informe con los enlaces dentro."]),
    ("next_steps", ["Te lo amplío incluyendo las URLs."]),
])
def test_a_report_that_promises_links_without_any_is_refused(field, value):
    args = {"title": "Estado actual del Live2D Cubism SDK", "summary": "Resumen del estado del SDK."}
    args[field] = value
    out, html = _run(args, _ctx("src-h"))
    assert out and "did NOT make that report" in out
    assert "Never write a URL you didn't see" in out
    assert html is None, "the artifact must not exist at all — it would promise a citation it hasn't got"


def test_the_same_promise_is_fine_once_the_real_urls_are_there():
    ctx = _ctx("src-i", {"https://github.com/Live2D/CubismWebSamples/releases": "Releases"})
    out, html = _run(
        {"title": "T", "summary": "Informe breve, con enlaces a las fuentes oficiales."}, ctx
    )
    assert "Report ready" in out
    assert _anchors(html) == [("https://github.com/Live2D/CubismWebSamples/releases", "Releases")]


def test_ordinary_report_prose_is_not_mistaken_for_a_promise():
    out, html = _run(
        {"title": "QA", "summary": "Revisé la página oficial y la documentación del SDK.",
         "results": ["La fuente oficial dice R5.", "Compared the official docs and the release notes."]},
        _ctx("src-j"),
    )
    assert "Report ready" in out and html


# --- the href is the one attribute she fills: it cannot be turned into an injection --------------------

@pytest.mark.parametrize("bad", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    'https://ok.example/" onmouseover="alert(1)',
    "https://ok.example/--><script>alert(1)</script>",
    "https://ok.example/ with space",
    "not a url at all",
])
def test_only_plain_http_urls_become_links(bad):
    _, html = _run({"title": "T", "summary": "S", "sources": [{"title": "x", "url": bad}]}, _ctx("src-k"))
    assert _anchors(html) == []
    assert "javascript:" not in html and "<script>" not in html


def test_the_templates_own_doc_comment_is_not_filled_with_the_report():
    """Every shipped report carried a second, unrendered copy of itself in the template's comment. Now that
    a source is an anchor, that copy is also where a URL could have escaped the comment."""
    _, html = _run(
        {"title": "T", "summary": "S", "sources": [{"title": "P", "url": "https://pixijs.com/versions"}]},
        _ctx("src-l"),
    )
    comment = html[html.index("<!--"): html.index("-->")]
    assert "pixijs.com" not in comment and "{{" not in comment


# --- the guidance no longer promises something the tool cannot do -------------------------------------

def test_the_research_skill_points_at_the_sources_field():
    from kotoba.core import skill_docs

    body = skill_docs.view_skill("research")
    assert "make_report" in body and "`sources`" in body
    assert "never fabricate a source or URL" in body


def test_the_companion_prompt_asks_for_real_urls_in_the_report():
    from kotoba.soul.prompt import build_system_prompt

    prompt = build_system_prompt({"name": "K", "personality": "warm"}, "", [])
    assert "`sources`" in prompt and "includes the links" in prompt
