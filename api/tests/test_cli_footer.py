"""What the pinned footer is allowed to draw: the pulse periods, the twins, the ladder, the edges."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from rich.text import Text

from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.kaomoji import Face
from kotoba.cli.render.theme import GLYPHS_ASCII, GLYPHS_UNICODE

#: The blink table, minus `long`: the WORKING mark is a swell at `rows.BEAT_HZ` now, pinned by
#: `test_the_working_mark_swells_at_the_bands_rate_and_never_strobes` below.
PERIODS = {"speak": 0.7, "work": 0.9, "think": 0.9, "peek": 0.9, "helpers": 0.9,
           "wait": 2.0, "okayed": 2.0,
           "card": 0.0, "due": 0.0, "okdone": 0.0, "landed": 0.0, "plan": 0.0, "idle": 0.0}


@dataclass
class Job:
    """The wire's five words, the ones `cli/state.Work` carries — a tool, a helper and the long job all
    speak them, and a footer with a private vocabulary reads every running one as finished.

    `tools` and `helpers` are here because the bar's phrase is the job's CURRENT step, read off them:
    a job with a shell running says `at the terminal…`, and the same job a second after it landed does
    not."""

    state: str = "running"
    landed: bool = False
    verb: str = "at the terminal…"
    elapsed: float = 252.0
    tools: list = field(default_factory=lambda: [Running(state="running", verb="shell", arg="")])
    helpers: list = field(default_factory=list)


@dataclass
class Running:
    """One step of the job, as `rows.work_verb` reads it."""

    state: str = "running"
    verb: str = "shell"
    arg: str = ""


@dataclass
class Held:
    """A deferred command is the one thing that is NOT on that axis: it is one command's lifecycle
    around an approval window, and it keeps its own words."""

    state: str = "run"
    verb: str = "shell"
    told: bool = False
    began: float = 1.0
    elapsed: float = 9.0


@dataclass
class Due:
    msg: str = "call the dentist"
    when: str = "19:30"
    every: str = ""
    told: bool = False


@dataclass
class Run:
    state: str = "running"


@dataclass
class Task:
    order: int = 2


@dataclass
class Plan:
    is_open: bool = True
    total: int = 3
    active_task: Task | None = field(default_factory=Task)


def caps_for(*, color="truecolor", unicode=True, interactive=True, width=100,
             height=24, calm=False) -> Caps:
    return Caps(color=color, background="dark", unicode=unicode, interactive=interactive,
                reduced_motion=calm, width=width, height=height,
                g=dict(GLYPHS_UNICODE if unicode else GLYPHS_ASCII))


def state_for(kind: str, now: float = 0.0, **extra) -> footer.State:
    base = {
        "idle": dict(model="gpt-5.4-mini"),
        "wait": dict(approval=object()),
        "helpers": dict(helpers=(Run(), Run())),
        "peek": dict(peek="get_credential"),
        "think": dict(status="thinking"),
        "work": dict(status="shell"),
        "speak": dict(partial="hello there"),
        "card": dict(holds=(Held(state="ask"),)),
        "due": dict(due=(Due(),)),
        "okayed": dict(holds=(Held(state="run"),)),
        "okdone": dict(holds=(Held(state="ok"),)),
        "long": dict(work=Job()),
        "landed": dict(work=Job(state="ok")),
        "plan": dict(plan=Plan()),
    }[kind]
    return footer.State(turn_start=now, **{**base, **extra})


def frozen(monkeypatch, now: float) -> None:
    monkeypatch.setattr(footer, "time", SimpleNamespace(monotonic=lambda: now))


def dot_at(monkeypatch, kind: str, now: float, *, calm: bool = False) -> str:
    frozen(monkeypatch, now)
    caps = caps_for(calm=calm)
    return footer.bar_text(caps, Face(unicode=True), state_for(kind, now)).plain[1]


def test_the_bar_alternates_on_the_period_each_state_was_measured_at(monkeypatch):
    caps = caps_for()
    for kind, period in PERIODS.items():
        if not period:
            continue
        assert dot_at(monkeypatch, kind, 0.0) == caps.g["dot"], kind
        assert dot_at(monkeypatch, kind, period / 2) == caps.g["ring"], kind
        assert dot_at(monkeypatch, kind, period - 0.01) == caps.g["ring"], kind
        assert dot_at(monkeypatch, kind, period) == caps.g["dot"], kind


def test_a_state_that_is_not_running_never_swaps_its_dot_however_long_you_watch(monkeypatch):
    caps = caps_for()
    for kind, period in PERIODS.items():
        if period:
            continue
        for now in (0.0, 0.35, 0.45, 0.9, 1.0, 1.7, 4.25):
            assert dot_at(monkeypatch, kind, now) == caps.g["dot"], (kind, now)


def test_out_of_turn_okayed_is_the_one_blink_left_and_long_rides_the_swell():
    """RE-RECORDED. This used to pin `out_period` at 2.0 for BOTH okayed and long, on a
    docstring that said "out here the bar is repainted twice a second" — a premise `app._wake`
    retired when the job started waking the prompt at `rows.BEAT_HZ`. `long` moved to `chip_dot`'s
    swell; `okayed` keeps its status blink because a hold never reaches `run` in this binary and
    nothing in `_wake` would sample a swell for it (see `out_period`'s docstring)."""
    assert [k for k in footer.OUT_KINDS if footer.out_period(k)] == ["okayed"]
    assert footer.out_period("okayed") == 2.0
    assert footer.out_period("long") == 0.0, "the job's mark is chip_dot's swell, not a period here"


def test_reduced_motion_stops_the_pulse_dead(monkeypatch):
    caps = caps_for(calm=True)
    for kind, period in PERIODS.items():
        for now in (0.0, 0.35, 0.5, 1.0, 1.7):
            assert dot_at(monkeypatch, kind, now, calm=True) == caps.g["dot"], (kind, now)


def test_the_working_mark_swells_at_the_bands_rate_and_never_strobes(monkeypatch):
    """RE-RECORDED premise. `long` used to blink dot/ring on `out_period`'s 2.0 s, an
    arithmetic sized against an out-of-turn repaint of "twice a second" — a constraint `app._wake`
    retired the day the long job started waking the prompt at `rows.BEAT_HZ`. The chip that says
    WORKING now moves like the fact it reports: the band's own grow-and-settle, four frames at the
    band's own rate, off the same `work_is_live` read the label uses. It is a swell and not a faster
    blink because two states at that rate is three flashes a second — a strobe at the general flash
    threshold — where the swell is 1.5 cycles."""
    step = 1.0 / rows.BEAT_HZ
    caps = caps_for()
    seen = [dot_at(monkeypatch, "long", (n + 0.5) * step)
            for n in range(len(footer.DOT_FRAMES))]
    assert seen == [caps.g[k] for k in footer.DOT_FRAMES]
    assert len(set(seen)) == 3, "a swell through the full dot, not a two-state blink"
    within = {dot_at(monkeypatch, "long", step / 8),
              dot_at(monkeypatch, "long", step * 7 / 8)}
    assert len(within) == 1, "inside one frame the mark holds still"


def test_the_working_mark_is_still_under_calm_and_whole_in_ascii(monkeypatch):
    """`--calm` promises nothing moves, and every swell frame must exist in the seven-bit table —
    a hole there is a hole in the chip, on the state that is watched most."""
    calm = caps_for(calm=True)
    step = 1.0 / rows.BEAT_HZ
    for n in range(len(footer.DOT_FRAMES) + 1):
        assert dot_at(monkeypatch, "long", (n + 0.5) * step, calm=True) == calm.g["dot"]
    ascii_caps = caps_for(unicode=False)
    for n in range(len(footer.DOT_FRAMES)):
        frozen(monkeypatch, (n + 0.5) * step)
        bar = footer.bar_text(ascii_caps, Face(unicode=False),
                              state_for("long", (n + 0.5) * step))
        assert bar.plain[1] == ascii_caps.g[footer.DOT_FRAMES[n]]


def test_an_open_card_keeps_its_status_blink_even_while_the_job_is_live(monkeypatch):
    """The gate outranks the job on the mark too: calm at a safety gate is the design (`bar_text`),
    so the chip still SAYS working and only the mark declines to race — a slow blink, not the
    `BEAT_HZ` swell. RE-RECORDED by the animation audit: the period was pinned as
    `4 * CARD_S`, a derivation from the sampler a card USED to have; no surface samples the wait
    blink at `CARD_S` now (`--calm` draws the chip solid), so the number is the demo's own 2.0 —
    the one blink slower than the rest on purpose — and not an arithmetic about frames."""
    caps = caps_for()
    at_gate = lambda now: footer.bar_text(
        caps, Face(unicode=True), state_for("wait", now, work=Job())).plain[1]
    frozen(monkeypatch, 0.0)
    assert at_gate(0.0) == caps.g["dot"]
    frozen(monkeypatch, 1.0)
    assert at_gate(1.0) == caps.g["ring"]
    assert footer.BLINK["wait"] == 2.0, "the demo's, not 4 x CARD_S"
    assert footer.BLINK["wait"] > max(v for k, v in footer.BLINK.items() if k != "wait")


def test_box_hint_picks_its_twin_by_the_room_it_has_at_a_hundred_sixty_four_and_forty():
    rest = footer.State(at_rest=True)
    assert footer.box_hint(caps_for(width=100), rest, 96) == "talk to her · @ a file · / for commands"
    assert footer.box_hint(caps_for(width=64), rest, 60) == "talk to her · @ a file · / for commands"
    assert footer.box_hint(caps_for(width=40), rest, 36) == "talk to her · / for commands"
    assert footer.box_hint(caps_for(width=20), rest, 16) == "talk to her"


def test_a_hint_shortens_by_twins_and_is_never_ellipsised_away():
    card = footer.State(approval=object())
    for room in range(0, 60):
        hint = footer.box_hint(caps_for(width=room + 4), card, room)
        assert hint in ("the card above is waiting — y, a, n or ?", "y, a, n or ?", "")
        assert "…" not in hint


def test_the_bar_never_paints_past_the_right_edge_of_the_window(monkeypatch):
    frozen(monkeypatch, 0.0)
    for kind in PERIODS:
        for unicode in (True, False):
            for width in range(84, 161):
                caps = caps_for(unicode=unicode, width=width)
                bar = footer.bar_text(caps, Face(unicode=unicode), state_for(kind, -12.0))
                assert bar.cell_len <= width, (kind, width, unicode, bar.plain)


def test_the_frame_is_exactly_as_wide_as_the_window_at_every_width():
    for unicode in (True, False):
        for width in range(40, 161):
            caps = caps_for(unicode=unicode, width=width)
            rows = footer.box_rows(caps, footer.State(at_rest=True))
            assert {r.cell_len for r in rows} == {width}, (width, unicode)


def test_a_full_input_line_stays_inside_its_own_border():
    """The caret is a cell too. Spending the closing border's cell on it made the body row one wider
    than the two rows it sits between, at every width."""
    for width in (40, 64, 80, 120, 200):
        caps = caps_for(width=width)
        rows = footer.box_rows(caps, footer.State(typing="x" * 400))
        assert {r.cell_len for r in rows} == {width}, width


def test_the_bar_fits_the_window_however_narrow_it_gets(monkeypatch):
    """The phrase is fitted against the room the prefix and right slot actually left, not a fixed 34
    cells — the two together want 42. The clock survives whatever else is dropped: it is the thing that
    proves she is not stuck."""
    frozen(monkeypatch, 0.0)
    for unicode in (True, False):
        for width in range(40, 120, 2):
            for kind in PERIODS:
                caps = caps_for(unicode=unicode, width=width)
                bar = footer.bar_text(caps, Face(unicode=unicode), state_for(kind, -12.0))
                assert bar.cell_len <= width, (kind, width, unicode, bar.plain)
    caps = caps_for(unicode=False, width=82)
    bar = footer.bar_text(caps, Face(unicode=False), state_for("peek", -12.0))
    assert bar.plain.endswith("12.0s")


def test_the_ascii_ladder_emits_no_character_above_seven_bits(monkeypatch):
    frozen(monkeypatch, 0.0)
    for kind in PERIODS:
        for width in (40, 52, 56, 60, 64, 80, 100):
            for color, interactive in (("none", False), ("16", True), ("truecolor", True)):
                caps = caps_for(color=color, unicode=False, interactive=interactive, width=width)
                for row in footer.pinned_view(caps, Face(unicode=False), state_for(kind, -12.0)):
                    assert all(ord(c) < 128 for c in row.plain), (kind, width, color, row.plain)


def test_everything_the_turn_is_doing_outranks_everything_out_of_turn():
    out = dict(holds=(Held(state="ask"),), due=(Due(),), work=Job(), plan=Plan())
    assert footer.bar_kind(footer.State(**out)) == "card"
    assert footer.bar_kind(footer.State(status="shell", **out)) == "work"
    assert footer.bar_kind(footer.State(approval=object(), **out)) == "wait"
    assert footer.bar_kind(footer.State(helpers=(Run(),), **out)) == "helpers"


def test_a_gate_outranks_a_nudge_and_both_outrank_her_working():
    holds, due, work = (Held(state="ask"), Held(state="run")), (Due(),), Job()
    assert footer.bar_kind(footer.State(holds=holds, due=due, work=work)) == "card"
    assert footer.bar_kind(footer.State(holds=holds[1:], due=due, work=work)) == "due"
    assert footer.bar_kind(footer.State(holds=holds[1:], work=work)) == "okayed"
    assert footer.bar_kind(footer.State(work=work)) == "long"
    assert footer.bar_kind(footer.State(plan=Plan())) == "plan"
    assert footer.bar_kind(footer.State()) == "idle"


def test_no_twin_shortens_the_instrument_out_of_the_picture():
    job = footer.State(work=Job())
    assert all("long job" in v for v in footer.work_twins(job))
    for state in ("ok", "failed", "interrupted"):
        assert all("long job" in v for v in footer.work_twins(footer.State(work=Job(state=state))))
    okayed = footer.State(holds=(Held(state="run"),))
    assert all("okayed" in v for v in footer.out_twins(okayed, "okayed"))
    assert "long job" in footer._bar_phrase(caps_for(width=44), job, "long", 0.0)


def test_esc_names_the_blast_radius_before_it_happens():
    job, cmd = Job(), Held(state="run")
    assert "the job and the command" in footer.armed_twins(footer.State(work=job, holds=(cmd,)))[0]
    assert "the one you okayed" in footer.armed_twins(footer.State(holds=(cmd,)))[0]
    assert "the long job" in footer.armed_twins(footer.State(work=job))[0]
    assert "all 3" in footer.armed_twins(footer.State(helpers=(Run(), Run()), tools=(Run(),)))[0]


def test_the_arm_never_states_a_blast_radius_of_none_or_of_one_of_them():
    """The arm is set once, on a press with more than one thing out there, and then the phrase is
    rebuilt every frame for three seconds — during which the things it counts LAND. Written for the
    moment of arming only, it ran down through `that stops all 1 of them` to `that stops all 0 of
    them`: the count the arm exists to state, stated wrong, on a warning about someone's twenty
    minutes."""
    for twins in (footer.armed_twins(footer.State()),
                  footer.armed_twins(footer.State(tools=(Run(),)))):
        assert "all 0" not in twins[0] and "all 1" not in twins[0], twins
        assert "esc again" in twins[0], twins
    assert "this turn" in footer.armed_twins(footer.State())[0]
    assert "the one still running" in footer.armed_twins(footer.State(tools=(Run(),)))[0]


def test_the_long_jobs_bar_names_a_gesture_the_surface_drawing_it_actually_binds():
    """One string served both bars. Inside a turn the CLI owns stdin and `esc` reaches
    `app._typeahead`; out at the prompt prompt_toolkit owns it and binds `escape` only as `escape
    enter` and as close-the-completion-list, so the long job's ONLY surface was telling the person to
    press a key that does nothing. `/stop` is the route out there, and `commands` says so in as many
    words: "neither is possible from a keypress"."""
    from kotoba.cli.input import commands

    caps = caps_for(width=100)
    at_prompt = footer.State(work=Job(), at_rest=True)
    in_turn = footer.State(work=Job(), turn_start=1.0)

    prompt_hint = footer.out_right(caps, at_prompt, footer.bar_kind(at_prompt))
    assert "esc" not in prompt_hint, prompt_hint
    named = next(w for w in prompt_hint.split() if w.startswith("/"))
    assert named in commands.COMMANDS, f"{named} is not a command she answers to"
    assert "esc stops it" in footer.out_right(caps, in_turn, footer.bar_kind(in_turn))


def test_a_narrow_window_gives_up_the_long_jobs_words_and_never_its_seconds():
    """The comment over the fallback says the clock is the last thing to go, and for an out-of-turn
    slot it was the FIRST: the variable offered back is this TURN's elapsed, which is 0 while the job
    runs at the prompt, so at 48 columns the right slot went empty — on the one surface the long job
    has. `cli/app._toolbar`, the other renderer of this same bar, already re-asked `out_right` with
    `armed=True` and kept the seconds; the two are supposed to agree cell for cell."""
    from kotoba.cli.render.kaomoji import Face

    st = footer.State(work=Job(elapsed=252.0), model="gpt-5.6-luna", at_rest=True)
    for width in (100, 70, 56, 48, 44, 40):
        drawn = footer.bar_text(caps_for(width=width), Face(unicode=True), st).plain
        assert "4m 12s" in drawn, f"{width} columns lost the job's clock: {drawn!r}"


def test_a_waiting_card_is_offered_a_gesture_and_never_a_countdown():
    caps = caps_for()
    card = footer.State(holds=(Held(state="ask"),))
    right = footer.out_right(caps, card, "card")
    assert right == "⏎ shows it"
    assert not any(c.isdigit() for c in right)
    assert footer.out_right(caps, footer.State(plan=Plan()), "plan") == ""


def test_the_long_jobs_clock_reads_in_minutes_and_gives_the_hint_up_first():
    job = footer.State(work=Job(elapsed=252.0))
    assert footer.work_right(caps_for(width=100), job) == "esc stops it   4m 12s"
    assert footer.work_right(caps_for(width=44), job) == "4m 12s"
    assert footer.work_right(caps_for(width=100), job, armed=True) == "4m 12s"


def test_what_she_is_still_typing_and_what_is_held_back_are_one_block():
    assert footer.State(partial="b").speaking == "b"
    assert footer.State(held="a").speaking == "a"
    assert footer.State(held="a", partial="b").speaking == "a\n\nb"


class Parts:
    def tool_text(self, tool):
        return Text("tool")

    def roster_rows(self):
        return []

    def approval_rows(self):
        return [Text("card")]

    def confirm_rows(self):
        return [Text("confirm")]

    def head_plate(self, live: bool = False):
        return Text("plate")

    def at_gutter(self, row):
        return row

    def prose(self, block: str):
        return Text(block)

    def link_rows(self, block: str):
        return []


def test_her_live_block_is_painted_early_whether_or_not_a_tool_is_running():
    """Her face goes on from the first frame she speaks. It used to be refused with anything in flight,
    because her block was drawn UNDER the running row and commits over it — so with `web_search` out
    she had a nameplate and a kaomoji and no portrait until the tool landed."""
    caps = caps_for()
    alone = footer.flow_view(caps, footer.State(partial="hi"), Parts())
    assert alone.her_at == 0 and alone.owes_tail
    busy = footer.flow_view(caps, footer.State(partial="hi", tools=(Run(),)), Parts())
    assert busy.her_at == 0


def test_the_region_holds_the_blank_row_a_landing_block_is_going_to_want():
    caps = caps_for()
    assert not footer.flow_view(caps, footer.State(), Parts()).owes_tail
    gap_only = footer.flow_view(caps, footer.State(owes_gap=True), Parts())
    assert gap_only.rows == [Text("")] and not gap_only.owes_tail
    assert footer.flow_view(caps, footer.State(owes_gap=True, tools=(Run(),)), Parts()).owes_tail


def test_a_tool_still_running_is_drawn_under_her_staged_block_because_it_commits_under_it():
    """`Screen.row` flushes what she has staged BEFORE it prints, so a block she has not committed yet
    lands above the tool row, never below it. The blank between them is the gap that block owes.

    Once she HAS a plate in the transcript the order flips, and for the same reason: what she is only
    typing does not commit, so the machine row goes above it."""
    caps = caps_for()
    running = footer.flow_view(caps, footer.State(partial="hi", tools=(Run(),)), Parts())
    landed = footer.flow_view(caps, footer.State(partial="hi", rows_committed=True), Parts())
    said = footer.flow_view(caps, footer.State(partial="hi", said_plate=True, tools=(Run(),)), Parts())
    assert [r.plain for r in running.rows] == ["plate", "hi", "", "tool"]
    assert [r.plain for r in landed.rows] == ["", "plate", "hi"]
    assert [r.plain for r in said.rows] == ["tool", "hi"]


def test_the_line_up_is_drawn_after_her_because_it_commits_after_her():
    class WithRoster(Parts):
        def roster_rows(self):
            return [Text("helper")]

    caps = caps_for()
    flow = footer.flow_view(caps, footer.State(partial="hi", helpers=(Run(),)), WithRoster())
    assert [r.plain for r in flow.rows] == ["plate", "hi", "", "helper"]
    assert flow.her_at == 0 and flow.tail_at == 2


def test_a_url_of_hers_is_tail_so_it_clears_the_art_beside_her_block():
    """Her block claims the whole height of the face beside it. A URL drawn inside that height here
    lands below it in the transcript, and drops two rows the moment she stops typing."""
    class WithLink(Parts):
        def link_rows(self, block: str):
            return [Text("https://x.example/" + "p" * 200)]

    caps = caps_for()
    flow = footer.flow_view(caps, footer.State(partial="hi", queued=("luego",)), WithLink())
    assert flow.tail_at == 2
    assert [r.plain[:5] for r in flow.rows] == ["plate", "hi", "https", "› lue"]


def test_queued_lines_land_under_everything_the_turn_is_already_holding():
    caps = caps_for()
    flow = footer.flow_view(caps, footer.State(tools=(Run(),), queued=("one", "two")), Parts())
    assert flow.tail_at == 1
    assert [r.plain for r in flow.rows[1:]] == ["› one   ⏎ queued", "› two   ⏎ queued"]


def test_the_chip_says_working_while_the_job_is_live_and_goes_back_to_live_when_it_lands():
    """Reported live: showing LIVE during a job is confusing. While a job ran, the loudest device in
    the bar announced LIVE — a fact the user already knew — and the one thing they wanted to know sat
    in the dim phrase beside it. The WORKING chip is new here — the bar had no third state before — so
    it is not a restoration of anything. It is off `work_is_live`, the read the phrase and the band's
    job row already use, so
    a chip that says WORKING and a phrase that says the job landed cannot both be on the glass."""
    caps, face = caps_for(), Face()
    assert " WORKING " in footer.bar_text(caps, face, footer.State(work=Job())).plain
    assert " LIVE " in footer.bar_text(caps, face, footer.State(model="m")).plain
    landed = footer.State(work=Job(state="ok", landed=True))
    assert " LIVE " in footer.bar_text(caps, face, landed).plain
    ringing = footer.State(phase="ringing", boot_line="waking up", work=Job())
    assert " RINGING " in footer.bar_text(caps, face, ringing).plain, "the call's phase still wins"


def test_the_working_chip_keeps_every_fallback_the_live_one_had():
    ascii_caps = caps_for(unicode=False)
    assert " WORKING " in footer.bar_text(ascii_caps, Face(unicode=False),
                                          footer.State(work=Job())).plain
    piped = footer.bar_text(caps_for(color="none", interactive=False), Face(),
                            footer.State(work=Job())).plain
    assert piped.startswith("[WORKING]"), "no colour and no tty: the bracketed form carries the label"
    on_a_tty = footer.bar_text(caps_for(color="none"), Face(), footer.State(work=Job())).plain
    assert " WORKING " in on_a_tty


def test_the_wider_chip_still_never_paints_past_the_right_edge():
    """WORKING is seven cells against LIVE's four, so every width is swept again — including the last
    resort that gives the model's name up before the row is allowed to wrap."""
    for width in (40, 44, 50, 56, 60, 64, 72, 80, 100, 120):
        for state in (footer.State(model="gpt-5.4-mini"), footer.State(model="gpt-5.4-mini",
                                                                       work=Job())):
            for unicode_ok in (True, False):
                caps = caps_for(width=width, unicode=unicode_ok)
                bar = footer.bar_text(caps, Face(unicode=unicode_ok), state)
                assert bar.cell_len <= width, f"{width}: {bar.plain!r}"


# --- the long job's helpers, said where the bar is -------------------------------------------------

def _job_with_helpers(*states: str) -> Job:
    from kotoba.cli import state

    helpers = [state.Helper(f"h{i}", "research", f"prices of service {i}", state=st, started=1.0)
               for i, st in enumerate(states, 1)]
    return Job(tools=[Running(state="running", verb="tool", arg="delegate")], helpers=helpers)


def test_the_bar_says_how_many_helpers_the_long_job_has_out_and_where_to_read_them():
    """Reported live over two QA rounds: without this you have to run `/helpers` just to find out
    whether she delegated at all.

    The band's row named the `delegate` step and the bar said `on it…`; nothing at the bottom said
    a line-up was out. The bar is the surface that survives a fold, so the COUNT and the pointer go
    there — true in each state: out, some back, none."""
    from kotoba.cli.input import commands

    st = footer.State(work=_job_with_helpers("running", "running", "queued"), at_rest=True)
    twins = footer.work_twins(st)
    assert twins[0] == "the long job — 3 helpers out · /helpers shows them", twins
    assert all("long job" in v for v in twins), "the instrument's marker, in every twin"
    named = next(w for w in twins[0].split() if w.startswith("/"))
    assert named in commands.COMMANDS, f"{named} is not a command she answers to"
    drawn = footer.bar_text(caps_for(width=100), Face(unicode=True), st).plain
    assert "3 helpers out" in drawn and "/helpers" in drawn, drawn

    st.work.helpers[0].state = "ok"
    assert footer.work_twins(st)[0] == "the long job — 2 helpers out, 1 back · /helpers shows them"
    for h in st.work.helpers:
        h.state = "ok"
    assert footer.work_twins(st)[0] == "the long job — on it…", "none out: no helper claim at all"


def test_the_helper_count_shortens_by_twins_and_never_takes_the_seconds():
    """The ladder the long job already keeps: a phrase that shortens reads as a decision, and the words
    `long job` — the marker every byte budget for this bar is measured against — survive every twin
    down to the shortest; the right slot's clock is the last thing to go, exactly as before the count
    joined the phrase."""
    st = footer.State(work=_job_with_helpers("running", "running"), model="gpt-5.6-luna",
                      at_rest=True)
    st.work.elapsed = 102.0
    caps = caps_for(width=100)
    for room in (60, 40, 30, 22, 12, 8):
        phrase = footer._bar_phrase(caps, st, "long", 0.0, room=room)
        assert "long job" in phrase, (room, phrase)
        assert not phrase.endswith("…") or phrase in ("the long job…", "long job…"), phrase
    assert "2 out" in footer._bar_phrase(caps, st, "long", 0.0, room=22)
    for width in (100, 80, 64, 56, 48, 44, 40):
        drawn = footer.bar_text(caps_for(width=width), Face(unicode=True), st).plain
        assert "1m 42s" in drawn, f"{width} columns lost the clock: {drawn!r}"
    assert "2 helpers out" in footer.bar_text(caps_for(width=100), Face(unicode=True), st).plain
    assert "2 helpers out" not in footer.bar_text(caps_for(width=44), Face(unicode=True), st).plain


def test_the_helper_hint_is_a_still_that_moves_only_when_the_line_up_does(monkeypatch):
    """Idle must stay at 0 B/s: the phrase is a function of the line-up's states and of nothing
    that ticks, so `_bar_state` repaints the bar once when a helper comes back and never between."""
    st = footer.State(work=_job_with_helpers("running", "running"), at_rest=True)
    frozen(monkeypatch, 1.0)
    first = footer.out_twins(st, footer.bar_kind(st))
    frozen(monkeypatch, 7.0)
    assert footer.out_twins(st, footer.bar_kind(st)) == first
    st.work.helpers[1].state = "ok"
    assert footer.out_twins(st, footer.bar_kind(st)) != first


def test_inside_a_turn_the_bar_names_the_turn_and_the_band_carries_the_jobs_helpers():
    """The bar's ladder is untouched: a turn over the job outranks it, and the count then lives on
    the band's job row (`rows.work_said`), which is the surface the two-row band was built for."""
    st = footer.State(work=_job_with_helpers("running", "queued"), turn_start=1.0, status="thinking")
    assert footer.bar_kind(st) == "think"
    row = rows.band_rows(caps_for(width=96), st, 94)[0].plain
    assert "2 helpers out · prices of service 1" in row, row
