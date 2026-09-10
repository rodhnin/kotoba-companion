"""Two things that must agree can never be two constants, applied to three numbers the render layer
had spelt out more than once: the fold gate (a window under twenty rows) was three literal `20`s
across three call sites; the width the band, frame and completion list are drawn at was the same
formula written three times; and the centred window over a list (the band's steps, the inventory's
open steps, the completion list) was one formula written three times, one copy with its own guard.
Each is one name now, and these pins read the surfaces, not the names: a surface that folds at a
height its neighbour does not is the defect these exist to keep out, whatever the constant is called.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time

from kotoba.cli import state
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import GLYPHS_ASCII, GLYPHS_UNICODE, Caps


def _caps(height: int, width: int = 96) -> Caps:
    caps = Caps(color="truecolor", background="dark", unicode=True, encodes_unicode=True,
                interactive=True, width=width, height=height, g=dict(GLYPHS_UNICODE))
    caps.g["sigil"] = "言"
    return caps


@dataclass
class _Plan:
    frame: dict
    is_open: bool = True
    total: int = 4
    active_task: object = None


@dataclass
class _State:
    turn_start: float = field(default_factory=time.monotonic)
    status: str = ""
    peek: str = ""
    partial: str = ""
    tools: tuple = ()
    helpers: tuple = ()
    holds: tuple = ()
    due: tuple = ()
    work: object = None
    plan: object = None
    approval: object = None
    confirm: object = None
    folded: bool = False


def _plan(n: int = 4) -> _Plan:
    tasks = [{"order": i + 1, "text": f"step {i + 1}",
              "status": "active" if i == 1 else "pending"} for i in range(n)]
    return _Plan({"status": "open", "tasks": tasks})


def test_the_fold_gate_is_one_height_for_the_line_up_the_band_and_the_bar():
    helpers = [state.Helper("s1", "research", "goal one", state="running", started=time.monotonic()),
               state.Helper("s2", "web", "goal two", state="running", started=time.monotonic())]
    st = _State(status="tool", helpers=tuple(helpers), plan=_plan())
    for height, folds in ((rows.FOLD_ROWS - 1, True), (rows.FOLD_ROWS, False)):
        caps = _caps(height)
        roster = rows.roster_rows(caps, helpers, 96)
        band = rows.band_rows(caps, st, 96)
        phrase = footer._bar_phrase(caps, footer.State(helpers=tuple(helpers), status="tool"),
                                    "helpers", 2.0, room=60)
        assert (len(roster) == 1) is folds, (height, len(roster))
        assert (len(band) == 1) is folds, (height, [r.plain for r in band])
        assert (phrase == "") is folds, (height, phrase)


def test_the_band_the_frame_and_the_completion_list_share_one_width():
    from rich.cells import cell_len

    class _Item:
        display_text = "/plan"
        display_meta_text = "the open list"

    for width in (44, 96, 160):
        caps = _caps(40, width)
        assert footer.frame_w(caps) == max(20, width - 1)
        box = footer.box_rows(caps, footer.State())
        menu = footer.menu_rows(caps, [_Item()], 0, 3)
        band = rows.band_rows(caps, _State(status="thinking", plan=_plan()), footer.frame_w(caps))
        shadow = cell_len(caps.g["shadow"])
        assert cell_len(box[0].plain) - shadow == footer.frame_w(caps)
        assert cell_len(menu[0].plain) - shadow == footer.frame_w(caps)
        assert all(cell_len(r.plain) <= footer.frame_w(caps) for r in band)


def test_one_centred_window_serves_the_steps_the_inventory_and_the_completion_list():
    assert rows.window(at=0, n=3, tall=5) == 0            # shorter than the window: whole
    assert rows.window(at=5, n=10, tall=3) == 4           # centred
    assert rows.window(at=9, n=10, tall=3) == 7           # never past the end
    assert rows.window(at=0, n=0, tall=3) == 0            # nothing to window
    caps = _caps(40)
    plan = _plan(10)
    for t in plan.frame["tasks"]:
        t["status"] = "active" if t["order"] == 6 else "pending"
    drawn = rows.band_rows(caps, _State(status="thinking", plan=plan), 96)[1:]
    assert [f"step {n}" in r.plain for n, r in zip((5, 6, 7), drawn)] == [True] * 3, \
        [r.plain for r in drawn]
    assert "+3 pending" in drawn[2].plain, drawn[2].plain    # the three below the window

    class _Item:
        def __init__(self, n: int) -> None:
            self.display_text, self.display_meta_text = f"/cmd{n}", ""

    menu = footer.menu_rows(caps, [_Item(i) for i in range(10)], 5, 3)
    assert "/cmd4" in menu[1].plain and "/cmd6" in menu[3].plain, [r.plain for r in menu]


def test_the_meter_strides_by_the_swells_own_step():
    """`Spin.tick` never reads `swell`'s table; it strides the meter and `spinner` lands on those
    frames. One arithmetic under both, or the pinned table and the drawn one can part."""
    for g in (GLYPHS_UNICODE, GLYPHS_ASCII):
        caps = Caps(unicode=g is GLYPHS_UNICODE, g=dict(g))
        spin = rows.Spin(caps)
        assert spin.stride() == rows.stride_of(g["spin"]) == len(g["spin"]) // len(rows.swell(g["spin"]))
        spin.at = time.monotonic()
        seen = []
        for _ in range(len(rows.BEAT_FRAMES)):
            spin.at -= 0.25
            spin.tick("neutral", hz=4.0)
            seen.append(spin.spinner(1.0))
        assert "".join(seen) == rows.swell(g["spin"])[1:] + rows.swell(g["spin"])[:1], seen
