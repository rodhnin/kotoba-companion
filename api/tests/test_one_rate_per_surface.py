"""One rate per surface: a mark is phased for the rate of the thing drawing it, never for its own.

A mark phased at 6 Hz on a region sampled at 2 advanced three frames per draw and then stalled —
motion that reads as a stutter, and key-to-redraw measured p50 240 ms against 52 ms unthrottled.
Cards sample at `caps.fps` like the rest of the turn now; SSH still drops the region to 4 fps, and
the fold is what keeps every frame of a ramp visible, in order, on a surface that slow."""
from __future__ import annotations

import time

from kotoba.cli import state
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.cards import Approval
from kotoba.cli.render.kaomoji import Face
from kotoba.cli.render.theme import GLYPHS_UNICODE

NOW = 10_000.0


def a_caps(**over) -> Caps:
    base = dict(color="none", background="dark", unicode=True, interactive=False,
                width=96, height=30, g=dict(GLYPHS_UNICODE))
    base.update(over)
    return Caps(**base)


def a_job() -> state.Work:
    return state.Work("hold a long job on the glass", t0=NOW - 60.0)


def a_card() -> Approval:
    return Approval("echo listo > qa-aprobado.txt", "", "echo")


def job_marks(monkeypatch, *, card: bool, step: float, draws: int = 8) -> list[str]:
    """The band's job-row mark as `pinned_view` draws it, one draw every `step` seconds."""
    caps, job = a_caps(), a_job()
    out = []
    for k in range(draws):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) * step)
        st = footer.State(work=job, approval=a_card() if card else None)
        band = footer.pinned_view(caps, Face(unicode=True), st)
        out.append(band[0].plain[0])
    return out


def test_the_job_mark_under_an_open_card_is_the_job_mark_beside_a_running_tool(monkeypatch):
    """Re-recorded after the lag round: a card no longer slows the surface, so the band's mark under
    it is the same sequence of draws and glyphs it is with no card — phased at `BEAT_HZ`, sampled at
    the turn's own rate. The old oracle here pinned the swell at 2 Hz; that surface no longer
    exists."""
    step = 1.0 / a_caps().fps
    with_card = job_marks(monkeypatch, card=True, step=step, draws=12)
    without = job_marks(monkeypatch, card=False, step=step, draws=12)
    assert with_card == without, (with_card, without)
    assert len(set(with_card)) > 1, "still moving"


def test_without_a_card_the_job_mark_keeps_beat_hz(monkeypatch):
    caps = a_caps()
    step = 1.0 / rows.BEAT_HZ
    marks = job_marks(monkeypatch, card=False, step=step)
    first = int((NOW - a_job().t0 + 0.5 * step) * rows.BEAT_HZ) % len(rows.BEAT_FRAMES)
    want = [caps.g[rows.BEAT_FRAMES[(first + k) % len(rows.BEAT_FRAMES)]]
            for k in range(len(marks))]
    assert marks == want, (marks, want)


def test_the_breath_under_an_inline_card_keeps_think_s(monkeypatch):
    """The reachable case the sweep ruled out — a turn is thinking and the job's card opens inline —
    re-recorded after the lag round: the card is sampled at the turn's rate now, so her breath keeps
    `THINK_S` under it, one frame per period, forward."""
    caps = a_caps()
    ramp = caps.g["think"]
    seen = []
    for k in range(9):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) * rows.THINK_S)
        st = footer.State(turn_start=NOW - 3.0, status="thinking", approval=a_card())
        band = footer.pinned_view(caps, Face(unicode=True), st, spin=rows.Spin(caps))
        seen.append(band[0].plain[0])
    first = int((NOW + 0.5 * rows.THINK_S) / rows.THINK_S) % len(ramp)
    want = [ramp[(first + k) % len(ramp)] for k in range(len(seen))]
    assert seen == want, (seen, want)


def test_the_breath_keeps_think_s_when_nothing_throttles(monkeypatch):
    """The in-turn 12/s sampling stays on `THINK_S` — the card rate is the exception, not a new
    default."""
    caps = a_caps()
    ramp = caps.g["think"]
    seen = []
    for k in range(6):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) * rows.THINK_S)
        st = footer.State(turn_start=NOW - 3.0, status="thinking")
        band = footer.pinned_view(caps, Face(unicode=True), st, spin=rows.Spin(caps))
        seen.append(band[0].plain[0])
    first = int((NOW + 0.5 * rows.THINK_S) / rows.THINK_S) % len(ramp)
    assert seen == [ramp[(first + k) % len(ramp)] for k in range(len(seen))]


def test_the_job_mark_over_ssh_steps_one_frame_per_region_draw(monkeypatch):
    """The same rule, one throttle over: `caps.detect` drops the region to 4 fps over SSH, and a
    6 Hz phase sampled at 4 Hz walks the four-frame swell 0,1,3,0,2 — out of order with stalls,
    the card stutter again. `pinned_view` must hand the band the rate this surface actually
    samples at: the lesser of `BEAT_HZ` and its own frame rate."""
    caps = a_caps(fps=4)
    step = 1.0 / caps.fps
    marks = []
    for k in range(8):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) * step)
        st = footer.State(work=a_job())
        marks.append(footer.pinned_view(caps, Face(unicode=True), st)[0].plain[0])
    hz = min(rows.BEAT_HZ, caps.fps)
    first = int((NOW - a_job().t0 + 0.5 * step) * hz) % len(rows.BEAT_FRAMES)
    want = [caps.g[rows.BEAT_FRAMES[(first + k) % len(rows.BEAT_FRAMES)]]
            for k in range(len(marks))]
    assert marks == want, (marks, want)


def test_the_working_chips_swell_over_ssh_steps_one_frame_per_region_draw(monkeypatch):
    """`chip_dot` is the other mark that swells while the job is live, drawn by the same 4 fps
    region over SSH — its rate has to fold to the sampler the same way, or the chip stutters
    beside a band that does not."""
    caps = a_caps(fps=4)
    step = 1.0 / caps.fps
    marks = []
    for k in range(8):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) * step)
        st = footer.State(phase="live", work=a_job())
        marks.append(footer.chip_dot(caps, st, footer.bar_kind(st)))
    hz = min(rows.BEAT_HZ, caps.fps)
    first = int((NOW + 0.5 * step) * hz) % len(footer.DOT_FRAMES)
    want = [caps.g[footer.DOT_FRAMES[(first + k) % len(footer.DOT_FRAMES)]]
            for k in range(len(marks))]
    assert marks == want, (marks, want)


# --- the tool spinner: the third mark, missed twice --------------------------------------------------
#
# Reported live: two running rows under the open card — the flow's `▪ BASH $ rm -rf …` and the band's
# now row — both badly slowed, the same defect as before. `Spin.spinner` had been left out of both
# earlier fixes on the reasoning that 3 frames per draw over 12 frames "degrades to a coherent slower
# pulse". It does at neutral only: `request_approval` puts her face on `confused`, whose 0.8 makes it
# 2.4 a draw — 0,2,4,5,3,0 — uneven and reversing; `thinking` stalls (1,1); under `--ascii` `confused`
# walks the four frames 0,2,0,3,1. The rule is the band's: one rate per surface, derived, and a
# sampler no faster than the meter shows exactly one frame per draw.

import asyncio
import io
from types import SimpleNamespace

import pytest
from conftest import needs_posix_terminal

from kotoba.cli.render import region as region_mod
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_ASCII, build_console


def a_tool() -> state.Tool:
    return state.Tool("shell", "$ rm -rf -- /tmp/kotoba-prueba-borrado.txt", started=NOW - 60.0)


def walks(cycle: str, marks: list[str]) -> bool:
    """`marks` is `cycle` played from SOME phase, one step per draw. A glyph the cycle repeats — `▄`
    twice in the swell, most of the ramp on its way down — makes `index()` the wrong oracle."""
    n = len(cycle)
    return any(marks == [cycle[(k + i) % n] for i in range(len(marks))] for k in range(n))


def plays(cycle: str, marks: list[str]):
    """(frames moved, draws held) when `marks` is `cycle` from SOME phase with every frame held for
    one draw or more — the shape of a meter slower than its sampler — or None. `walks` is the
    one-per-draw special case of this."""
    n = len(cycle)
    for k in range(n):
        if marks[0] != cycle[k]:
            continue
        pos, moved, held, ok = k, 0, 0, True
        for mark in marks[1:]:
            if mark == cycle[pos]:
                held += 1
            elif mark == cycle[(pos + 1) % n]:
                pos, moved = (pos + 1) % n, moved + 1
            else:
                ok = False
                break
        if ok and moved:
            return moved, held
    return None


def spinner_marks(monkeypatch, caps, *, mood: str, card: bool, step: float, draws: int = 10,
                  band: bool = False) -> list[str]:
    """The running row's mark as the region draws it: one `tick` per frame at the rate THIS surface
    samples at, then the row — the tool row of the flow, or the band's now row via `pinned_view`."""
    spin, out = rows.Spin(caps), []
    for k in range(draws):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) * step)
        st = footer.State(turn_start=NOW - 70.0, tools=(a_tool(),),
                          approval=a_card() if card else None)
        spin.tick(mood, hz=footer.frame_hz(caps, st))
        if band:
            out.append(footer.pinned_view(caps, Face(unicode=True), st, spin=spin)[0].plain[0])
        else:
            out.append(rows.tool_text(caps, a_tool(), 96, spin=spin).plain[0])
    return out


@pytest.mark.parametrize("mood", ("neutral", "confused", "thinking", "surprised", "sleepy"))
def test_the_tool_rows_spinner_under_an_open_card_plays_the_whole_ramp(monkeypatch, mood):
    """Re-recorded twice, across two live rounds on the same day. The first: one frame per draw at
    2 Hz was a six-second cycle, so the swell was chosen for a sampler slower than the beat. The
    second, once the 2 Hz sampler turned out to BE the lag: a card is drawn at the turn's rate, so
    the mark under it is the twelve-frame ramp that was asked back — the one preferred on sight —
    held per frame for as long as the mood says, exactly as beside a running tool. The swell is
    SSH's now (below)."""
    caps = a_caps()
    marks = spinner_marks(monkeypatch, caps, mood=mood, card=True, step=1.0 / caps.fps, draws=14)
    assert plays(caps.g["spin"], marks), (mood, "".join(marks))
    assert set(marks) - set(rows.swell(caps.g["spin"])), (mood, "the ramp, not the swell")


def test_the_bands_now_row_under_an_open_card_plays_the_whole_ramp(monkeypatch):
    caps = a_caps()
    marks = spinner_marks(monkeypatch, caps, mood="confused", card=True, step=1.0 / caps.fps,
                          draws=14, band=True)
    assert plays(caps.g["spin"], marks), "".join(marks)
    assert set(marks) - set(rows.swell(caps.g["spin"])), "the ramp, not the swell"


def test_the_ascii_spinner_under_an_open_card_is_held_per_frame_like_a_turns(monkeypatch):
    """`.oOo` is four frames whichever way it is sampled, so the tell here is the HOLD: at 12 Hz a
    `confused` meter fills 0.8 of a frame per draw and some glyph stays for two draws; the 2 Hz fold
    stepped exactly one frame per draw and never held."""
    caps = a_caps(unicode=False, g=dict(GLYPHS_ASCII))
    marks = spinner_marks(monkeypatch, caps, mood="confused", card=True, step=1.0 / caps.fps,
                          draws=14)
    played = plays(caps.g["spin"], marks)
    assert played and played[1] > 0, "".join(marks)
    assert rows.swell(caps.g["spin"]) == caps.g["spin"], "`.oOo` is four frames already: unchanged"


@pytest.mark.parametrize("mood", ("neutral", "confused"))
def test_the_tool_rows_spinner_over_ssh_steps_one_frame_per_region_draw(monkeypatch, mood):
    caps = a_caps(fps=4)
    marks = spinner_marks(monkeypatch, caps, mood=mood, card=False, step=1.0 / caps.fps)
    assert walks(rows.swell(caps.g["spin"]), marks), (mood, "".join(marks))
    assert len(rows.swell(caps.g["spin"])) / caps.fps == 1.0, "one second a cycle at 4 Hz, the beat's"


def test_a_sampler_faster_than_the_meter_still_shows_every_frame_in_order(monkeypatch):
    """The fold is for a sampler the meter outruns. In a turn at 12/s a `sleepy` 0.35 fills the meter
    at 4.2 frames a second — slower than the surface — and that is her mood, not a tear: every frame
    is drawn, in order, held for as long as the mood says."""
    caps = a_caps()
    marks = spinner_marks(monkeypatch, caps, mood="sleepy", card=False, step=1.0 / caps.fps,
                          draws=24)
    # The ramp repeats glyphs on its way down, so the oracle walks the CYCLE rather than indexing it.
    frames, pos, held = caps.g["spin"], caps.g["spin"].index(marks[0]), 0
    for mark in marks[1:]:
        if mark == frames[pos]:
            held += 1
        elif mark == frames[(pos + 1) % len(frames)]:
            pos = (pos + 1) % len(frames)
        else:
            raise AssertionError(("".join(marks), mark, frames[pos]))
    assert held and pos != frames.index(marks[0]), "".join(marks)


def test_the_surface_rate_is_one_number(monkeypatch):
    """`footer.frame_hz` is the clock's sleep inverted and the only copy of it: `app._clock` sleeps
    on it, `pinned_view` phases the band off it, `region.LiveView` ticks the spinner at it."""
    caps = a_caps()
    assert footer.frame_hz(caps, footer.State()) == caps.fps
    # A card is a surface at the turn's own rate; only `--calm` folds to `CARD_S`.
    assert footer.frame_hz(caps, footer.State(approval=a_card())) == caps.fps
    assert footer.frame_hz(caps, footer.State(confirm=SimpleNamespace())) == caps.fps
    assert footer.frame_hz(a_caps(reduced_motion=True), footer.State()) == 1.0 / footer.CARD_S
    assert footer.frame_hz(a_caps(reduced_motion=True), footer.State(approval=a_card())) == (
        1.0 / footer.CARD_S)


def _screen(caps) -> Screen:
    return Screen(caps, console=build_console(caps, file=io.StringIO()),
                  portrait=Portrait(caps, wanted=False))


@needs_posix_terminal
def test_the_clock_sleeps_on_the_rate_the_marks_are_phased_for(monkeypatch):
    from kotoba.cli import app as appmod

    caps = a_caps(interactive=True)
    app = appmod.App(caps, _screen(caps), prompt=None)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        app.region = None

    monkeypatch.setattr(appmod.asyncio, "sleep", sleep)
    for card in (None, a_card()):
        app.approval = card
        app.region = SimpleNamespace(refresh=lambda: None)
        asyncio.run(app._clock())
    assert slept == [1.0 / footer.frame_hz(caps, footer.State()),
                     1.0 / footer.frame_hz(caps, footer.State(approval=a_card()))], slept


class _Parts:
    seated_k = 0

    def __init__(self, caps, screen, spin) -> None:
        self.caps, self.screen, self.spin = caps, screen, spin

    def tool_text(self, tool):
        return rows.tool_text(self.caps, tool, self.screen.rw, spin=self.spin)

    def roster_rows(self):
        return []

    def approval_rows(self):
        return []

    def seated_rows(self):
        return []

    def confirm_rows(self):
        return []

    def head_plate(self, live=False):
        return self.screen.plate(live=live)

    def at_gutter(self, row):
        return self.screen.at_gutter(row)

    def prose(self, block):
        return self.screen.at_gutter(block)

    def link_rows(self, block):
        return []


def test_the_region_ticks_the_spinner_at_the_rate_it_is_sampled_at(monkeypatch):
    caps = a_caps(interactive=True)
    screen = _screen(caps)
    spin = rows.Spin(caps)
    seen: list = []
    monkeypatch.setattr(spin, "tick", lambda mood="neutral", hz=None: seen.append(hz))
    for st in (footer.State(turn_start=NOW - 1.0, tools=(a_tool(),)),
               footer.State(turn_start=NOW - 1.0, tools=(a_tool(),), approval=a_card())):
        view = region_mod.LiveView(screen, spin, lambda st=st: st, _Parts(caps, screen, spin))
        list(view.__rich_console__(screen.console, screen.console.options))
    assert seen == [float(caps.fps), float(caps.fps)], seen


# --- the frame table follows the rate: fewer frames, not slower ones -------------------------------

def test_the_swell_is_the_ramp_at_the_beats_four_steps_and_shares_no_glyph_with_the_breath():
    """Not a new table: the ramp is a seven-level triangle and the beat's shape is low-mid-high-mid,
    so the swell is the ramp every third frame — `▁▄▇▄` — and `.oOo` is that shape already. The
    spinner's frames must stay apart from `breath`'s in either charset (`Spin.breath`), and a subset
    of the ramp cannot fail that."""
    assert rows.swell(GLYPHS_UNICODE["spin"]) == "▁▄▇▄"
    assert rows.swell(GLYPHS_ASCII["spin"]) == GLYPHS_ASCII["spin"] == ".oOo"
    assert len(rows.swell(GLYPHS_UNICODE["spin"])) == len(rows.BEAT_FRAMES)
    for g in (GLYPHS_UNICODE, GLYPHS_ASCII):
        assert not set(rows.swell(g["spin"])) & set(g["think"]), g["spin"]


def test_under_a_card_the_spinners_cycle_is_the_turns_one_second(monkeypatch):
    """Re-recorded after the lag round. The envelope argument was right about a 2 Hz surface — no
    table reads as motion there — and wrong about which surface a card is: the card's 2 Hz was the
    lag, not a calm. Sampled at the turn's rate the twelve-frame ramp turns in ONE second under the
    card, as it does beside any running tool, and twelve draws are exactly one cycle."""
    caps = a_caps()
    marks = spinner_marks(monkeypatch, caps, mood="neutral", card=True, step=1.0 / caps.fps,
                          draws=12)
    assert walks(caps.g["spin"], marks), "".join(marks)
    assert len(set(marks)) == len(set(caps.g["spin"])), "every level of the ramp, once"
    hz = footer.frame_hz(caps, footer.State(approval=a_card()))
    assert len(caps.g["spin"]) / hz == 1.0
    assert hz >= rows.BEAT_HZ, "the fold is for a surface slower than the beat, and a card is not one"


def test_entering_the_fold_lands_on_a_swell_step_and_keeps_the_ramps_phase(monkeypatch):
    """A card opens mid-turn with a fraction of a frame in the meter. Stepped from there the swell
    is the ramp at an offset — `▂▅▆▃`, +3 +1 -3 -1, a wobble — so the first folded draw snaps to
    the swell's own step, the nearest one AHEAD, and the ramp's phase is otherwise kept."""
    caps = a_caps()
    spin = rows.Spin(caps)
    spin.acc = 5.7
    marks = []
    for k in range(6):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) * footer.CARD_S)
        spin.tick("neutral", hz=1.0 / footer.CARD_S)
        marks.append(spin.spinner(5.0))
    assert walks(rows.swell(caps.g["spin"]), marks), "".join(marks)
    assert marks[0] == caps.g["spin"][6], "5.7 frames in, the next swell step is the peak at 6"


@pytest.mark.parametrize("mood", ("neutral", "determined", "excited"))
def test_in_a_turn_the_whole_ramp_still_plays(monkeypatch, mood):
    """The swell is chosen by the SURFACE, never by the fold. At the turn's own 12/s the meter is no
    faster than the sampler at neutral and outruns it on `determined` — the face every work step
    wears — so both take one frame per draw; and both must still be the twelve-frame ramp, because
    at 12 Hz the ramp turns in a second and the swell would turn three times in it, a strobe."""
    caps = a_caps()
    marks = spinner_marks(monkeypatch, caps, mood=mood, card=False, step=1.0 / caps.fps, draws=12)
    assert walks(caps.g["spin"], marks), (mood, "".join(marks))
    assert set(marks) - set(rows.swell(caps.g["spin"])), "the ramp, not the swell"
    assert caps.fps >= rows.BEAT_HZ > 1.0 / footer.CARD_S, "the threshold is the beat's rate"


def test_calm_keeps_the_spinner_still_under_a_card(monkeypatch):
    caps = a_caps(reduced_motion=True)
    marks = spinner_marks(monkeypatch, caps, mood="neutral", card=True, step=footer.CARD_S)
    assert len(set(marks)) == 1, "".join(marks)


def test_nothing_is_sized_against_card_s_any_more_because_calm_draws_every_mark_still(monkeypatch):
    """RE-RECORDED by the animation audit. This pinned the cycle lengths the breath and the
    chip's swell would have at `CARD_S` — arithmetic for a surface that shows neither: `--calm` is
    the only sampler left at `CARD_S`, and there every mark is a still. What is true instead: at
    every phase of a calm frame the breath, the chip and the spinner draw one glyph each."""
    calm = a_caps(reduced_motion=True)
    spin = rows.Spin(calm)
    seen = {"breath": set(), "chip": set(), "spin": set()}
    for k in range(8):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) * footer.CARD_S)
        st = footer.State(phase="live", work=a_job(), turn_start=NOW - 3.0, status="thinking")
        spin.tick("neutral", hz=footer.frame_hz(calm, st))
        seen["breath"].add(spin.breath())
        seen["chip"].add(footer.chip_dot(calm, st, footer.bar_kind(st)))
        seen["spin"].add(spin.spinner(5.0))
    assert all(len(v) == 1 for v in seen.values()), seen
    assert footer.BLINK["wait"] == 2.0, "the demo's number, not 4 x CARD_S"
