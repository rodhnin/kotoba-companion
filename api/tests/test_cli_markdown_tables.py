"""A markdown table reaches the transcript as a table, whole, at any width.

The long job's summary was a five-column price comparison and `/work 1` drew it as wrapped pipes:
`|---|---|`, `**bold**`, a literal `<br>`, the newlines flattened, cut at the 500th character. Three
causes, none of them "no table support": rich has rendered GFM tables since 13.0 and `Prose` is rich's
`Markdown` — but the receipt never went through it (`slash._work`, `app._land_work` used `text.wrap`),
rich drops `<br>` and ellipsises table cells, and the `work_done` frame carries only the first 500
characters.
"""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from rich.cells import cell_len

from kotoba.cli import slash, state
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.markdown import Blocks, prose
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

TABLE = (
    "| Servicio | Planes y precio mensual | Facturación anual | Límites o unidades incluidas "
    "| Facturación y notas |\n"
    "|---|---|---|---|---|\n"
    "| **ElevenLabs** | Gratis: **$0** — 10.000 créditos/mes.<br>Starter: **$6** — 30.000 créditos."
    "<br/>Creator: **$22** — 121.000 créditos.<BR />Pro: **$99** — 500.000 créditos. "
    "| Sí, ~2 meses gratis | Créditos de texto a voz | Suscripción, impuestos aparte |\n"
    "| **OpenAI ChatGPT** | Plus: **$20**<br>Pro: **$200** | No | Uso ampliado de modelos "
    "| Por usuario |\n"
    "| **Railway** | Hobby: **$5**<br>Pro: **$20** | No | $5 de uso incluido en Hobby "
    "| CPU, RAM, disco y tráfico por consumo |"
)
SUMMARY = ("## Comparativa de precios\n\n> Precios en **USD**, antes de impuestos.\n\n" + TABLE
           + "\n\n**Conclusión:** ElevenLabs tiene el descuento anual más claro.")
WORDS = ("ElevenLabs", "créditos/mes.", "Starter:", "Creator:", "Suscripción,", "Facturación",
         "incluidas", "Independiente" if "Independiente" in TABLE else "consumo")


def screen_at(width: int, height: int = 30) -> Screen:
    caps = Caps(color="none", background="dark", unicode=True, width=width, height=height,
                g=dict(GLYPHS_UNICODE))
    return Screen(caps, console=build_console(caps, file=io.StringIO()),
                  portrait=Portrait(caps, wanted=False))


def drawn(md: str, width: int) -> list[str]:
    screen = screen_at(width)
    screen.console.print(screen.at_gutter(prose(md, screen.caps)))
    return screen.console.file.getvalue().rstrip("\n").split("\n")


def wired(height: int = 40) -> tuple[App, io.StringIO]:
    screen = screen_at(96, height)
    app = App(screen.caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1", events=SimpleNamespace(steps={}, settle=lambda: None))
    return app, screen.console.file


# ── the renderer ─────────────────────────────────────────────────────────────────────────────────

def test_a_table_is_drawn_as_a_table_and_not_as_a_row_of_pipes():
    out = "\n".join(drawn(TABLE, 96))
    assert "|" not in out and "---|" not in out, out
    assert "Servicio" in out and "ElevenLabs" in out


def test_br_inside_a_cell_is_a_line_break_and_never_a_dropped_tag():
    """Rich drops every inline HTML tag but `<kbd>`, so `créditos.<br>Creator:` became one word and
    was ellipsised as one. The tag means a line break, in any of the three spellings a model uses."""
    lines = drawn(TABLE, 96)
    out = "\n".join(lines)
    assert "<br" not in out.lower() and "créditos.Creator" not in out, out
    rows = [ln for ln in lines if "Starter: $6" in ln or "Creator: $22" in ln or "Pro: $99" in ln]
    assert len(rows) == 3, lines


def test_br_in_a_paragraph_is_a_line_break_too():
    lines = drawn("uno<br>dos<br/>tres", 96)
    assert [ln.strip() for ln in lines if ln.strip()] == ["uno", "dos", "tres"], lines


@pytest.mark.parametrize("width", (120, 96, 80, 60, 40, 30))
def test_nothing_in_a_table_is_cut_or_wider_than_the_window(width):
    """rich's table columns ELLIPSISE by default — `ElevenLa…` at sixty columns — and a renderer may
    lose nothing. Folded instead, and stacked when even the fold would break words."""
    lines = drawn(TABLE, width)
    out = "\n".join(lines)
    assert "…" not in out, out
    for ln in lines:
        assert cell_len(ln) <= width, f"{cell_len(ln)} cells at {width}: {ln!r}"
    if width >= 40:
        for word in WORDS:
            assert word in out, f"{word!r} lost at {width}: {out}"


def test_a_narrow_window_stacks_the_rows_and_a_wide_one_keeps_the_grid():
    wide = "\n".join(drawn(TABLE, 96))
    narrow = "\n".join(drawn(TABLE, 60))
    assert wide.count("Servicio") == 1 and "───" in wide, wide
    assert narrow.count("Servicio") == 3 and "───" not in narrow, narrow


def test_a_table_streams_as_one_block_and_draws_at_every_partial():
    """`Blocks` ends a block at a blank line and a table has none inside it, so it lands whole; while it
    is in flight `partial` is rendered as-is, and a half-arrived row must draw rather than raise."""
    blocks = Blocks()
    assert blocks.feed(TABLE + "\n") == []
    assert blocks.feed("\n") == [TABLE]
    lines = TABLE.split("\n")
    for cut in (1, 2, 3):
        partial = "\n".join(lines[:cut]) + "\n" + lines[cut][:20]
        for ln in drawn(partial, 60):
            assert cell_len(ln) <= 60, ln


# ── the receipt ──────────────────────────────────────────────────────────────────────────────────

def test_work_n_draws_the_summary_through_the_renderer():
    app, buf = wired()
    app.work = state.Work("the prices", n=1, state="ok", summary=SUMMARY)
    app.works = [app.work]
    asyncio.run(slash.run(app, commands.Command("/work", "1")))
    out = buf.getvalue()
    assert "**" not in out and "|---" not in out and "<br>" not in out, out
    assert "Comparativa de precios" in out and "Starter: $6" in out and "Conclusión" in out, out


def test_work_n_cuts_a_summary_by_rows_and_says_how_many_it_kept_back():
    app, buf = wired(height=22)
    app.work = state.Work("the prices", n=1, state="ok", summary=SUMMARY)
    app.works = [app.work]
    asyncio.run(slash.run(app, commands.Command("/work", "1")))
    out = buf.getvalue()
    assert "Comparativa de precios" in out, out
    assert "rows of the summary not drawn" in out, out
    assert "Conclusión" not in out, out


def test_a_stopped_jobs_summary_goes_through_the_renderer_too():
    app, buf = wired()
    app.work = state.Work("the prices", state="interrupted", summary="**stopped** — nothing kept")
    assert app._land_work() is True
    out = buf.getvalue()
    assert "**" not in out and "stopped" in out and "nothing kept" in out, out


def test_the_work_done_frame_takes_the_whole_summary_from_the_record_it_names():
    """The wire carries 500 characters, sized for the web's contextual update,
    which never draws it. The CLI draws it and runs in process, where `work_state` holds the whole text
    the runner wrote a line before it emitted the frame — taken only when the record is this run's."""
    from kotoba.core import work_state

    long = SUMMARY * 3
    assert len(long) > 500
    app, _ = wired()
    app.session.session_id = "b5_whole"
    try:
        work_state.start("b5_whole", "the prices", run_id="r1")
        work_state.finish("b5_whole", long, [], run_id="r1")
        app.work = state.Work("the prices", n=1, run_id="r1")
        app.works = [app.work]
        app._event("work_done", {"kind": "work_done", "ok": True, "summary": long[:500],
                                 "run_id": "r1"})
        assert app.work.summary == long
    finally:
        work_state.clear("b5_whole")


def test_a_record_of_another_run_hands_back_the_frames_own_text():
    from kotoba.core import work_state

    app, _ = wired()
    app.session.session_id = "b5_other"
    try:
        work_state.start("b5_other", "something else", run_id="r2")
        work_state.finish("b5_other", "somebody else's whole summary", [], run_id="r2")
        app.work = state.Work("the prices", n=1, run_id="r1")
        app.works = [app.work]
        app._event("work_done", {"kind": "work_done", "ok": True, "summary": "the frame's",
                                 "run_id": "r1"})
        assert app.work.summary == "the frame's"
    finally:
        work_state.clear("b5_other")
