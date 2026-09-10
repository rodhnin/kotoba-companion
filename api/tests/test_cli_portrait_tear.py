"""The two mechanisms that tore her portrait, pinned so they stay fixed.

Mechanism A: a machine row committing while her block is still un-staged used to print on the live
region's first row — her face — and the reply's sixel never came back. `Screen.row` now holds such a
row in `pending` until her block lands; the lead flag has one owner, `Screen.rows_committed`, never
the app snapshot's copy, which raises before the row has landed.

Mechanism B: at the prompt, the band painted its rows over the tail of a freshly landed portrait. The
band now cedes to her face the way it cedes to a list, keeping its top rows in whatever is free
beneath her and going absent when none are."""
from __future__ import annotations

import io
import re
import time
from types import SimpleNamespace

from prompt_toolkit.completion import Completion
from rich.text import Text

from kotoba.cli import state
from kotoba.cli.input import menu as panels
from kotoba.cli.input.menu import Menu
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.markdown import prose
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.region import LiveView
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

AT = re.compile(r"\x1b\[([0-9]+);1H")
RECEIPT = "PLAN receipt row landing mid-partial"


def a_caps(**over) -> Caps:
    base = dict(color="truecolor", background="dark", unicode=True, interactive=True,
                width=96, height=24, g=dict(GLYPHS_UNICODE))
    base.update(over)
    return Caps(**base)


def sixel_screen(caps=None) -> tuple[Screen, io.StringIO]:
    caps = caps or a_caps(sixel=True)
    box = Portrait(caps, wanted=False)
    box.mode, box.cols, box.rows, box.icols, box.irows = "sixel", 12, 5, 12, 5
    box._sixel = lambda emotion, scale: (f"<SIXEL {emotion}>", 12, 5)
    buf = io.StringIO()
    console = build_console(caps, file=buf, force_terminal=True)
    console.width = caps.width
    return Screen(caps, console=console, portrait=box), buf


def her_partial_owns_the_region(screen: Screen) -> None:
    """The exact state of mechanism A: portrait painted live, nothing of hers committable."""
    screen.begin_turn()
    screen.face.set("neutral", instant=True)
    screen.face.set("happy", instant=True)
    first, tall, payload = screen.face_frame(0)
    screen.erase_span = (tall + 2, frozenset(range(first, first + tall)), screen.gutter)
    assert payload and screen.held == "" and not screen.said_plate


def test_a_receipt_over_her_unstaged_partial_waits_and_lands_below_her_block():
    screen, buf = sixel_screen()
    her_partial_owns_the_region(screen)
    screen.row(Text(RECEIPT))
    assert RECEIPT not in buf.getvalue(), "the row printed onto her face"
    assert [r.plain for r in screen.pending] == [RECEIPT]
    assert screen.rows_committed is False, "a row that has not landed must not buy her block a lead"
    screen.say("Voy cerrando el plan y te cuento en un momento.", last=True)
    out = buf.getvalue()
    assert screen.pending == [] and screen.rows_committed is True
    assert 0 < out.index("Voy cerrando el plan") < out.index(RECEIPT), \
        "the transcript keeps the order the glass showed: she spoke first"
    assert "<SIXEL" not in out, "one sixel per reply — the committed copy re-emits nothing"


def test_deferred_rows_keep_arrival_order_even_past_the_state_that_deferred_them():
    screen, buf = sixel_screen()
    her_partial_owns_the_region(screen)
    screen.row(Text("first: deferred"))
    screen.erase_span = None
    screen.row(Text("second: prints"))
    out = buf.getvalue()
    assert 0 < out.index("first: deferred") < out.index("second: prints")


def test_end_turn_is_the_belt_that_lands_what_nothing_else_flushed():
    screen, buf = sixel_screen()
    her_partial_owns_the_region(screen)
    screen.row(Text(RECEIPT))
    assert RECEIPT not in buf.getvalue()
    screen.end_turn()
    assert RECEIPT in buf.getvalue() and screen.pending == []


def test_a_row_with_her_block_staged_still_flushes_and_prints_as_it_always_did():
    screen, buf = sixel_screen()
    screen.begin_turn()
    screen.face.set("happy", instant=True)
    screen.say("Un primer bloque corto.")
    assert screen.held
    screen.row(Text(RECEIPT))
    out = buf.getvalue()
    assert 0 < out.index("Un primer bloque corto.") < out.index(RECEIPT)
    assert screen.pending == []


class P:
    def __init__(self, screen, caps):
        self.screen, self.caps = screen, caps
        self.spin = rows.Spin(caps)

    def tool_text(self, tool):
        return rows.tool_text(self.caps, tool, self.screen.rw, spin=self.spin)

    def roster_rows(self):
        return []

    def approval_rows(self):
        return [Text("APPROVAL CARD ROW")]

    def seated_rows(self):
        return []

    def confirm_rows(self):
        return []

    def head_plate(self, live=False):
        return self.screen.plate(live=live)

    def at_gutter(self, row):
        return self.screen.at_gutter(row)

    def prose(self, block):
        return self.screen.at_gutter(prose(block, self.caps))

    def link_rows(self, block):
        return []


def region_lines(screen, st) -> list[str]:
    parts = P(screen, screen.caps)
    view = LiveView(screen, parts.spin, lambda: st, parts)
    with screen.console.capture() as cap:
        screen.console.print(view)
    return cap.get().split("\n")


def rowed(lines: list[str], said: str) -> int:
    hits = [i for i, line in enumerate(lines) if said in line]
    assert hits, (said, lines)
    return hits[0]


def test_the_region_draws_pending_under_her_block_and_above_what_is_still_running():
    screen, _ = sixel_screen()
    screen.pending = [Text(RECEIPT)]
    tool = state.Tool("web", "web_search 'live2d'", started=time.monotonic())
    st = footer.State(model="m", turn_start=time.monotonic(), partial="Voy cerrando el plan.",
                      tools=(tool,))
    lines = region_lines(screen, st)
    prose_at, landed_at = rowed(lines, "Voy cerrando"), rowed(lines, RECEIPT)
    assert prose_at < landed_at < rowed(lines, "web_search"), \
        "under her block, above the running row that lands later"
    assert lines[landed_at - 1].strip() == "", "the blank her gap will spend is already held"


def test_with_nothing_running_the_pending_row_still_draws_below_her_with_its_blank():
    screen, _ = sixel_screen()
    screen.pending = [Text(RECEIPT)]
    st = footer.State(model="m", turn_start=time.monotonic(), partial="Voy cerrando el plan.")
    lines = region_lines(screen, st)
    landed_at = rowed(lines, RECEIPT)
    assert rowed(lines, "Voy cerrando") < landed_at
    assert lines[landed_at - 1].strip() == ""


def test_with_her_block_off_the_glass_pending_rides_at_the_top_where_it_would_print():
    screen, _ = sixel_screen()
    screen.pending = [Text(RECEIPT)]
    st = footer.State(model="m", turn_start=time.monotonic(), partial="Voy cerrando el plan.",
                      approval=object())
    lines = region_lines(screen, st)
    assert rowed(lines, RECEIPT) < rowed(lines, "APPROVAL CARD ROW")


def test_the_lead_is_read_off_the_screens_flag_never_the_snapshots_stale_copy():
    """The real `App._commit` raises its `rows_committed` BEFORE the row lands; while `Screen.row`
    holds that row back, a region trusting the snapshot would slide a blank above her painted block
    and walk her face off by one row."""
    screen, _ = sixel_screen()
    screen.pending = [Text(RECEIPT)]
    st = footer.State(model="m", turn_start=time.monotonic(), partial="Voy cerrando el plan.",
                      rows_committed=True)
    lines = region_lines(screen, st)
    assert "KOTOBA" in lines[0], "no lead blank the committed copy will never print"
    screen.pending = []
    screen.rows_committed = True
    lines = region_lines(screen, st)
    assert lines[0].strip() == "" and "KOTOBA" in lines[1], \
        "and a lead the screen itself owes is still drawn"


def banked_face(screen: Screen, top: int, anchor: int) -> None:
    """Her landing in `Screen.tail`: the art anchored so the face spans `anchor .. anchor+4`.
    The last banked row is `top - 1`, so the pads after the anchor place it: `top - 1 - anchor`."""
    for i in range(9):
        screen.kept([f"transcript row {i:02d}"])
    screen.kept(["   " + " " * 12 + "her announcement beside the art"], "<SIXEL excited>")
    for _ in range(top - 1 - anchor):
        screen.kept([""])


def at_the_prompt(screen, band_rows_fn, bottom: int = 20):
    prompt = SimpleNamespace(
        session=SimpleNamespace(default_buffer=SimpleNamespace(complete_state=None)),
        picker=None, resizes=0, geometry=lambda h: (bottom, 15))
    menu = Menu(screen, prompt)
    menu.band = band_rows_fn
    return menu


def a_band(caps, steps=("done", "active", "pending"), room: int = 0) -> list:
    job = state.Work("get the real latency numbers", 1, t0=time.monotonic() - 72.0)
    job.tools.append(state.Tool("web", "web_search 'first byte'", started=time.monotonic() - 6.0))
    plan = state.Plan({"list_id": "L1", "title": "informe", "status": "open",
                       "tasks": [{"id": str(i), "text": f"paso {i}", "status": s, "order": i}
                                 for i, s in enumerate(steps, 1)]})
    st = footer.State(model="m", work=job, plan=plan, at_rest=True)
    return rows.band_rows(caps, st, max(20, caps.width - 1), room=room)


def test_the_band_folds_to_the_free_rows_beneath_her_face_and_the_plan_takes_the_last(monkeypatch):
    """RE-RECORDED. It used to be row one — the action and its clock — that survived the fold, and the
    plan that went. Out here row one is the bar's own phrase and clock, repeated one row higher; the
    plan step is on no other surface, and a six-step job showing one step and no `… +N pending` is
    what the fold was measured doing in a real terminal."""
    screen, _ = sixel_screen()
    monkeypatch.setattr(screen.caps, "sync_size", lambda: None)
    banked_face(screen, top=15, anchor=14)
    written: list[str] = []
    monkeypatch.setattr(panels, "_emit", written.append)
    menu = at_the_prompt(screen, lambda room=0: a_band(screen.caps, room=room))
    menu.sync()
    painted = "".join(written)
    touched = {int(m) - 1 for m in AT.findall(painted)}
    assert touched == {19}, "her face spans 14..18 — the one free row is the band's whole surface"
    assert "paso 2" in painted, "the active step, which nothing else on the glass is saying"
    assert "looking that up…" not in painted
    menu.sync()
    assert "".join(written) == painted, "folded and unchanged writes nothing"


def test_with_no_free_row_beneath_her_the_band_is_absent_not_painted_into_her(monkeypatch):
    screen, _ = sixel_screen()
    monkeypatch.setattr(screen.caps, "sync_size", lambda: None)
    banked_face(screen, top=15, anchor=14)
    written: list[str] = []
    monkeypatch.setattr(panels, "_emit", written.append)
    menu = at_the_prompt(screen, lambda room=0: a_band(screen.caps, room=room), bottom=18)
    menu.sync()
    assert written == [] and menu.held == {}, "no room is zero rows and zero bytes"


def test_a_face_clear_of_the_band_costs_the_fold_nothing(monkeypatch):
    screen, _ = sixel_screen()
    monkeypatch.setattr(screen.caps, "sync_size", lambda: None)
    banked_face(screen, top=15, anchor=10)
    written: list[str] = []
    monkeypatch.setattr(panels, "_emit", written.append)
    menu = at_the_prompt(screen, lambda room=0: a_band(screen.caps, room=room))
    menu.sync()
    touched = {int(m) - 1 for m in AT.findall("".join(written))}
    assert touched == {16, 17, 18, 19}, "face ends at 14 — the band keeps all four of its rows"


def test_the_completion_list_may_still_cover_her_because_close_restores_the_art(monkeypatch):
    screen, _ = sixel_screen()
    monkeypatch.setattr(screen.caps, "sync_size", lambda: None)
    banked_face(screen, top=15, anchor=14)
    written: list[str] = []
    monkeypatch.setattr(panels, "_emit", written.append)
    menu = at_the_prompt(screen, lambda room=0: a_band(screen.caps, room=room))
    menu.prompt.session.default_buffer.complete_state = SimpleNamespace(
        completions=[Completion("", 0, display=f"/cmd{i}") for i in range(8)], complete_index=0)
    menu.sync()
    listed = "".join(written)
    assert {int(m) - 1 for m in AT.findall(listed)} & {14, 15, 16, 17, 18}, \
        "transitory, summoned, and restored on close — the list keeps its whole surface"
    menu.prompt.session.default_buffer.complete_state = None
    menu.sync()
    restored = "".join(written)[len(listed):]
    assert "<SIXEL excited>" in restored, "the art comes back with the rows it was banked against"
