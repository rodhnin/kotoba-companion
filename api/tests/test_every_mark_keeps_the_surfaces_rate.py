"""The animation audit: every animation must follow the same rate so the whole surface agrees
rather than each mark keeping its own cadence — two rates that must agree can never be two constants.

The ground truth moved the same morning a card's own sampler turned out to be the lag it was being
blamed for, so anything sized or phased against the old rate stood on a premise that had gone. Marks
still keeping their own copy: `_wake` woke at 6 against a chip phased at 4; `FLASH_S` was a demo's
0.45s stretched for the dead 2 Hz card; `.oOo` cycled at its own strobe figure instead of the swell
rate; and the mouth kept moving under `--calm`, which promises nothing moves. `footer.beat_hz` and
`flash_secs` are the one origins now."""
from __future__ import annotations

import io
import time

import pytest
from conftest import needs_posix_terminal

from kotoba.cli import state
from kotoba.cli.app import App
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.kaomoji import Face
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_ASCII, GLYPHS_UNICODE, build_console

NOW = 10_000.0


def a_caps(**over) -> Caps:
    base = dict(color="none", background="dark", unicode=True, interactive=True,
                width=96, height=30, g=dict(GLYPHS_UNICODE))
    base.update(over)
    return Caps(**base)


def an_app(caps: Caps) -> App:
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=False))
    return App(caps, screen, prompt=None)


def walks(cycle, marks: list[str]) -> bool:
    """`marks` is `cycle` from SOME phase, one step per draw."""
    n = len(cycle)
    return any(marks == [cycle[(k + i) % n] for i in range(len(marks))] for k in range(n))


def moved_and_held(cycle: str, marks: list[str]):
    """(frames moved, draws held) walking `cycle` from some phase with holds allowed, or None."""
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
        if ok:
            return moved, held
    return None


# --- the prompt's wake is the surface's rate, folded, and nothing else ------------------------------

def test_the_beat_rate_is_one_function_for_every_surface():
    """`footer.beat_hz` is `min(BEAT_HZ, frame_hz)`: the design speed inside a turn, `caps.fps`
    over SSH, `1 / CARD_S` under `--calm` — and the SAME number for the band, the chip and the
    prompt's wake, so none of them can be phased for a rate another one draws at."""
    st = footer.State()
    assert footer.beat_hz(a_caps(), st) == rows.BEAT_HZ
    assert footer.beat_hz(a_caps(fps=4), st) == 4.0
    assert footer.beat_hz(a_caps(reduced_motion=True), st) == 1.0 / footer.CARD_S
    for caps in (a_caps(), a_caps(fps=4), a_caps(reduced_motion=True)):
        assert footer.beat_hz(caps, st) == min(float(rows.BEAT_HZ), footer.frame_hz(caps, st))


@needs_posix_terminal
def test_over_ssh_the_prompt_wakes_at_the_rate_its_marks_are_phased_for(monkeypatch):
    """`caps.detect` drops the region to 4 fps over SSH — every frame is a round trip there — and
    the band's mark inside a turn already folds to it (`pinned_view`). Out at the prompt the same
    band woke at `BEAT_HZ` = 6 while `chip_dot` beside it was phased at 4: the swell held 2,1,2,1
    wakes per frame, and the beat paid six round trips a second for four frames. The wake, the
    band's phase and the chip's phase are one number now, and on this surface it is 4."""
    caps = a_caps(fps=4)
    app = an_app(caps)
    app.work = state.Work("hold a long job on the glass", t0=NOW - 60.0)
    step = app._wake()
    assert step == pytest.approx(1.0 / min(rows.BEAT_HZ, caps.fps)), step
    marks, dots = [], []
    for k in range(8):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) * step)
        marks.append(app._band()[0].plain[0])
        st = app._state()
        dots.append(footer.chip_dot(caps, st, footer.bar_kind(st)))
    assert walks([caps.g[f] for f in rows.BEAT_FRAMES], marks), marks
    assert walks([caps.g[f] for f in footer.DOT_FRAMES], dots), dots


@needs_posix_terminal
def test_the_fingerprint_the_beat_diffs_on_is_phased_like_the_row_it_paints(monkeypatch):
    """`_bar_state` carries the band's rows so the beat can tell a change from a wake; a fingerprint
    phased at 6 beside a painter phased at 4 would change on wakes where the glass does not."""
    caps = a_caps(fps=4)
    app = an_app(caps)
    app.work = state.Work("hold a long job on the glass", t0=NOW - 60.0)
    step = app._wake()
    for k in range(6):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) * step)
        assert app._bar_state()[-1][0] == app._band()[0].plain


@needs_posix_terminal
def test_under_calm_the_wake_is_the_calm_surfaces_own_frame_and_not_a_second_half_second(monkeypatch):
    """The `0.5` `_wake` returned under `--calm` was `CARD_S` written twice. Derived from
    `footer.frame_hz` it is the same half-second today, and it FOLLOWS the fold: move the calm
    surface's rate and the wake moves with it, which a copied constant cannot do."""
    caps = a_caps(reduced_motion=True)
    app = an_app(caps)
    app.work = state.Work("hold a long job on the glass", t0=NOW - 60.0)
    assert app._wake() == 1.0 / footer.frame_hz(caps, app._state()) == footer.CARD_S
    monkeypatch.setattr(footer, "frame_hz", lambda caps, st: 3.0)
    assert app._wake() == pytest.approx(1.0 / 3.0)


# --- the flash: the prototype's, and longer than a frame of whatever samples it --------------------

def test_the_flash_is_the_demos_at_the_turns_rate_and_outlives_calms_frame():
    """0.45 s is the flash the CLI was designed around: long enough to read as a refusal, short enough
    not to sit on the rail. The port made it `CARD_S + 0.2` because the card
    was sampled at 2 Hz and a 0.45 s span could fall whole between two frames; the card is sampled
    at the turn's rate now, so that reason survives only under `--calm`. `flash_secs` is the designed
    0.45 wherever a frame is shorter than it, and one frame plus a margin where it is
    not."""
    st = footer.State()
    assert footer.FLASH_S == 0.45
    assert footer.flash_secs(a_caps(), st) == footer.FLASH_S
    assert footer.flash_secs(a_caps(fps=4), st) == pytest.approx(0.45)
    assert footer.flash_secs(a_caps(reduced_motion=True), st) == footer.CARD_S + 0.2
    for caps in (a_caps(), a_caps(fps=4), a_caps(reduced_motion=True)):
        assert footer.flash_secs(caps, st) > 1.0 / footer.frame_hz(caps, st)


# --- a four-frame spinner table is a swell, and a swell runs at the beat --------------------------

def test_a_four_frame_spinner_table_fills_at_the_beats_rate_so_it_cannot_strobe(monkeypatch):
    """`Spin.hz` = 12 frames a second is the RAMP's speed: twelve frames, one cycle a second at
    neutral. Applied to `.oOo` it was three cycles a second — the figure the unicode tier was
    protected from ("the swell would strobe three times in a second", `Spin.tick`). A four-frame
    table fills at `BEAT_HZ` instead: 1.5 cycles a second, the chip's and the band's own cadence,
    every glyph held two draws at the turn's 12 Hz. The ramp is untouched."""
    ascii_caps = a_caps(unicode=False, g=dict(GLYPHS_ASCII))
    spin, marks = rows.Spin(ascii_caps), []
    for k in range(24):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) / ascii_caps.fps)
        spin.tick("neutral", hz=float(ascii_caps.fps))
        marks.append(spin.spinner(5.0))
    played = moved_and_held(ascii_caps.g["spin"], marks)
    assert played, "".join(marks)
    moved, held = played
    assert abs(moved - 12) <= 1, ("".join(marks), moved)
    assert held >= 10, ("every glyph held two draws at 12 Hz", "".join(marks))
    assert rows.BEAT_HZ / len(ascii_caps.g["spin"]) == 1.5, "the chip's cycle, not the ramp's three"

    caps, spin, ramp = a_caps(), None, []
    spin = rows.Spin(caps)
    for k in range(24):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) / caps.fps)
        spin.tick("neutral", hz=float(caps.fps))
        ramp.append(spin.spinner(5.0))
    played = moved_and_held(caps.g["spin"], ramp)
    assert played and abs(played[0] - 23) <= 1, ("the twelve-frame ramp still turns once a second",
                                                  "".join(ramp))


def test_over_ssh_the_ascii_spinner_is_still_one_frame_per_draw(monkeypatch):
    caps = a_caps(unicode=False, g=dict(GLYPHS_ASCII), fps=4)
    spin, marks = rows.Spin(caps), []
    for k in range(8):
        monkeypatch.setattr(time, "monotonic", lambda k=k: NOW + (k + 0.5) / caps.fps)
        spin.tick("neutral", hz=float(caps.fps))
        marks.append(spin.spinner(5.0))
    assert walks(caps.g["spin"], marks), "".join(marks)


# --- her face under --calm ------------------------------------------------------------------------

def test_under_calm_her_mouth_stays_shut_and_her_eyes_do_not_blink_through_a_mood_change():
    """`/calm` promises nothing moves (`rows._mark`), and the bar's face was the one mark the
    promise missed: the mouth is fed by the stream and decays 2.2 a second, so at the calm
    surface's 2 Hz it flapped between `ω`, `o` and `O` twice a second, and a mood change put a
    110 ms `－` blink on whichever frame caught it. The prototype did the same; the prototype made no
    such promise. Still under calm: mouth shut, mood settled at once, no blink frame."""
    calm, live = a_caps(reduced_motion=True), footer.State(phase="live", partial="hola")
    face = Face(unicode=True)
    face.feed(12)
    assert "( ･ω･ )" in footer.bar_text(calm, face, live).plain
    face.set("happy")
    assert "( ^ω^ )" in footer.bar_text(calm, face, live).plain, "settled at once, no blink"
    assert face.settled

    moving = Face(unicode=True)
    moving.feed(12)
    assert "( ･O･ )" in footer.bar_text(a_caps(), moving, live).plain, "the turn's surface keeps it"
    moving.set("happy")
    assert "( －O－ )" in footer.bar_text(a_caps(), moving, live).plain, "and the blink"
