"""A markdown research file must carry the real source URLs the run actually fetched.

Two nets catch citations on either side of the write: one appends a Sources section when the model
calls a search and a file write in one response, so citation annotations reach the context during the
stream, before the write runs; the other revisits every markdown file after the run finishes, catching
the case the first net cannot see, where the report is written partway through the research and the
remaining citations arrive afterwards. The second net re-reads each file, so one that has since gained
links, or been deleted, is left alone and no second Sources section is ever stacked.
"""
from __future__ import annotations

import asyncio

import kotoba.core.loop as loop
from kotoba.core import citations, events
from kotoba.tools import ToolContext
from kotoba.tools.action import file_write


def _ctx(tmp_path):
    return ToolContext(db=None, session_id="t", workdir=tmp_path, mode="work")


def _cite(url, title=""):
    return {"type": "url_citation", "url": url, "title": title}


class _DB:
    async def insert_audit_log(self, **kw): pass
    async def list_approved_commands(self): return []


def test_research_md_with_prose_sources_gets_url_section(tmp_path):
    """A markdown file that names sources in prose but has no URLs must receive a ## Sources
    section with real, fetched URLs from ctx._sources after write_file executes."""
    ctx = _ctx(tmp_path)
    citations.collect_from_annotation(ctx, _cite("https://example.com/docs", "Example Docs"))
    citations.collect_from_annotation(ctx, _cite("https://other.org/reference", ""))

    prose_only = (
        "# LangChain vs CrewAI Comparison\n\n"
        "According to the official documentation, LangChain introduced a new approach.\n"
        "The library docs explain the agent interface in detail.\n"
        "Various benchmark reports cover performance.\n"
    )
    result = asyncio.run(
        file_write.execute({"path": "research/langchain-comparison.md", "content": prose_only}, ctx)
    )

    assert result and "Wrote" in result
    written = (tmp_path / "research" / "langchain-comparison.md").read_text(encoding="utf-8")
    assert "## Sources" in written, "must have a Sources section"
    assert "https://example.com/docs" in written, "first URL must appear"
    assert "https://other.org/reference" in written, "second URL must appear"
    assert "Example Docs" in written, "titled URL must use markdown link format"


def test_a_sources_heading_with_no_links_does_not_block_the_real_urls(tmp_path):
    """A "## Sources" section listing titles in prose, with no URL, is not "already cited"."""
    ctx = _ctx(tmp_path)
    citations.collect_from_annotation(ctx, _cite("https://sw.kovidgoyal.net/kitty/graphics-protocol/",
                                                 "Terminal graphics protocol"))
    prose_heading = (
        "# Kitty graphics protocol\n\nThe protocol sends pixel data through escape sequences.\n\n"
        "## Sources\n- Kitty official documentation: Terminal graphics protocol.\n"
    )
    asyncio.run(file_write.execute({"path": "research/kitty.md", "content": prose_heading}, ctx))

    written = (tmp_path / "research" / "kitty.md").read_text(encoding="utf-8")
    assert "https://sw.kovidgoyal.net/kitty/graphics-protocol/" in written, "the real URL must land"
    assert written.count("## Sources") == 1, "reuse her heading, don't stack a second one"


def test_sources_section_not_duplicated_when_model_already_included_it(tmp_path):
    """No second ## Sources block when the model already wrote one."""
    ctx = _ctx(tmp_path)
    citations.collect_from_annotation(ctx, _cite("https://example.com", "Example"))

    with_sources = (
        "# Report\n\nContent here.\n\n## Sources\n\n- https://already-there.com\n"
    )
    asyncio.run(
        file_write.execute({"path": "research/report.md", "content": with_sources}, ctx)
    )
    written = (tmp_path / "research" / "report.md").read_text(encoding="utf-8")
    assert written.count("## Sources") == 1


def test_sources_not_appended_to_non_markdown_files(tmp_path):
    """Source URLs must never be injected into non-.md files (Python scripts, configs, etc.)."""
    ctx = _ctx(tmp_path)
    citations.collect_from_annotation(ctx, _cite("https://example.com", "Example"))

    asyncio.run(
        file_write.execute({"path": "analysis.py", "content": "# code\nresult = 1\n"}, ctx)
    )
    written = (tmp_path / "analysis.py").read_text(encoding="utf-8")
    assert "## Sources" not in written
    assert "https://example.com" not in written


def test_no_sources_section_when_ctx_has_no_citations(tmp_path):
    """No ## Sources appended when no web_search citations were captured."""
    ctx = _ctx(tmp_path)

    asyncio.run(
        file_write.execute({"path": "research/no-search.md", "content": "# Report\n\nContent.\n"}, ctx)
    )
    written = (tmp_path / "research" / "no-search.md").read_text(encoding="utf-8")
    assert "## Sources" not in written


def test_fuentes_heading_variant_not_duplicated(tmp_path):
    """Spanish '## Fuentes' heading is recognised as an existing sources section."""
    ctx = _ctx(tmp_path)
    citations.collect_from_annotation(ctx, _cite("https://example.com", "Example"))

    with_fuentes = "# Informe\n\nContenido.\n\n## Fuentes\n\n- https://ya-esta.com\n"
    asyncio.run(
        file_write.execute({"path": "research/informe.md", "content": with_fuentes}, ctx)
    )
    written = (tmp_path / "research" / "informe.md").read_text(encoding="utf-8")
    assert "## Fuentes" in written
    assert written.count("## Sources") == 0


# --- net 2: the turn-end sweep, for sources that land AFTER the write ------------------------------

_KITTY_URL = "https://sw.kovidgoyal.net/kitty/keyboard-protocol/"
_PROSE_REPORT = (
    "# Kitty keyboard protocol\n\nThe protocol encodes modifiers in the escape sequence.\n\n"
    "## Sources\n- Kitty official documentation.\n"
)


def _run_turn(session, db):
    """Drive the REAL agentic_loop with a scripted body, so the net is exercised where it lives."""
    events.register(session)
    try:
        return asyncio.run(loop.agentic_loop(
            [{"role": "user", "content": "research the kitty keyboard protocol"}],
            session, db, asyncio.Queue(), {},
        ))
    finally:
        events.unregister(session)


def test_sources_captured_after_the_write_still_reach_the_file(tmp_path, monkeypatch):
    """write_file runs while ctx._sources is still EMPTY and the url_citation annotations land after.
    The report must still end up with the real URL — once."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path))
    monkeypatch.setattr(loop, "get_client", lambda: object())
    report = tmp_path / "research" / "kitty-keyboard-protocol.md"

    async def iterations(client, ctx, *a, **k):
        await file_write.execute(
            {"path": "research/kitty-keyboard-protocol.md", "content": _PROSE_REPORT}, ctx
        )
        assert "http" not in report.read_text(encoding="utf-8"), "precondition: nothing to cite at write time"
        citations.collect_from_annotation(ctx, _cite(_KITTY_URL, "kitty keyboard protocol"))
        return "done"

    monkeypatch.setattr(loop, "_run_iterations", iterations)
    _run_turn("sess-late-sources", _DB())

    written = report.read_text(encoding="utf-8")
    assert _KITTY_URL in written, "the URL the run really fetched must land in the file"
    assert written.count("## Sources") == 1, "reuse her heading — never stack a second section"
    assert "[kitty keyboard protocol](" in written, "titled URL as a markdown link"


def test_turn_end_net_leaves_a_file_that_already_has_links_untouched(tmp_path):
    """A report that already cites links is finished work: the net must not touch it, even when the
    run captured other URLs it does not mention."""
    ctx = _ctx(tmp_path)
    body = "# Report\n\nSee the docs.\n\n## Sources\n\n- https://cited-by-her.example/page\n"
    (tmp_path / "report.md").write_text(body)
    citations.note_markdown_write(ctx, "report.md")
    citations.collect_from_annotation(ctx, _cite("https://not-mentioned.example", "Other"))

    assert citations.complete_reports(ctx) == []
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == body
    assert "not-mentioned" not in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_turn_end_net_adds_no_second_section_after_a_write_time_append(tmp_path):
    """Both nets run on the same file when the citations arrived first. Exactly one Sources section."""
    ctx = _ctx(tmp_path)
    citations.collect_from_annotation(ctx, _cite("https://early.example/doc", "Early"))
    asyncio.run(file_write.execute({"path": "r.md", "content": _PROSE_REPORT}, ctx))
    after_write = (tmp_path / "r.md").read_text(encoding="utf-8")

    assert citations.complete_reports(ctx) == []
    assert (tmp_path / "r.md").read_text(encoding="utf-8") == after_write
    assert after_write.count("## Sources") == 1


def test_turn_end_net_skips_a_file_deleted_during_the_run(tmp_path):
    ctx = _ctx(tmp_path)
    asyncio.run(file_write.execute({"path": "gone.md", "content": "# Draft\n"}, ctx))
    (tmp_path / "gone.md").unlink()
    citations.collect_from_annotation(ctx, _cite("https://late.example", "L"))

    assert citations.complete_reports(ctx) == []
    assert not (tmp_path / "gone.md").exists(), "never resurrect a file she removed"


def test_turn_end_net_never_writes_outside_the_jail(tmp_path):
    """The sweep re-resolves every remembered path through the same jail the write used."""
    ctx = _ctx(tmp_path / "work")
    (tmp_path / "work").mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Untouched\n")
    ctx._md_writes = ["../outside.md", "/etc/kotoba-escape.md"]
    citations.collect_from_annotation(ctx, _cite("https://late.example", "L"))

    assert citations.complete_reports(ctx) == []
    assert outside.read_text(encoding="utf-8") == "# Untouched\n"


def test_a_patched_markdown_file_is_swept_too(tmp_path):
    """patch is a file tool: an expanded-in-place report gets the same net."""
    ctx = _ctx(tmp_path)
    from kotoba.tools.action import patch

    (tmp_path / "notes.md").write_text("# Notes\n\nOld line.\n")
    asyncio.run(patch.execute({"path": "notes.md", "old_string": "Old line.",
                               "new_string": "New line."}, ctx))
    citations.collect_from_annotation(ctx, _cite("https://late.example/x", "X"))

    assert citations.complete_reports(ctx) == ["notes.md"]
    assert "https://late.example/x" in (tmp_path / "notes.md").read_text(encoding="utf-8")


def test_a_helper_report_is_swept_by_the_parents_turn_end(tmp_path):
    """delegate runs the helper on ctx.child(); the parent's finally is the only net that will run."""
    parent = _ctx(tmp_path)
    child = parent.child("sub1")
    asyncio.run(file_write.execute({"path": "helper.md", "content": "# Helper\n\nFindings.\n"}, child))
    citations.collect_from_annotation(child, _cite("https://helper-src.example", "H"))

    assert citations.complete_reports(parent) == ["helper.md"]
    assert "https://helper-src.example" in (tmp_path / "helper.md").read_text(encoding="utf-8")


def test_the_net_never_breaks_the_turn(tmp_path, monkeypatch):
    """A failure in the safety net must not fail the turn it is decorating."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path))
    monkeypatch.setattr(loop, "get_client", lambda: object())

    def boom(_ctx):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(citations, "complete_reports", boom)

    async def iterations(client, ctx, *a, **k):
        return "the answer"

    monkeypatch.setattr(loop, "_run_iterations", iterations)
    assert _run_turn("sess-net-boom", _DB()) == "the answer"


# --- the loop's library mirror must not undo either net --------------------------------------------

def _ev_text(t):
    import types
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args):
    import json
    import types
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id="c1")
    return types.SimpleNamespace(type="response.output_item.done", item=item)


class _FakeClient:
    def __init__(self, scripts):
        self.responses = self
        self._scripts, self._i = scripts, 0

    async def create(self, **kw):
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1

        class _S:
            def __aiter__(_self):
                async def gen():
                    for e in evs:
                        yield e
                return gen()
        return _S()


def test_library_mirror_does_not_clobber_the_appended_sources(tmp_path, monkeypatch):
    """The loop mirrors every write into the file library. On the default setup the workdir IS the
    library, so mirroring the model's raw arguments overwrote the Sources write_file had just added —
    the write-time net was landing on disk and then being erased."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path))
    session = "sess-mirror"
    ctx = _ctx(tmp_path)
    ctx.session_id = session
    ctx.approval = None
    citations.collect_from_annotation(ctx, _cite("https://early.example/doc", "Early"))

    events.register(session)
    scripts = [[_ev_call("write_file", {"path": "research/r.md", "content": _PROSE_REPORT})],
               [_ev_text("done")]]
    try:
        asyncio.run(loop._run_iterations(
            _FakeClient(scripts), ctx, [{"role": "user", "content": "x"}], asyncio.Queue(), {},
            max_iterations=3, mode="work", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        ))
    finally:
        events.unregister(session)

    written = (tmp_path / "research" / "r.md").read_text(encoding="utf-8")
    assert "https://early.example/doc" in written, "the mirror must not overwrite the completed file"
    assert written.count("## Sources") == 1


def test_every_turn_chain_reaches_the_net_through_agentic_loop():
    """main, the local-voice WS and work_runner all end their turn in agentic_loop's finally — which is
    why the net lives there and not at three call sites."""
    import inspect

    import kotoba.core.voice.session as voice_session
    import kotoba.core.work_runner as work_runner
    import kotoba.server as main

    for mod in (main, voice_session, work_runner):
        assert "agentic_loop(" in inspect.getsource(mod), mod.__name__
    assert "complete_reports(ctx)" in inspect.getsource(loop.agentic_loop)
