"""Research agent, the `open_link` tool, and the citation plumbing around them.

`open_link` offers a URL for the user to open in a new tab and waits for nobody: the research agent
uses it to hand over its principal source mid-call. This file also covers the `research` skill doc,
the citation markers that must never be spoken, and the sources note the model is handed.

The safety-critical assertions: open_link must NEVER open a non-web URI (file://, javascript:) and
must emit a need_input card with mode='open_link' carrying the url, WITHOUT blocking (wait=False).
"""
from __future__ import annotations

import asyncio
import types

import pytest

import kotoba.core.interaction as interaction
import kotoba.tools.registry as reg
from kotoba.tools.builtin import open_link


def _ctx(session_id="sess-test"):
    return types.SimpleNamespace(session_id=session_id, mode="companion", call_id="call-1")


def _capture_events(monkeypatch):
    """Capture what open_link_card emits onto the SSE channel (it imports emit_task/emit_emotion into the
    core.interaction namespace, so patch them there)."""
    events: list[dict] = []

    async def fake_emit_task(session_id, kind, **data):
        events.append({"session_id": session_id, "kind": kind, **data})

    async def fake_emit_emotion(session_id, emotion):
        events.append({"session_id": session_id, "kind": "emotion", "emotion": emotion})

    monkeypatch.setattr(interaction, "emit_task", fake_emit_task)
    monkeypatch.setattr(interaction, "emit_emotion", fake_emit_emotion)
    return events


# --- registration / mode gating -------------------------------------------------------------------

def test_open_link_registered_and_offered_in_companion():
    """Both modes offer it: the research agent hands over its source mid-call, in a companion turn."""
    reg.discover()
    companion = {s.get("name") for s in reg.schemas_for(mode="companion")}
    work = {s.get("name") for s in reg.schemas_for(mode="work")}
    assert "open_link" in companion
    assert "open_link" in work


def test_open_link_is_read_risk_core_toolset():
    """Read risk on the core toolset: companion-safe, and behind no approval gate.

    It only SHOWS a card — opening the page is the user's own click, so the consent is already in the
    interaction and a second gate in front of it would buy nothing."""
    assert open_link.RISK == "read"
    assert open_link.TOOLSET == "core"


# --- execute: happy path ---------------------------------------------------------------------------

def test_open_link_emits_open_link_card(monkeypatch):
    """The card carries the url and the `why` as its shown label, and never blocks.

    `wait=False` is the load-bearing half: a blocking card here would stall the voice turn behind a
    click. The call returns a short non-empty nudge instead, telling the model the card is up and
    that it should wrap the turn."""
    events = _capture_events(monkeypatch)

    result = asyncio.run(open_link.execute(
        {"url": "https://example.com/report", "why": "the original report"}, _ctx("s1")
    ))

    card = next((e for e in events if e["kind"] == "need_input"), None)
    assert card is not None, "open_link must emit a need_input card"
    assert card["mode"] == "open_link"
    assert card["url"] == "https://example.com/report"
    assert card["label"] == "the original report"
    assert card["wait"] is False
    assert card["session_id"] == "s1"
    assert isinstance(result, str) and result.strip()


def test_open_link_accepts_http_and_https(monkeypatch):
    for url in ("http://plain.example", "https://secure.example/x?y=1"):
        events = _capture_events(monkeypatch)
        asyncio.run(open_link.execute({"url": url}, _ctx()))
        assert any(e.get("url") == url for e in events), url


# --- execute: safety (never open a non-web URI) ----------------------------------------------------

@pytest.mark.parametrize("bad", [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,<script>1</script>",
    "ftp://host/x",
    "",
    "   ",
    "example.com",
])
def test_open_link_rejects_non_web_uris(monkeypatch, bad):
    """The safety half: no card, ever, for anything that is not http(s) — a bare host included.

    The refusal used to be `return None`, which the loop grades `failed` and answers with the FAIL
    pattern — "that one didn't go through", spoken about a link nothing ever tried to open, and with
    nothing said to the model that could get a missing scheme fixed. (Dormant on a reasoning model,
    which suppresses canned narration; live on the stock `.env.example` config.) It is now witnessed
    as a refusal, like the no-screen branch beside it, so no line is spoken and the model is told what
    actually happened. The rejected URI is never echoed back, either."""
    events = _capture_events(monkeypatch)
    ctx = _ctx()
    result = asyncio.run(open_link.execute({"url": bad}, ctx))
    assert not any(e["kind"] == "need_input" for e in events), f"must NOT emit a card for {bad!r}"
    assert isinstance(result, str) and "NOTHING was shown" in result, f"must say so for {bad!r}"
    assert bad.strip() not in result or not bad.strip()
    from kotoba.core.loop import tool_refused

    assert tool_refused(ctx, ctx.call_id), "the refusal must leave a witness, or it is graded a success"


def test_open_link_card_helper_payload(monkeypatch):
    """interaction.open_link_card emits the exact contract the frontend reads."""
    events = _capture_events(monkeypatch)
    asyncio.run(interaction.open_link_card("s2", "https://src.example", why="why text"))
    card = next(e for e in events if e["kind"] == "need_input")
    assert card["mode"] == "open_link" and card["input_kind"] == "link"
    assert card["url"] == "https://src.example" and card["label"] == "why text"
    assert card["wait"] is False


def test_open_link_card_noop_without_session(monkeypatch):
    """With no session there is no SSE channel to target, so the helper emits nothing at all."""
    events = _capture_events(monkeypatch)
    asyncio.run(interaction.open_link_card(None, "https://x.example", "y"))
    assert events == []


# --- the research skill ----------------------------------------------------------------------------

def test_research_skill_present_and_eligible():
    """Discoverable, described in what-plus-when terms, and eligible with no toolsets active.

    The description has to say what the skill does AND when to reach for it (trigger words), or the
    model never fires it. It declares no `requires_toolsets`, which is what makes it available in a
    bare companion turn."""
    from kotoba.core import skill_docs

    skills = skill_docs.list_skills()
    research = next((s for s in skills if s["name"] == "research"), None)
    assert research is not None, "research skill must be discoverable"
    desc = research["description"].lower()
    assert "research" in desc or "investigate" in desc
    assert research.get("requires_toolsets") == []
    eligible = {s["name"] for s in skill_docs.skills_for_toolsets(set())}
    assert "research" in eligible


def test_research_skill_body_loads():
    """The body still carries its two load-bearing halves.

    The companion/work split (Phase 1 / Phase 2) is the core of the skill, and the report it produces
    goes into the workspace `research/` folder, written once, with real URLs."""
    from kotoba.core import skill_docs

    body = skill_docs.view_skill("research")
    assert body and "Phase 1" in body and "Phase 2" in body
    assert "research/" in body and "write_file" in body


# --- citation markers: strip the opaque web_search runs, capture the real URLs --------------------

_E200, _E201, _E202 = chr(0xE200), chr(0xE201), chr(0xE202)


def test_strip_citation_markers_removes_run():
    from kotoba.core.stream import strip_citation_markers

    raw = "Green tea has caffeine." + _E200 + "cite" + _E202 + "turn0search0" + _E202 + "turn0search2" + _E201 + " It helps."
    out = strip_citation_markers(raw)
    assert out == "Green tea has caffeine. It helps."
    assert "cite" not in out and _E200 not in out and _E201 not in out


def test_strip_citation_markers_drops_stray_pua():
    from kotoba.core.stream import strip_citation_markers

    assert strip_citation_markers("alert" + _E202 + "ness") == "alertness"
    assert strip_citation_markers("") == ""
    assert strip_citation_markers("no markers here") == "no markers here"


def test_spoken_scrub_strips_citation_markers():
    """The production spoken-text filter (UrlFilter layer) must never voice a citation run."""
    from kotoba.core import stream

    u = stream.UrlFilter()
    raw = "Listo." + _E200 + "cite" + _E202 + "turn0search0" + _E201 + " Eso es todo."
    out = (u.feed(raw) + u.flush())
    assert "cite" not in out and _E200 not in out


def test_citations_collect_dedups_and_filters():
    """Only `url_citation` annotations with a web URL are kept, and a repeat url does not duplicate.

    Anything of another annotation type, and any non-web scheme, is dropped on the way in. Both the
    mapping shape and the attribute shape of an annotation are accepted."""
    from kotoba.core import citations

    ctx = types.SimpleNamespace()
    citations.collect_from_annotation(ctx, {"type": "url_citation", "url": "https://a.example", "title": "A"})
    citations.collect_from_annotation(ctx, {"type": "url_citation", "url": "https://a.example", "title": "dup"})
    citations.collect_from_annotation(ctx, {"type": "other", "url": "https://b.example"})
    citations.collect_from_annotation(ctx, {"type": "url_citation", "url": "ftp://c.example"})
    citations.collect_from_annotation(ctx, types.SimpleNamespace(type="url_citation", url="https://d.example", title="D"))
    assert ctx._sources == {"https://a.example": "A", "https://d.example": "D"}


def _spoken(text):
    """Run text through the production UrlFilter (where URLs / file paths / citation markers are stripped)."""
    from kotoba.core import stream

    u = stream.UrlFilter()
    return u.feed(text) + u.flush()


def test_spoken_strips_file_path_and_name():
    out = _spoken("lo guardé en research/caminar-treinta-minutos-al-dia-2026-06-14.md, ¿ok?")
    assert ".md" not in out and "research/" not in out and "caminar" not in out
    out2 = _spoken("dejé el scraper en scraper.py para ti.")
    assert "scraper.py" not in out2


def test_spoken_no_orphan_paren_from_citation_or_url():
    """Stripping a citation or a URL must take its parentheses with it, or an orphan is spoken.

    A citation marker inside parens leaves the parens empty, and an empty pair was read aloud as a
    stray bracket. A whole `(label: url)` parenthetical goes the same way — removed entirely, never
    left as an orphan `(label`."""
    raw = "los beneficios y matices (" + _E200 + "cite" + _E202 + "turn0search0" + _E201 + "). Eso es todo."
    out = _spoken(raw)
    assert "(" not in out and ")" not in out and "cite" not in out
    out2 = _spoken("La fuente principal (Reuters: https://reuters.com/ai) lo confirma.")
    assert "(" not in out2 and "Reuters" not in out2 and "http" not in out2


def test_spoken_keeps_tech_names_and_numbers():
    """The file-path net must not eat spoken tech names or decimals on its way past a dot."""
    assert "Node.js" in _spoken("uso Node.js en el proyecto")
    assert "3.5" in _spoken("caminé 3.5 km hoy")
    assert _spoken("te lo dejé en tus archivos") == "te lo dejé en tus archivos"


def test_citations_pending_note_is_delta_then_none():
    """The note is a DELTA: each url is handed over once, and nothing new means no note at all.

    Re-injecting the whole list every turn is what makes the model repeat sources it already had."""
    from kotoba.core import citations

    ctx = types.SimpleNamespace()
    citations.collect_from_annotation(ctx, {"type": "url_citation", "url": "https://a.example", "title": "A"})
    note = citations.pending_note(ctx)
    assert note and "https://a.example" in note
    assert citations.pending_note(ctx) is None
    citations.collect_from_annotation(ctx, {"type": "url_citation", "url": "https://e.example", "title": "E"})
    note2 = citations.pending_note(ctx)
    assert note2 and "https://e.example" in note2 and "https://a.example" not in note2


def test_citations_pending_note_cannot_be_copied_into_a_report():
    """The old note opened with an instruction ending in a colon, directly above the URL list — and the
    model copied "Use these real URLs verbatim:" into a report as a lead-in under ## Sources / Fuentes.
    Nothing in the note may read as a heading or a lead-in for that list."""
    from kotoba.core import citations

    ctx = types.SimpleNamespace()
    citations.collect_from_annotation(ctx, {"type": "url_citation", "url": "https://real.example/doc",
                                            "title": "Doc"})
    note = citations.pending_note(ctx)
    assert note and "https://real.example/doc" in note, "it must still hand over the real URL"

    head = note.split("\n- ", 1)[0]                       # everything above the URL list
    lines = [ln.strip() for ln in head.splitlines() if ln.strip()]
    assert not any(ln.endswith(":") for ln in lines), "a colon-terminated line reads as a list lead-in"
    assert not any(ln.startswith(("#", "-", "*")) for ln in lines), "no heading or bullet to copy"
    low = head.lower()
    for phrase in ("use these", "verbatim", "fuentes section", "sources found"):
        assert phrase not in low, f"{phrase!r} reads as report prose"
    assert low.startswith("[internal]"), "the note must announce itself as internal"
    assert "not content" in low, "and say outright that it is not content"
