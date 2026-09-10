"""The status band above the input box, and the three defects the design itself created.

One builder feeds two surfaces — `footer.pinned_view` inside a turn, `input/menu.Menu` at the prompt —
so the first thing asserted here is that they draw the same rows. The rest is the byte discipline the
band cannot break: zero rows and zero bytes with nothing happening, an unchanged band writing nothing,
a ticking clock costing exactly its own row, a resize forgetting a diff that no longer matches the
glass, and never a cell of prompt_toolkit's frame.
"""
from __future__ import annotations

import asyncio
import io
import re
import time
from types import SimpleNamespace

import pytest
from conftest import needs_posix_terminal
from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.completion import Completion
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kotoba.cli import slash, state
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.input import menu as panels
from kotoba.cli.input.prompt import Prompt
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.kaomoji import Face
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render import theme
from kotoba.cli.render.theme import (GLYPHS_ASCII, GLYPHS_UNICODE, build_console, fold)

NOW = 10_000.0
TAIL = ["she wrote the file out", "› and then you asked her something", "one more transcript row"]
AT = re.compile(r"\x1b\[([0-9]+);1H")


def a_caps(**over) -> Caps:
    base = dict(color="none", background="dark", unicode=True, interactive=False,
                width=96, height=24, g=dict(GLYPHS_UNICODE))
    base.update(over)
    return Caps(**base)


def plan_frame(*statuses, list_id="L", title="informe", status="open") -> dict:
    return {"list_id": list_id, "title": title, "status": status,
            "tasks": [{"id": str(i), "text": f"paso {i}", "status": s, "order": i}
                      for i, s in enumerate(statuses, 1)]}


def a_state(**over) -> footer.State:
    fields = dict(over)
    if "plan" in fields and isinstance(fields["plan"], dict):
        fields["plan"] = state.Plan(fields["plan"])
    return footer.State(**fields)


def plains(drawn) -> list[str]:
    return [r.plain for r in drawn]


# --- the builder -----------------------------------------------------------------------------------

def test_with_nothing_running_and_no_plan_the_band_is_zero_rows():
    assert rows.band_rows(a_caps(), a_state(), 94) == []
    assert rows.band_rows(a_caps(), a_state(partial="she is speaking"), 94) == []


def test_row_one_is_her_actual_action_with_its_clock_and_never_a_considering(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    tool = state.Tool("web", "web_search 'live2d lipsync'", started=NOW - 6.0)
    band = rows.band_rows(a_caps(), a_state(turn_start=NOW - 8.0, status="web", tools=(tool,)), 94)
    assert len(band) == 1
    said = band[0].plain
    assert said.startswith("▸ web_search 'live2d lipsync'") and said.endswith("6s")
    assert "Considering" not in said and "working" not in said


def test_the_now_row_falls_back_to_peek_and_thinking_while_the_job_keeps_its_own(monkeypatch):
    """RE-RECORDED. The last line used to assert the band went EMPTY when a turn spoke over a running
    job — one row, two facts, and the turn winning the clash. Live use hit that: typing while a job
    ran took the row above the box out with the job still going. Two facts now get two rows."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    caps = a_caps()
    peeking = a_state(turn_start=NOW - 3.0, peek="memory_recall")
    assert plains(rows.band_rows(caps, peeking, 94))[0].startswith("▸ recalling…")
    thinking = a_state(turn_start=NOW - 3.0, status="thinking")
    assert plains(rows.band_rows(caps, thinking, 94))[0].startswith("▸ thinking…")
    job = state.Work("read both pages", t0=NOW - 69.0)
    job.tools.append(state.Tool("web", "web_search 'both pages'", started=NOW - 5.0))
    out = plains(rows.band_rows(caps, a_state(work=job), 94))[0]
    # The job's rung names the step's own ARGUMENT, exactly as the turn's rung under it does, and its
    # mark is `beat_mark` — the arrow growing in her grape, one frame per second of the clock beside it.
    assert out[1:].startswith(" web_search 'both pages'") and out.endswith("1m 09s")
    assert out[0] in {caps.g[k] for k in rows.BEAT_FRAMES}
    assert out[0] not in caps.g["spin"] and rows.work_verb(job) == "looking that up…"
    speaking = rows.band_rows(caps, a_state(turn_start=NOW - 2.0, work=job, partial="Ya la"), 94)
    assert len(speaking) == 1 and "web_search 'both pages'" in speaking[0].plain, \
        "she is only speaking, so the turn adds no row — and the job keeps the one that is its own"


def test_the_open_plan_is_struck_boxed_and_windowed_with_the_overflow_counted(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    caps = a_caps()
    tool = state.Tool("web", "reading the page", started=NOW - 6.0)
    st = a_state(turn_start=NOW - 8.0, tools=(tool,),
                 plan=plan_frame("done", "active", "pending", "pending", "pending", "pending"))
    band = rows.band_rows(caps, st, 94)
    assert len(band) == 4
    assert band[1].plain.startswith("  └ ✓ paso 1")
    assert band[2].plain.startswith("    ▸ paso 2")
    assert band[3].plain.startswith("    □ paso 3")
    assert band[3].plain.rstrip().endswith("… +3 pending")
    assert "pending" not in band[1].plain and "pending" not in band[2].plain


def test_the_overflow_counts_only_the_pendings_below_the_window(monkeypatch):
    """RE-RECORDED: the state used to be at rest, which now takes the inventory shape.
    The below-the-window count is the IN-TURN rule and that is where it stays pinned."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    tool = state.Tool("web", "reading the page", started=NOW - 6.0)
    st = a_state(turn_start=NOW - 8.0, tools=(tool,),
                 plan=plan_frame("done", "done", "active", "pending", "dropped", "pending",
                                 "pending"))
    band = rows.band_rows(a_caps(), st, 94)
    assert band[-1].plain.rstrip().endswith("… +2 pending"), \
        "a dropped step below the window is not a pending one"


def test_a_band_given_fewer_rows_than_it_wants_gives_up_steps_and_never_the_count(monkeypatch):
    """The surface hands the budget to the BUILDER. Trimmed by the painter instead, from the bottom,
    what went was the pending steps and the `… +N pending` with them — a six-step job showing one."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    tool = state.Tool("web", "reading the page", started=NOW - 6.0)
    st = a_state(turn_start=NOW - 8.0, tools=(tool,),
                 plan=plan_frame("done", "active", "pending", "pending", "pending", "pending"))
    assert len(rows.band_rows(a_caps(), st, 94)) == 4
    for room, steps in ((3, 2), (2, 1)):
        band = rows.band_rows(a_caps(), st, 94, room=room)
        assert len(band) == room and len(band) - 1 == steps
        assert band[0].plain.startswith("▸ reading the page"), "row one keeps the clock"
        assert band[-1].plain.rstrip().endswith("… +4 pending"), \
            "the count is taken against the steps actually drawn"
    assert "paso 2" in rows.band_rows(a_caps(), st, 94, room=2)[1].plain, "the ACTIVE step survives"


def test_down_to_one_row_the_plan_takes_it_because_the_bar_already_says_row_one(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    st = a_state(work=running_job(), plan=plan_frame("done", "active", "pending", "pending"))
    assert rows.work_verb(st.work) in footer.out_twins(st, footer.bar_kind(st))[0]
    band = rows.band_rows(a_caps(), st, 94, room=1)
    assert len(band) == 1 and "paso 2" in band[0].plain
    assert band[0].plain.rstrip().endswith("… +2 pending")
    bare = a_state(work=running_job())
    assert len(rows.band_rows(a_caps(), bare, 94, room=1)) == 1, "no plan, row one keeps its row"
    # In a turn the bar names the TURN (`bar_kind`), so the job is on nothing and the row is its own.
    turning = a_state(work=running_job(), turn_start=NOW - 2.0, partial="Ya la",
                      plan=plan_frame("done", "active", "pending"))
    one = rows.band_rows(a_caps(), turning, 94, room=1)
    assert len(one) == 1 and "web_search 'flash v2.5 first byte'" in one[0].plain


def test_a_plan_open_at_an_idle_prompt_is_the_band_without_row_one_and_without_a_clock():
    """RE-RECORDED: three bare windowed rows said nothing at rest, so the shape
    is the inventory now — counts header, open steps first, the done one struck below them."""
    st = a_state(plan=plan_frame("done", "active", "pending"))
    band = rows.band_rows(a_caps(), st, 94)
    assert len(band) == 4
    assert band[0].plain.startswith("    3 tasks (1 done, 2 open)"), "the header opens the shape"
    assert band[1].plain.startswith("    ◇ paso 2"), "open steps first, and no elbow to hang from"
    assert band[2].plain.startswith("    □ paso 3")
    assert band[3].plain.startswith("    ✓ paso 1"), "a done one only with room left, and last"
    assert not any(re.search(r"\ds$", r.plain.rstrip()) for r in band), "no clock at rest"
    assert rows.band_rows(a_caps(), st, 94) is not band
    assert plains(rows.band_rows(a_caps(), st, 94)) == plains(band), "a still payload, for the diff"


def test_at_rest_the_plan_is_an_inventory_with_a_header_a_cap_and_one_joined_tail():
    """THE ASK: a header with the counts, `BAND_TASKS` rows with the open steps first, a done one only
    with room left, and a tail for what the cap hid — the reference glass was a 72-task list that
    drew three bare rows and `… +2 pending`."""
    st = a_state(plan=plan_frame(*["done"] * 68, "active", "pending", "pending", "pending"))
    band = rows.band_rows(a_caps(), st, 94)
    assert len(band) == 6
    assert band[0].plain.lstrip().startswith("72 tasks (68 done, 4 open)")
    assert band[1].plain.lstrip().startswith("◇ paso 69"), "the open steps come first"
    assert [r.plain.lstrip()[:1] for r in band[2:5]] == ["□", "□", "□"]
    assert band[5].plain.lstrip().startswith("✓ paso 1"), "one done fills the last row"
    assert band[5].plain.rstrip().endswith("… +67 done"), "the one tail says what the cap hid"


def test_the_header_and_the_cap_live_at_rest_only_never_inside_a_turn_or_over_a_job(monkeypatch):
    """THE RULE: the inventory shape belongs to the resting prompt, and unambiguously to that state
    alone. Inside a turn the height is already spoken for — the job's row and the now row were split
    so they stop competing, and a header would be a third claimant that costs a step from a three-row
    window."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    frame = plan_frame(*["done"] * 68, "active", "pending", "pending", "pending")
    tool = state.Tool("web", "reading the page", started=NOW - 6.0)
    turning = plains(rows.band_rows(a_caps(), a_state(turn_start=NOW - 8.0, tools=(tool,),
                                                      plan=frame), 94))
    assert len(turning) == 4, "one head row and BAND_STEPS — the settled in-turn height"
    jobbed = plains(rows.band_rows(a_caps(), a_state(work=running_job(), plan=frame), 94))
    for band in (turning, jobbed):
        assert not any("tasks (" in t for t in band), "the inventory shape is at-rest only"


def test_an_open_card_is_not_at_rest_so_the_band_under_it_keeps_the_settled_window():
    """A hold can outlive its run, so a card can be open with no turn and no live job — and the
    card's overlay seats against this band at the height the prompt released (`app._overlay_open`),
    which a shape three rows taller would move. A gate is a place for calm; waiting for the
    user to answer a gate is not waiting for them to speak."""
    frame = plan_frame(*["done"] * 68, "active", "pending", "pending", "pending")
    for card in (dict(approval=object()), dict(confirm=object())):
        band = plains(rows.band_rows(a_caps(), a_state(plan=frame, **card), 94))
        assert len(band) == rows.BAND_STEPS, band
        assert not any("tasks (" in t for t in band), "the inventory yields to the card"


def test_the_at_rest_tail_joins_pending_and_done_and_the_active_step_is_always_aboard():
    st = a_state(plan=plan_frame(*["done"] * 3, *["pending"] * 6, "active", "pending"))
    band = rows.band_rows(a_caps(), st, 94)
    assert band[0].plain.lstrip().startswith("11 tasks (3 done, 8 open)")
    assert any("◇ paso 10" in r.plain for r in band), "the window slides to keep the active aboard"
    assert band[-1].plain.rstrip().endswith("… +3 pending, +3 done"), \
        "one tail, both counts — never two tails that can disagree"


def test_a_plan_entirely_done_but_still_open_says_so_instead_of_mute_struck_rows():
    st = a_state(plan=plan_frame(*["done"] * 9))
    band = rows.band_rows(a_caps(), st, 94)
    assert len(band) == 6
    assert band[0].plain.lstrip().startswith("9 tasks (9 done, 0 open)")
    assert band[1].plain.lstrip().startswith("✓ paso 1")
    assert band[-1].plain.rstrip().endswith("… +4 done")


def test_the_at_rest_shape_bows_to_the_height_budget_and_the_header_needs_two_rows():
    """The cap of `BAND_TASKS` cooperates with `room` — the budget wins whenever it is smaller. At
    one row the step keeps it: the bar already says the position (`plan_bar_twins`) while the step's
    own text is on no other surface, so the header — which needs a row of its own — yields."""
    st = a_state(plan=plan_frame("done", "active", "pending", "pending", "pending", "pending"))
    for room in (4, 3, 2):
        band = rows.band_rows(a_caps(), st, 94, room=room)
        assert len(band) == room, room
        assert band[0].plain.lstrip().startswith("6 tasks (1 done, 5 open)"), room
    one = rows.band_rows(a_caps(), st, 94, room=1)
    assert len(one) == 1 and "tasks (" not in one[0].plain
    assert "paso 2" in one[0].plain, "the row that survives is the active step's"


def test_a_dropped_step_counts_in_the_total_and_in_neither_bucket():
    st = a_state(plan=plan_frame("done", "dropped", "active", "pending"))
    band = rows.band_rows(a_caps(), st, 94)
    assert band[0].plain.lstrip().startswith("4 tasks (1 done, 2 open)")


def test_every_at_rest_row_fits_the_width_it_was_asked_for():
    st = a_state(plan=plan_frame(*["done"] * 30, "active", *["pending"] * 8))
    for width in (119, 95, 71, 63):
        for row in rows.band_rows(a_caps(width=width + 1, height=30), st, width):
            assert row.cell_len <= width, (width, row.plain)


def test_the_band_folds_to_row_one_by_the_line_ups_own_three_gates(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    tool = state.Tool("web", "reading the page", started=NOW - 6.0)
    st = a_state(turn_start=NOW - 8.0, tools=(tool,), plan=plan_frame("done", "active", "pending"))
    assert len(rows.band_rows(a_caps(), st, 94)) == 4
    folded = a_state(turn_start=NOW - 8.0, tools=(tool,), folded=True,
                     plan=plan_frame("done", "active", "pending"))
    assert len(rows.band_rows(a_caps(), folded, 94)) == 1
    assert len(rows.band_rows(a_caps(height=19), st, 94)) == 1
    assert len(rows.band_rows(a_caps(), st, rows.ROSTER_MIN_W - 1)) == 1


def test_every_band_row_fits_the_width_it_was_asked_for(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    tool = state.Tool("web", "web_search 'a very long query about pixi-live2d-display versions'",
                      started=NOW - 6.0)
    st = a_state(turn_start=NOW - 8.0, tools=(tool,),
                 plan=plan_frame("done", "active", "pending", "pending", "pending"))
    for width in (119, 95, 71, 43):
        for row in rows.band_rows(a_caps(width=width + 1, height=30), st, width):
            assert row.cell_len <= width, (width, row.plain)


def test_only_row_one_carries_a_clock_so_the_steps_never_cost_a_byte_per_second(monkeypatch):
    at = [NOW]
    monkeypatch.setattr(time, "monotonic", lambda: at[0])
    job = state.Work("read both pages", t0=NOW - 69.0)
    st = a_state(work=job, plan=plan_frame("done", "active", "pending"))
    first = plains(rows.band_rows(a_caps(), st, 94))
    at[0] = NOW + 47.0
    second = plains(rows.band_rows(a_caps(), st, 94))
    assert first[0] != second[0]
    assert first[1:] == second[1:]


def test_a_done_step_is_struck_through_its_dim_and_the_glass_really_gets_sgr_nine():
    caps = a_caps(color="16", interactive=True)
    st = a_state(plan=plan_frame("done", "active", "pending"))
    band = rows.band_rows(caps, st, 94)
    done_row = next(r for r in band if "paso 1" in r.plain)
    buf = io.StringIO()
    console = build_console(caps, file=buf, force_terminal=True)
    console.width = caps.width
    console.print(done_row)
    params = [m for m in re.findall(r"\x1b\[([0-9;]*)m", buf.getvalue())]
    assert any("9" in p.split(";") for p in params), buf.getvalue()


def test_the_ascii_band_has_the_box_as_a_bracket_and_not_one_byte_above_seven_bits(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    caps = a_caps(unicode=False, g=dict(GLYPHS_ASCII))
    tool = state.Tool("web", "web_search 'live2d'", started=NOW - 6.0)
    st = a_state(turn_start=NOW - 8.0, tools=(tool,),
                 plan=plan_frame("done", "active", "pending", "pending"))
    band = rows.band_rows(caps, st, 94)
    for row in band:
        assert all(ord(c) < 128 for c in row.plain), row.plain
        assert "?" not in row.plain, "the fold's catch-all means a glyph is missing its entry"
    assert band[3].plain.lstrip().startswith("[ ")
    assert GLYPHS_UNICODE["box"] == "□" and fold("□") == "["


def test_the_pinned_block_and_the_prompt_surface_draw_the_very_same_band(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    caps = a_caps()
    tool = state.Tool("web", "reading the page", started=NOW - 6.0)
    st = a_state(turn_start=NOW - 8.0, tools=(tool,), plan=plan_frame("done", "active", "pending"))
    band = rows.band_rows(caps, st, max(20, caps.width - 1))
    pinned = footer.pinned_view(caps, Face(unicode=True), st)
    assert plains(pinned[:len(band)]) == plains(band)
    assert len(pinned) == len(band) + 4, "the frame's three rows and the bar follow the band"
    at_rest = footer.pinned_view(caps, Face(unicode=True), a_state(at_rest=True))
    assert len(at_rest) == 4, "no band at rest — the frame and the bar and nothing above them"


# --- the prompt surface ----------------------------------------------------------------------------

class Live:
    """A real line editor over a pipe, a Menu with its emissions captured, and a transcript bank."""

    def __init__(self, pipe, monkeypatch) -> None:
        self.caps = Caps(color="none", background="dark", unicode=True, interactive=True,
                         width=96, height=24, g=dict(GLYPHS_UNICODE))
        monkeypatch.setattr(self.caps, "sync_size", lambda: None)
        self.buf = io.StringIO()
        console = build_console(self.caps, file=self.buf)
        console.width = self.caps.width
        self.screen = Screen(self.caps, console=console,
                             portrait=Portrait(self.caps, wanted=False))
        self.prompt = Prompt(self.caps, complete_while_typing=False, input=pipe,
                             output=DummyOutput())
        monkeypatch.setattr(self.prompt, "geometry", lambda h: (20, 21))
        self.app = App(self.caps, self.screen, self.prompt)
        self.app.session = SimpleNamespace(session_id="s1",
                                           events=SimpleNamespace(steps={}, settle=lambda: None))
        self.written: list[str] = []
        monkeypatch.setattr(panels, "_emit", self.written.append)
        self.screen.kept(TAIL)

    def sync(self) -> str:
        before = len(self.written)
        self.app.menu.sync()
        return "".join(self.written[before:])

    def rows_touched(self, payload: str) -> set[int]:
        return {int(m) - 1 for m in AT.findall(payload)}


@pytest.fixture
def live(monkeypatch):
    with create_pipe_input() as pipe:
        yield Live(pipe, monkeypatch)


def running_job(at: float = NOW) -> state.Work:
    """A job with a web search OPEN. The phrase is the job's current step now, so a job with no step
    running says so — pinning `verb` alone would pin the latch this replaced."""
    job = state.Work("get the real latency numbers", 1, t0=at - 72.0)
    job.tools.append(state.Tool("web", "web_search 'flash v2.5 first byte'", started=at - 4.0))
    return job


@needs_posix_terminal
def test_the_band_paints_ending_one_row_above_the_frame_and_repeats_write_nothing(live, monkeypatch):
    at = [NOW]
    monkeypatch.setattr(time, "monotonic", lambda: at[0])
    live.app.work = running_job()
    live.app._event("task_list", plan_frame("done", "active", "pending"))
    painted = live.sync()
    touched = live.rows_touched(painted)
    assert touched == {16, 17, 18, 19}, "four rows, ending at bottom - 1, and row 20 is the frame's"
    assert "web_search 'flash v2.5 first byte'" in painted and "paso 2" in painted
    assert painted.startswith("\x1b7") and painted.endswith("\x1b8")
    assert live.sync() == "", "an unchanged band is an unwritten band"


@needs_posix_terminal
def test_her_portrait_takes_rows_off_the_band_and_the_pending_count_is_not_one_of_them(
        live, monkeypatch):
    """Driven in a pty at 96x44 with the sixel tier on, a six-step job drew ONE plan row and no count:
    the band was sliced to fit under her face, and the slice came off the end."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.screen.portrait.mode = "sixel"
    live.screen.portrait.rows = live.screen.portrait.irows = 4
    live.screen.tail.clear()
    live.screen.kept(["a", "b", "c"])
    live.screen.kept(["her reply"], art="\x1bPq#0;2;0;0;0#0~~@\x1b\\")
    live.screen.kept(["d", "e", "f", "g", "h", "i"])
    live.app.work = running_job()
    live.app._event("task_list",
                    plan_frame("done", "active", "pending", "pending", "pending", "pending"))
    painted = live.sync()
    assert live.app.menu._face_floor(20, 4) == 18, "her face reaches row 17, so 18 and 19 are left"
    assert live.rows_touched(painted) == {18, 19}
    assert "paso 2" in painted and "… +4 pending" in painted


@needs_posix_terminal
def test_her_reply_face_reaches_only_as_far_as_it_was_drawn_so_the_jobs_row_survives(
        live, monkeypatch):
    """Reported live: a job at 1m 21s, a short chat turn typed over it, her reply — and the band GONE
    under a bar still saying WORKING · 1m 42s. Reproduced with a scripted repro on the sixel
    portrait tier: a chat turn over a live job releases a region that is
    exactly band + box + bar, her four-row face sits one blank above it, and `_face_floor` measured
    that face as `max(rows, irows)` tall — the BOOT face's six — so it reached two rows past its own
    chin, through a pad that was not there, and the one row the band had went to nobody. The
    height travels WITH the art now, the way its column already does."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    box = live.screen.portrait
    box.mode, box.rows, box.irows = "sixel", 6, 4
    live.screen.tail.clear()
    live.screen.kept(["a", "b", "c", "d"])
    live.screen.kept(["her reply", "", "", ""], art="\x1bPq#0;2;0;0;0#0~~@\x1b\\", reach=4)
    live.screen.kept(["", ""])
    live.app.work = running_job()
    painted = live.sync()
    assert live.app.menu._face_floor(20, 1) == 19, "her face ends on row 18 — row 19 is free"
    assert live.rows_touched(painted) == {19}, painted
    assert "web_search 'flash v2.5 first byte'" in painted


@needs_posix_terminal
def test_a_ticking_clock_rewrites_exactly_the_row_that_changed(live, monkeypatch):
    at = [NOW]
    monkeypatch.setattr(time, "monotonic", lambda: at[0])
    live.app.work = running_job()
    live.app._event("task_list", plan_frame("done", "active", "pending"))
    live.sync()
    at[0] = NOW + 1.0
    moved = live.sync()
    assert live.rows_touched(moved) == {16}, "one second is one row, not the whole payload"
    assert "1m 13s" in moved
    # Inside the SAME mark frame: nothing on the row has changed, so nothing is written. A quarter of
    # a frame, not 0.4 s — the mark turns over every `1 / rows.BEAT_HZ` now, so 0.4 s is two frames.
    at[0] = NOW + 1.0 + (1.0 / rows.BEAT_HZ) / 4
    assert live.sync() == ""


@needs_posix_terminal
def test_an_at_rest_plan_band_is_stills_all_the_way_down_and_repeats_write_nothing(live,
                                                                                  monkeypatch):
    """The inventory shape lives ONLY in the state whose byte cost is the measured invariant: every
    row of it — header, steps, tail — is a still, so the diff writes the landing once and then
    nothing, and an idle prompt with a plan open stays at exactly 0 B/s."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.app._event("task_list", plan_frame(*["done"] * 5, "active", "pending"))
    painted = live.sync()
    assert "7 tasks (5 done, 2 open)" in painted and "… +2 done" in painted
    assert live.sync() == "", "an unchanged at-rest band is an unwritten band"


@needs_posix_terminal
def test_a_plan_frame_at_a_dead_prompt_pokes_the_beat_awake(live, monkeypatch):
    poked = []
    monkeypatch.setattr(live.app, "_poke", lambda: poked.append(True))
    live.app._event("task_list", plan_frame("active", "pending"))
    assert poked == [True]
    assert live.app._wake() is None, "nothing runs — the poke is the only wake there is"


@needs_posix_terminal
def test_a_step_landing_moves_the_bands_fingerprint_so_the_beat_repaints_it(live, monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.app.work = running_job()
    live.app._event("task_list", plan_frame("active", "pending"))
    live.app._rendered(None)
    assert live.app._bar_state() == live.app._painted
    live.app._event("task_list", plan_frame("done", "active"))
    assert live.app._bar_state() != live.app._painted, \
        "a struck step changes nothing the bar reads — the band's fingerprint is what catches it"


@needs_posix_terminal
def test_a_list_takes_the_surface_the_band_cedes_and_esc_gives_both_back(live, monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.app.work = running_job()
    live.app._event("task_list", plan_frame("done", "active", "pending"))
    live.sync()
    box = live.prompt.session.default_buffer
    box.complete_state = CompletionState(box.document, [Completion("", 0, display="/help")], 0)
    listed = live.sync()
    assert "/help" in listed
    assert 20 in live.rows_touched(listed), "the list ends ON the frame's top border"
    assert f"\x1b[19;1H\x1b[0m{live.screen.ansi(TAIL[0])}\x1b[0m\x1b[K" in listed, \
        "the transcript row the band was covering comes back the moment the list needs its row"
    box.complete_state = None
    restored = live.sync()
    assert live.app.menu.framed is False
    assert live.prompt.session.app.renderer._last_screen is None, \
        "the frame row went back through prompt_toolkit, not through this painter"
    assert live.sync() != "", "the sync after the repaint paints the band from nothing"
    assert "web_search 'flash v2.5 first byte'" in "".join(live.written)


@needs_posix_terminal
def test_a_resize_forgets_the_diff_the_bank_and_the_frame_dance(live, monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.app.work = running_job()
    live.app._event("task_list", plan_frame("done", "active", "pending"))
    first = live.sync()
    assert first != "" and live.screen.tail
    live.caps.width = 90
    repainted = live.sync()
    assert live.screen.tail == [], "rows banked at the old width restore somebody else's transcript"
    assert live.rows_touched(repainted) == {16, 17, 18, 19}, \
        "prompt_toolkit erased the glass on resize — a diff against `last` would refuse to repaint"
    assert live.app.menu._size == (90, 24)


@needs_posix_terminal
def test_a_height_only_resize_also_forgets_the_diff_but_keeps_the_bank(live, monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.app.work = running_job()
    live.app._event("task_list", plan_frame("done", "active", "pending"))
    live.sync()
    live.caps.height = 30
    repainted = live.sync()
    assert live.rows_touched(repainted) == {16, 17, 18, 19}
    assert live.screen.tail, "the width did not change, so the bank is still the right width"


@needs_posix_terminal
def test_the_band_never_asks_for_a_terminal_mode(live, monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.app.work = running_job()
    live.app._event("task_list", plan_frame("done", "active", "pending"))
    payload = live.sync()
    live.app.work.state, live.app.work.stopped = "ok", NOW
    payload += live.sync()
    assert not any(mode in payload for mode in ("\x1b[?1049", "\x1b[?47", "\x1b[?1047"))
    assert re.search(r"\x1b\[[0-9]*(;[0-9]*)?r", payload) is None


@needs_posix_terminal
def test_the_band_leaves_with_its_content_and_the_covered_rows_come_back(live, monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    live.app.work = running_job()
    live.sync()
    live.app.work.state, live.app.work.stopped, live.app.work.landed = "ok", NOW, True
    live.app.work = None
    gone = live.sync()
    assert live.app.menu.held == {}
    assert live.sync() == "", "an absent band costs nothing to stay absent"
    assert "\x1b[20;1H" in gone, "the row it covered is written back on the way out"


# --- the receipt, the parting, and /plan ------------------------------------------------------------

def wired() -> tuple[App, io.StringIO]:
    caps = a_caps(width=96)
    buf = io.StringIO()
    console = build_console(caps, file=buf)
    console.width = caps.width
    screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=Prompt(caps))
    app.session = SimpleNamespace(session_id="s1",
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app, buf


def landed_job() -> state.Work:
    job = state.Work("get the real latency numbers", 1, t0=NOW - 72.0)
    job.state, job.stopped, job.summary = "ok", NOW, "three pages, one note"
    job.tools = [state.Tool("shell", "python measure.py", started=NOW - 40.0, stopped=NOW - 30.0,
                            state="ok", detail="exit 0"),
                 state.Tool("file", "research/latency.md", started=NOW - 20.0, stopped=NOW - 18.0,
                            state="ok", detail="2.1 kB")]
    job.helpers = [state.Helper("h1", "research", "read both pages", state="ok",
                                started=NOW - 30.0, stopped=NOW - 5.0),
                   state.Helper("h2", "web", "find the issue", state="failed",
                                started=NOW - 10.0, stopped=NOW - 2.0)]
    return job


def test_the_receipt_keeps_the_failed_helper_and_lets_the_rest_live_in_work_n():
    app, buf = wired()
    app.work = landed_job()
    app.works.append(app.work)
    assert app._land_work() is True
    out = buf.getvalue()
    assert "find the issue" in out, "a failure that existed only in a closed band is unreadable"
    assert "read both pages" not in out and "python measure.py" not in out
    assert "/work 1 opens the whole of it" in out
    said = asyncio.run(_work_listing(app))
    assert "read both pages" in said and "python measure.py" in said


async def _work_listing(app) -> str:
    await slash.run(app, commands.Command("/work", "1"))
    return app.screen.console.file.getvalue()


def test_the_receipt_ends_on_her_voice_with_one_clear_row_under_it():
    """The order the CLI was first written with, and it was wrong: `/work N opens…` printed
    UNDER her sentence, so the last thing before the next prompt line was a pointer at column 0 sitting
    in the rows her portrait reserves under a one-line reply — read from the prompt, a wide empty region
    with a stray hint in it."""
    app, buf = wired()
    app.work = landed_job()
    app.works.append(app.work)
    app.work.said = "Listo — el informe quedó en research/latency.md."
    assert app._land_work() is True
    lines = buf.getvalue().split("\n")
    hint = next(i for i, r in enumerate(lines) if "opens the whole of it" in r)
    said = next(i for i, r in enumerate(lines) if "el informe quedó" in r)
    work_row = next(i for i, r in enumerate(lines) if "WORK" in r and "done" in r)
    assert work_row < hint < said, "the hint belongs with the rows it points at"
    assert lines[hint - 1].strip(), "and it is not cut off from them by a clear row"
    assert not lines[said + 1].strip() and lines[said + 2].strip() == "", \
        "exactly one clear row under her, then the transcript ends"
    app.screen.commit_user("y ahora dime otra cosa")
    after = buf.getvalue().split("\n")
    said = next(i for i, r in enumerate(after) if "el informe quedó" in r)
    typed = next(i for i, r in enumerate(after) if "y ahora dime otra cosa" in r)
    assert typed - said == 2, "one blank row between her last line and his next one"


def test_parting_lands_the_whole_roster_because_work_n_dies_with_the_session():
    app, buf = wired()
    app.work = landed_job()
    app._bye()
    out = buf.getvalue()
    assert "python measure.py" in out and "read both pages" in out and "find the issue" in out
    assert "opens the whole of it" not in out, "a pointer at a session that is ending is a lie"


def test_plan_with_an_argument_says_whose_plans_are_and_keeps_the_snapshot_for_no_argument():
    app, buf = wired()
    app._event("task_list", plan_frame("done", "active", "pending"))
    asyncio.run(slash.run(app, commands.Command("/plan", "investigar live2d")))
    out = buf.getvalue()
    assert "plans are hers to open" in out
    assert "no plan open right now" not in out
    asyncio.run(slash.run(app, commands.Command("/plan")))
    assert "informe" in app.screen.console.file.getvalue()


def test_the_console_never_expands_an_emoji_shortcode():
    caps = a_caps(color="16", interactive=True)
    buf = io.StringIO()
    console = build_console(caps, file=buf, force_terminal=True)
    console.print("plain :tada: string")
    assert ":tada:" in buf.getvalue() and "🎉" not in buf.getvalue()


def test_the_wizards_checkmark_folds_for_the_terminal_that_asked_for_ascii(monkeypatch, capsys,
                                                                            tmp_path):
    import tempfile

    from kotoba.cli import wizard
    from kotoba.db.database import Database

    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))

    async def accept(provider_id, key):
        return True, ""

    async def accept_voice(key):
        return True, ""

    monkeypatch.setattr(wizard, "_verify", accept)
    # The wizard does not stop at the brain: `ask_secret` answers the voice prompt too, and that check
    # is a live request to the voice service. Both round-trips are stubbed or neither is.
    monkeypatch.setattr(wizard.voice_key, "verify", accept_voice)

    async def go():
        db = Database("sqlite:///" + (tempfile.mkdtemp() + "/kotoba.db"))
        await db.connect()
        ok = await wizard.run(db, a_caps(unicode=False, g=dict(GLYPHS_ASCII)),
                              ask=lambda _p: "1", ask_secret=lambda _p: "sk-good")
        await db.close()
        return ok

    assert asyncio.run(go()) is True
    out = capsys.readouterr().out
    assert "that key works" in out
    done = next(line for line in out.splitlines() if "that key works" in line)
    assert "✓" not in done and done.startswith("+"), "the --ascii terminal got the raw checkmark"


def test_row_one_wears_the_running_mark_and_the_machine_dim_not_dead_text(monkeypatch):
    """Row one IS a running row, so it dresses like one: the family's still `▸` at the prompt, the
    phrase in the machine's dim — a bare bullet and default-colour text read as a port that lost its
    styling, which is exactly what it was."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    row = rows.band_rows(a_caps(), a_state(turn_start=NOW - 3.0, status="thinking"), 94)[0]
    assert row.plain.startswith("▸ thinking…")
    styled = {str(sp.style) for sp in row.spans}
    assert "grape" in styled, "the still mark is the family's, coloured"
    assert "chrome" in styled, "the phrase is the machine's dim, not default text"


def test_row_one_pulses_off_the_regions_spinner_inside_a_turn(monkeypatch):
    """In a turn the region repaints every frame anyway, so the mark may live — off the SAME Spin the
    tool rows read, never a second clock. At the prompt nothing passes one and the row stays a still,
    which is what the byte budget and the diff painter both require."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    caps = a_caps()
    spin = rows.Spin(caps)
    working = a_state(turn_start=NOW - 3.0, status="shell")
    seen = set()
    for i in range(len(caps.g["spin"])):
        spin.acc, spin.at = float(i), NOW
        seen.add(rows.band_rows(caps, working, 94, spin=spin)[0].plain[0])
    assert seen == set(caps.g["spin"])
    still = rows.band_rows(caps, working, 94)[0].plain[0]
    assert still not in seen, "no spinner frame may be committed as a still"


@pytest.mark.parametrize("charset", ["unicode", "ascii"])
def test_her_thinking_wears_its_own_mark_and_never_the_tool_ramp(monkeypatch, charset):
    """"She is composing" and "a tool is running" are two facts and were drawing one glyph. The breath
    is hers — its own ramp, its own grape against the spinner's sun — and it shares not one frame with
    the meter every machine state fills.

    BOTH charsets, because the ASCII pair was `.oO` against the spinner's `.oOo` — the same three
    characters in the same column — so the whole distinction existed only where the terminal could
    draw block elements, and this test passed anyway by only ever asking the unicode one."""
    caps = a_caps() if charset == "unicode" else a_caps(unicode=False, g=dict(GLYPHS_ASCII))
    spin = rows.Spin(caps)
    thinking = a_state(turn_start=NOW - 3.0, status="thinking")
    breaths, machine = set(), set()
    for beat in range(24):
        monkeypatch.setattr(time, "monotonic", lambda b=beat: NOW + b * rows.THINK_S)
        breaths.add(rows.band_rows(caps, thinking, 94, spin=spin)[0].plain[0])
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    for i in range(len(caps.g["spin"])):
        spin.acc, spin.at = float(i), NOW
        for machine_state in (a_state(turn_start=NOW - 3.0, status="shell"),
                              a_state(turn_start=NOW - 3.0, peek="memory_recall"),
                              a_state(turn_start=NOW - 3.0, status="web",
                                      tools=(state.Tool("web", "web_search 'x'", started=NOW - 6.0),))):
            machine.add(rows.band_rows(caps, machine_state, 94, spin=machine_state and spin)[0].plain[0])
    assert breaths == set(caps.g["think"]), breaths
    assert not (breaths & machine), "the breath borrowed a frame from the tool ramp"
    hue = {str(sp.style) for sp in rows.band_rows(caps, thinking, 94, spin=spin)[0].spans[:1]}
    assert hue == {"grape"}, "her mark is her colour, not the spinner's sun"


def test_the_two_ramps_share_no_character_in_either_glyph_table():
    """The table-level twin of the test above, so a future edit to one ramp cannot quietly re-collide
    it with the other — nor with a mark that already means something at column 0. The unicode ramp
    opened on `·`, which is `bullet`: the frame a tool wears for its first fifth of a second, the
    mark a queued helper wears, and the separator every row uses. `--plain` has no hue to tell those
    apart. `--calm` freezes each ramp to one frame and those two must differ for the same reason."""
    for name, table in (("unicode", GLYPHS_UNICODE), ("ascii", GLYPHS_ASCII)):
        think, spin = set(table["think"]), set(table["spin"])
        assert len(table["think"]) == len(think), (name, "the breath repeats a frame")
        assert not (think & spin), (name, sorted(think & spin))
        # `give` is exempt: it IS the still the breath falls back to out at the prompt.
        marks = {table[k] for k in ("ok", "fail", "rule", "cut", "ask", "bullet")}
        assert not (think & marks), (name, sorted(think & marks))
        assert table["think"][0] != table["spin"][len(table["spin"]) // 3], (name, "--calm")


def test_the_breath_keeps_its_own_rhythm_whatever_mood_she_is_in(monkeypatch):
    """`thinking` IS the mood, so scaling its mark by `ENERGY['thinking']` was circular — and 0.7 is
    the slowest of her working speeds, which is what made the state she spends most of a turn in the
    sluggish one to watch. The breath is wall clock, so no `tick` can slow it down."""
    caps = a_caps()
    ramp = caps.g["think"]
    first = int(NOW / rows.THINK_S) % len(ramp)
    thinking = a_state(turn_start=NOW - 3.0, status="thinking")
    for mood in ("sleepy", "thinking", "excited"):
        spin = rows.Spin(caps)
        marks = []
        for beat in range(12):
            monkeypatch.setattr(time, "monotonic", lambda b=beat: NOW + b * rows.THINK_S)
            spin.tick(mood)
            marks.append(rows.band_rows(caps, thinking, 94, spin=spin)[0].plain[0])
        assert marks == [ramp[(first + b) % len(ramp)] for b in range(12)], mood


def test_the_breath_is_a_still_under_reduced_motion_and_at_the_prompt(monkeypatch):
    """The at-rest invariant is not negotiable for a new mark: `--calm` freezes it, and out at the
    prompt — where the region passes no spinner — it is the same `▸` every other row wears."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    thinking = a_state(turn_start=NOW - 3.0, status="thinking")
    calm = a_caps(reduced_motion=True)
    spin = rows.Spin(calm)
    frozen = set()
    for beat in range(12):
        monkeypatch.setattr(time, "monotonic", lambda b=beat: NOW + b * rows.THINK_S)
        frozen.add(rows.band_rows(calm, thinking, 94, spin=spin)[0].plain[0])
    assert frozen == {calm.g["think"][0]}
    assert rows.band_rows(a_caps(), thinking, 94)[0].plain[0] == a_caps().g["give"]


@needs_posix_terminal
def test_the_prompts_own_chip_says_working_too_and_goes_back_when_the_job_lands(live, monkeypatch):
    """One bar, two renderers. A chip that read WORKING in rich and LIVE in prompt_toolkit would be a
    seam in the one surface the long job has."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    assert " LIVE " in "".join(t for _, t in live.app._toolbar())
    live.app.work = running_job()
    frags = live.app._toolbar()
    assert " WORKING " in "".join(t for _, t in frags)
    assert frags[0][0] == theme.PT_WORK_CHIP[live.caps.color], "its own slot, not the live chip's"
    assert " WORKING " in footer.bar_text(live.caps, live.screen.face,
                                          live.app._state()).plain, "and the other renderer agrees"
    live.app.work.state, live.app.work.landed = "ok", True
    assert " LIVE " in "".join(t for _, t in live.app._toolbar())


@needs_posix_terminal
def test_the_prompts_chip_mark_rides_the_bands_rate_off_the_one_shared_function(live, monkeypatch):
    """One bar, two renderers, and now one MARK: `footer.chip_dot` is the only place the chip's cell
    is computed, so the swell prompt_toolkit draws and the swell rich draws cannot drift. The rate is
    `rows.BEAT_HZ` — the number `app._wake` already wakes the prompt at while a job runs — and the
    bar's fingerprint carries the drawn frame, so the beat repaints exactly when the mark turns and
    compares equal inside a frame."""
    at = [NOW]
    monkeypatch.setattr(time, "monotonic", lambda: at[0])
    live.app.work = running_job()
    step = 1.0 / rows.BEAT_HZ
    marks = []
    for n in range(len(footer.DOT_FRAMES)):
        at[0] = NOW + (n + 0.5) * step
        chip = next(t for _, t in live.app._toolbar() if "WORKING" in t)
        marks.append(chip.split()[0])
    assert marks == [live.caps.g[k] for k in footer.DOT_FRAMES]
    at[0] = NOW
    live.app._rendered(None)
    at[0] = NOW + step / 8
    assert live.app._bar_state() == live.app._painted, "inside one frame the wake has nothing to say"
    at[0] = NOW + step
    assert live.app._bar_state() != live.app._painted, "a new frame is something to say"


def test_a_plan_nobody_is_on_states_position_and_never_motion(monkeypatch):
    """THE REPORT: a dead job's open plan kept claiming `▸ <step>` and `step 1 of 8…`
    beside a LIVE chip. `bar_kind` can only reach "plan" with nothing running — every running state
    outranks it — so an ellipsis there is always a motion claim about a plan nobody is on. The band's
    `▸` is the same claim, so a stalled plan's current step wears the committed vocabulary's `◇`
    instead: it was running when its run ended. The plan itself stays on the glass — it is still what
    she meant to do — and the counts (the header, and a tail when the cap hides rows) are facts about
    the list, not about motion."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    idle = a_state(plan=plan_frame("done", "active", "pending", "pending"))
    assert footer.bar_kind(idle) == "plan"
    twins = footer.out_twins(idle, "plan")
    assert twins[0] == "step 2 of 4" and twins[-1] == "2/4"
    assert not any("…" in t for t in twins), "an ellipsis is this vocabulary's motion mark"

    texts = plains(rows.band_rows(a_caps(), idle, 94))
    assert any(t.lstrip().startswith("◇ paso 2") for t in texts), texts
    assert not any("▸" in t for t in texts), "nobody is running this plan"
    assert texts[0].lstrip().startswith("4 tasks (1 done, 3 open)"), texts
    assert not any("… +" in t for t in texts), \
        "everything fits under the cap, so the counts live on the header and no tail claims a window"


def test_the_active_step_keeps_its_mark_while_a_turn_or_the_job_is_actually_on_it(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    tool = state.Tool("web", "reading the page", started=NOW - 6.0)
    turning = a_state(turn_start=NOW - 8.0, tools=(tool,),
                      plan=plan_frame("done", "active", "pending"))
    assert any("▸ paso 2" in t for t in plains(rows.band_rows(a_caps(), turning, 94)))
    jobbed = a_state(work=running_job(), plan=plan_frame("done", "active", "pending"))
    assert any("▸ paso 2" in t for t in plains(rows.band_rows(a_caps(), jobbed, 94)))
