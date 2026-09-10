"""make_report's refusal must be narrow enough to be true, and visible enough to count as a refusal.

It matched "with links" anywhere, including ordinary prose fields like steps or results, so five
realistic reports resting on no source at all were refused in a row, each time telling her to remove a
TRUE sentence about what she had done. Worse: a refusal is a non-empty string, so the heartbeat runner
graded it a success — an executed audit row was written for a report never made, a green checkmark was
drawn whose text was the refusal, and a verbatim retry hit the duplicate guard's success branch,
insisting the failed run was already done. That is this project's most-tracked defect family — a
refusal reported as a success — entering through a fourth door: an in-tool refusal no witness saw.
`note_tool_refusal` is now that witness, alongside the two call sites that already had one."""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.core.loop as loop
import kotoba.tools.action.make_report as mr
from kotoba.core import events, reports
from kotoba.core.loop import _nothing_ran, _step_outcome, execute_with_heartbeat, note_tool_refusal
from kotoba.tools import ToolContext


@pytest.fixture(autouse=True)
def _tmp_library(tmp_path, monkeypatch):
    """Never touch the user's real ~/.kotoba while producing reports."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "memory"))


def _run(args: dict, sid: str):
    ctx = ToolContext(db=None, session_id=sid, mode="work")
    out = asyncio.run(mr.execute(args, ctx))
    return out, reports.get_report(sid)


def _refused(out) -> bool:
    return bool(out) and "did NOT make that report" in out


# --- a report ABOUT links is not a report that CITES any ----------------------------------------------

@pytest.mark.parametrize("i,args", list(enumerate([
    {"title": "Menú del restaurante", "summary": "Actualicé la carta de la web.",
     "steps": ["dejé el menú con los enlaces nuevos"]},
    {"title": "Auditoría de enlaces", "summary": "Revisé el sitio entero.",
     "results": ["tres páginas con los enlaces caídos"]},
    {"title": "Footer rebuild", "summary": "Rebuilt the site footer.",
     "steps": ["Rebuilt the footer with links to your socials"]},
    {"title": "Blog cleanup", "summary": "Tidied the blog.",
     "next_steps": ["Add a related-reading block with links to the docs"]},
    {"title": "Migración", "summary": "Migré la base de datos.",
     "files": ["migracion.md — incluye los enlaces a cada tabla"]},
])))
def test_an_ordinary_report_that_rests_on_no_source_is_produced(i, args):
    """Five realistic reports, in both languages, whose prose mentions links because the JOB was about
    links. None of them promises a citation, so none of them may be refused."""
    out, html = _run(args, f"witness-ok-{i}")
    assert not _refused(out), f"refused a true sentence about the work: {out}"
    assert "Report ready" in out and html


@pytest.mark.parametrize("i,args", list(enumerate([
    {"title": "Informe formal de actualización de la web",
     "summary": "Este informe resume el trabajo realizado en la web y deja el menu con los enlaces nuevos."},
    {"title": "Informe", "summary": "Resumen del trabajo.",
     "results": ["Este informe recoge las tres paginas con los enlaces caidos que se arreglaron."]},
    {"title": "Reporte de cambios realizados en la web",
     "summary": "Reporte de cambios: se arreglaron las paginas con los enlaces caidos."},
])))
def test_naming_the_report_does_not_make_it_rest_on_sources(i, args):
    """Live QA on the running server, a pure website job with nothing to look up: refused twice, produced
    only on the third reworded attempt. The research signal contained `informe`/`reporte`/`report`, and
    "Este informe…" is how every report summary in Spanish begins — so the door opened for work reports
    shut again the moment she named what she was writing. Every report is a report; that is not evidence
    it rests on anything."""
    out, html = _run(args, f"witness-named-{i}")
    assert not _refused(out), f"refused for naming its own genre: {out}"
    assert "Report ready" in out and html


def test_what_the_links_hang_off_is_what_decides():
    """The distinction the predicate rests on, stated on its own: the deliverable carrying links is a
    claim about the citations; a menu or three pages carrying links is a claim about a website."""
    assert mr._promises_links({"title": "T", "summary": "Preparé el informe con los enlaces dentro."})
    assert mr._promises_links({"title": "T", "summary": "Te dejo el reporte con los enlaces."})
    assert not mr._promises_links({"title": "T", "summary": "Dejé el menú con los enlaces nuevos."})
    assert not mr._promises_links({"title": "T", "summary": "Arreglé las páginas con los enlaces caídos."})
    assert not mr._RESEARCH_RE.search("este informe reporte report write-up"), \
        "the artifact's own name is not evidence of research"


@pytest.mark.parametrize("i,args", list(enumerate([
    {"title": "SDK", "summary": "Informe breve del SDK, con enlaces a las fuentes oficiales."},
    {"title": "SDK", "summary": "A short write-up, with links to the sources."},
    {"title": "SDK", "summary": "Resumen.", "results": ["Preparé el informe con los enlaces dentro."]},
    {"title": "SDK", "summary": "Resumen.", "next_steps": ["Te lo amplío incluyendo las URLs."]},
    {"title": "SDK", "summary": "Resumen del estudio, incluyendo las fuentes consultadas."},
    {"title": "SDK", "summary": "Research write-up, including the citations."},
    {"title": "SDK", "summary": "Resumen.",
     "results": ["Busqué en la documentación y te dejo aquí los enlaces."]},
])))
def test_a_real_promise_of_sources_with_nothing_to_cite_is_still_refused(i, args):
    """The narrowing must not cost the refusal its job: a report that promises the reader sources,
    citations or URLs, with nothing collected to cite, is still refused — and no artifact is left
    behind, because the artifact itself would carry the promise."""
    out, html = _run(args, f"witness-no-{i}")
    assert _refused(out), f"a promise of citations went unrefused: {args}"
    assert html is None, "the artifact must not exist — it would advertise a citation it hasn't got"


def test_the_same_promise_passes_once_there_is_something_to_cite():
    """The identical summary that was refused above goes through once the context actually carries a
    source, and the return says how many were cited."""
    ctx = ToolContext(db=None, session_id="witness-cited", mode="work")
    ctx._sources = {"https://github.com/Live2D/CubismWebSamples/releases": "Releases"}
    out = asyncio.run(mr.execute(
        {"title": "SDK", "summary": "Informe breve, con enlaces a las fuentes oficiales."}, ctx))
    assert "Report ready" in out and "cites 1 source" in out


# --- the witness: an in-tool refusal is not a success --------------------------------------------------

def test_note_tool_refusal_is_the_third_witness_nothing_ran_asks():
    """Same shape as core.interaction.note_no_run and core.deferred_exec.ran_nothing: keyed by the
    call_id in flight, read by _nothing_ran, and blind to every other call."""
    ctx = ToolContext(db=None, session_id="witness-w", mode="work")
    ctx.call_id = "c1"
    assert _nothing_ran(ctx, "c1") is False
    note_tool_refusal(ctx)
    assert _nothing_ran(ctx, "c1") is True
    assert _nothing_ran(ctx, "c2") is False, "a refusal must not spill onto the next call"


def test_the_refusal_is_not_graded_ok():
    """A refusal is a non-empty return, which used to be all the grader looked at.

    Three things have to come out of it: not ok, the full explanation still handed to the model so it
    knows what to do differently, and an outcome of `refused` — neither `ok` nor `failed`, because
    nothing ran."""
    async def go():
        ctx = ToolContext(db=None, session_id="witness-grade", mode="work")
        ctx.call_id = "c1"
        ok, result = await execute_with_heartbeat(
            "make_report", {"title": "SDK", "summary": "Informe, con enlaces a las fuentes oficiales."},
            asyncio.Queue(), {}, ctx,
        )
        return ok, result, _step_outcome(ctx, "c1", ok, False, [])

    ok, result, outcome = asyncio.run(go())
    assert ok is False, "a refusal graded ok draws a green ✓ over a report that was never made"
    assert _refused(result), "the model must still be told exactly why, and what to do instead"
    assert outcome == "refused", "not `ok`, and not `failed` either — nothing ran"


def test_a_report_that_was_really_made_is_still_ok():
    """The other side of the grader change: an ordinary report is still a success."""
    async def go():
        ctx = ToolContext(db=None, session_id="witness-good", mode="work")
        ctx.call_id = "c1"
        ok, _ = await execute_with_heartbeat(
            "make_report", {"title": "Fine", "summary": "Un informe normal."},
            asyncio.Queue(), {}, ctx,
        )
        return ok, _step_outcome(ctx, "c1", ok, False, [])

    assert asyncio.run(go()) == (True, "ok")


# --- through the loop: the row, the audit trail, and the retry -----------------------------------------

def _done(item):
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _call(call_id, name, args):
    return _done(types.SimpleNamespace(type="function_call", name=name,
                                       arguments=json.dumps(args), call_id=call_id))


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _Client:
    def __init__(self, turns):
        self.turns, self._i = turns, 0
        self.responses = self

    async def create(self, **kw):
        evs = self.turns[min(self._i, len(self.turns) - 1)]
        self._i += 1
        return _Stream(evs)


class _DB:
    def __init__(self):
        self.audit = []

    async def insert_audit_log(self, **kw):
        self.audit.append(kw)


def _turn(sid: str, args: dict, repeats: int = 1):
    """Drive the REAL make_report through the loop and collect what the user's screen and trail get."""
    turns = [[_call(f"c{i}", "make_report", args)] for i in range(repeats)]
    turns.append([_done(types.SimpleNamespace(type="message", content=[]))])
    db = _DB()
    input_items = [{"role": "user", "content": "hazme el informe"}]

    async def go():
        q = events.register(sid)
        ctx = ToolContext(db=db, session_id=sid, client=None, mode="work")
        ctx.approval = None
        try:
            await loop._run_iterations(
                _Client(turns), ctx, input_items, asyncio.Queue(), {},
                max_iterations=len(turns), mode="work",
                allow_risk={"read", "write", "exec", "network"}, toolset_filter=None,
            )
            return [q.get_nowait() for _ in range(q.qsize())]
        finally:
            events.unregister(sid, q)

    frames = asyncio.run(go())
    steps = [f for f in frames if f.get("kind") == "step" and f.get("phase") == "done"]
    outputs = [i for i in input_items if isinstance(i, dict) and i.get("type") == "function_call_output"]
    return db.audit, steps, outputs


_PROMISE = {"title": "Estado del SDK", "summary": "Informe breve, con enlaces a las fuentes oficiales."}


def test_the_loop_writes_no_executed_row_for_a_report_it_never_made():
    """End to end through the loop: a refused report leaves NO audit row claiming it executed, and the
    step the user sees is marked refused rather than ticked."""
    audit, steps, _ = _turn("witness-loop-refused", _PROMISE)
    assert audit == [], f"an `executed` audit row for a report that does not exist: {audit}"
    assert [s["outcome"] for s in steps] == ["refused"]
    assert steps[0]["ok"] is False, "the ✓ said the report was made"


def test_a_report_that_was_made_still_logs_and_still_draws_a_tick():
    """And the audit trail is not lost for real reports — one `executed` row, one ticked step."""
    audit, steps, _ = _turn("witness-loop-ok", {"title": "Fine", "summary": "Un informe normal."})
    assert [r["detail"] for r in audit] == ["executed"]
    assert [s["outcome"] for s in steps] == ["ok"] and steps[0]["ok"] is True


def test_a_verbatim_retry_is_not_told_the_report_is_done():
    """The duplicate guard branches on the recorded outcome, so a refusal graded ok answered the retry
    with "it is DONE — the earlier result still stands. Do NOT say it failed."""
    _, _, outputs = _turn("witness-loop-retry", _PROMISE, repeats=2)
    assert len(outputs) == 2
    assert "did NOT succeed" in outputs[1]["output"]
    assert "it is DONE" not in outputs[1]["output"]
