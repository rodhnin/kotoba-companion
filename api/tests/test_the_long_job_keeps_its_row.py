"""The long job owns row one of the band, and a turn cannot take it.

One row used to carry two independent facts — what the TURN is doing and what the JOB is doing — and
resolved the clash by letting the turn win. That is the regression reported live: a message typed while
a job ran took the row above the box out, with the job still going. The fix gives each fact its own
row: row one is the job, from start until it lands, and what she is doing right now sits under it.

The fold keeps the job for the same reason: during a turn the bar under the box already names the turn,
so the turn's activity has a second home and the job has none.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from kotoba.cli import state
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.theme import GLYPHS_ASCII, GLYPHS_UNICODE

NOW = 10_000.0


def a_caps(**over) -> Caps:
    base = dict(color="none", background="dark", unicode=True, interactive=False,
                width=96, height=24, g=dict(GLYPHS_UNICODE))
    base.update(over)
    return Caps(**base)


def a_state(**over) -> footer.State:
    fields = dict(over)
    if isinstance(fields.get("plan"), dict):
        fields["plan"] = state.Plan(fields["plan"])
    return footer.State(**fields)


def plan_frame(*statuses) -> dict:
    return {"list_id": "L", "title": "informe", "status": "open",
            "tasks": [{"id": str(i), "text": f"paso {i}", "status": s, "order": i}
                      for i, s in enumerate(statuses, 1)]}


def running_job(at: float = NOW) -> state.Work:
    job = state.Work("get the real latency numbers", 1, t0=at - 72.0)
    job.tools.append(state.Tool("web", "web_search 'flash v2.5 first byte'", started=at - 4.0))
    return job


def plains(drawn) -> list[str]:
    return [r.plain for r in drawn]


def test_the_job_keeps_row_one_while_she_speaks_over_it(monkeypatch):
    """The exact shape of the regression: a message sent, her answer streaming, the job still on."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    st = a_state(work=running_job(), turn_start=NOW - 2.0, partial="Ya la")
    band = rows.band_rows(a_caps(), st, 94)
    assert band, "the job did not stop because he typed at her"
    assert "web_search 'flash v2.5 first byte'" in band[0].plain
    assert band[0].plain.endswith("1m 12s")


def test_what_she_is_doing_now_sits_under_the_job_and_never_over_it(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    tool = state.Tool("web", "web_search 'live2d lipsync'", started=NOW - 6.0)
    st = a_state(work=running_job(), turn_start=NOW - 8.0, status="web", tools=(tool,))
    band = plains(rows.band_rows(a_caps(), st, 94))
    assert len(band) == 2
    assert "web_search 'flash v2.5 first byte'" in band[0], "row one is the job"
    assert "web_search 'live2d lipsync'" in band[1], "the turn's own tool goes under it"


def test_the_job_row_outlives_a_whole_turn(monkeypatch):
    """Starting, thinking, speaking, ending: the row is there for all four."""
    at = [NOW]
    monkeypatch.setattr(time, "monotonic", lambda: at[0])
    job = running_job()
    for over in (dict(),
                 dict(turn_start=NOW - 1.0, status="thinking"),
                 dict(turn_start=NOW - 4.0, partial="Ya la"),
                 dict()):
        band = rows.band_rows(a_caps(), a_state(work=job, **over), 94)
        assert band and "web_search 'flash v2.5 first byte'" in band[0].plain, over


def test_the_job_row_goes_when_the_job_does_and_not_before(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    job = running_job()
    assert rows.band_rows(a_caps(), a_state(work=job), 94)
    job.state, job.stopped, job.landed = "ok", NOW, True
    assert rows.band_rows(a_caps(), a_state(work=job), 94) == [], \
        "`work_is_live` is the one reading, the same three other surfaces use"


def test_folded_to_one_row_the_job_is_the_row_that_survives(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    tool = state.Tool("web", "web_search 'live2d lipsync'", started=NOW - 6.0)
    st = a_state(work=running_job(), turn_start=NOW - 8.0, status="web", tools=(tool,),
                 folded=True, plan=plan_frame("done", "active", "pending"))
    band = rows.band_rows(a_caps(), st, 94)
    assert len(band) == 1 and "web_search 'flash v2.5 first byte'" in band[0].plain
    for caps, width in ((a_caps(height=19), 94), (a_caps(), rows.ROSTER_MIN_W - 1)):
        one = rows.band_rows(caps, a_state(work=running_job(), turn_start=NOW - 8.0,
                                           status="web", tools=(tool,)), width)
        assert len(one) == 1 and "flash v2.5" in one[0].plain


def test_the_plan_gives_its_rows_up_to_the_job_and_not_the_other_way_round(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    tool = state.Tool("web", "web_search 'live2d lipsync'", started=NOW - 6.0)
    st = a_state(work=running_job(), turn_start=NOW - 8.0, status="web", tools=(tool,),
                 plan=plan_frame("done", "active", "pending", "pending", "pending"))
    for room in (1, 2, 3, 4):
        band = rows.band_rows(a_caps(), st, 94, room=room)
        assert len(band) <= room, room
        assert "web_search 'flash v2.5 first byte'" in band[0].plain, room
    assert len(rows.band_rows(a_caps(), st, 94, room=2)) == 2


def test_the_two_rows_never_outgrow_the_window_they_were_given(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    tool = state.Tool("web", "web_search 'a very long query about pixi-live2d-display'",
                      started=NOW - 6.0)
    st = a_state(work=running_job(), turn_start=NOW - 8.0, status="web", tools=(tool,),
                 plan=plan_frame("done", "active", "pending", "pending"))
    for width in (119, 95, 71, 43):
        for row in rows.band_rows(a_caps(width=width + 1, height=30), st, width):
            assert row.cell_len <= width, (width, row.plain)


# --- the mark ---------------------------------------------------------------------------------------

def test_the_job_wears_the_arrow_growing_in_her_grape_and_never_the_spinners_sun():
    """It grows INTO the arrow and back down, so the loop has no jump: a cycle that snapped from `▸`
    to `·` was the stutter that was reported."""
    caps = a_caps()
    # Sampled at each frame's CENTRE. On the boundary this is testing float division, not the design:
    # 7 * (1/6) * 6 is 6.9999…, which truncates into the frame before it.
    step = 1.0 / rows.BEAT_HZ
    seen = [rows.beat_mark(caps, (s + 0.5) * step).plain for s in range(8)]
    assert seen == [caps.g["bullet"], caps.g["pip"], caps.g["give"], caps.g["pip"]] * 2, seen
    assert caps.g["give"] == rows.still_glyph(caps, "grape").plain, "calm is a frame of the animation"
    assert {rows.beat_mark(caps, s * step).style for s in range(4)} == {"grape"}
    assert all(f not in caps.g["spin"] for f in seen), "the spinner's frames are a tool's, not hers"


def test_the_mark_moves_at_the_rate_the_beat_wakes_at():
    """Re-recorded. This pinned one frame per SECOND, which cost nothing because the row was already
    being rewritten for its clock — and produced a three-second loop that read as the animation being
    slow rather than as an animation. The rate is the point of the thing, so it is what is pinned:
    `BEAT_HZ` frames a second, and `app._wake` waking at exactly that so the two cannot drift."""
    caps = a_caps()
    step = 1.0 / rows.BEAT_HZ
    assert rows.beat_mark(caps, 4.0 + step / 2).plain != rows.beat_mark(caps, 4.0 + step * 1.5).plain, (
        "a frame does not turn over in one period — the mark is slower than the beat that draws it")
    within = {rows.beat_mark(caps, 4.0 + step / 4 + t * step / 16).plain for t in range(8)}
    assert len(within) == 1, "the mark changes faster than a frame — writes nobody can see"
    assert len(rows.BEAT_FRAMES) / rows.BEAT_HZ < 1.0, "a loop this long reads as a stutter"


def a_bare_app(**caps):
    """An `App` with only what `_wake` reads. RE-RECORDED by the animation audit: the wake
    is `footer.beat_hz` now — the surface's rate folded — so it reads `caps.fps` and a state."""
    from kotoba.cli.app import App
    from kotoba.cli.render import footer

    app = App.__new__(App)
    app.caps = SimpleNamespace(**{"reduced_motion": False, "fps": 12, **caps})
    app.work = SimpleNamespace(state="running")
    app.holds = []
    app._deliverable = lambda: False
    app._card_deliverable = lambda: False
    app._state = lambda: footer.State()
    return app


def test_the_beat_wakes_exactly_as_often_as_the_mark_moves():
    """Two numbers that must agree, asserted against each other rather than kept equal by hand: waking
    slower than the mark draws is the stutter, waking faster is writes nobody sees. Over SSH the
    mark is phased at `caps.fps` = 4 (`footer.beat_hz`), and the wake follows it there too."""
    assert a_bare_app()._wake() == pytest.approx(1.0 / rows.BEAT_HZ)
    assert a_bare_app(fps=4)._wake() == pytest.approx(1.0 / 4)


def test_the_beat_slows_back_to_the_clock_under_calm():
    """`/calm` holds the mark still (`beat_mark`), and a beat still waking at `BEAT_HZ` is paying the
    animation's whole CPU bill for a frame that never changes — measured on the real binary, 3.4% of
    a core against the 1.5% the clock rate costs. The clock beside the mark is the only thing left
    turning over, once a second, so the wake drops back to the half-second that catches a tick —
    `footer.CARD_S`, through `frame_hz`, never a second `0.5`."""
    from kotoba.cli.render import footer

    assert a_bare_app(reduced_motion=True)._wake() == 0.5 == footer.CARD_S


def test_the_mark_holds_still_under_calm_and_stays_inside_ascii():
    step = 1.0 / rows.BEAT_HZ
    calm = a_caps(reduced_motion=True)
    assert {rows.beat_mark(calm, (s + 0.5) * step).plain for s in range(12)} == {calm.g["give"]}
    plain = a_caps(unicode=False, g=dict(GLYPHS_ASCII))
    seen = [rows.beat_mark(plain, (s + 0.5) * step).plain for s in range(4)]
    assert seen == [".", "*", ">", "*"] and all(ord(c) < 128 for c in "".join(seen))
