"""After `/stop` → `y`, the settled screen has to carry the fact that the job stopped.

Measured live: both the receipt and the closing bracket PRINTED, and neither survived. The at-rest band
paints over the rows under it and undoes that only when the prompt ends, which at rest never happens —
so the glass kept a plan step and a LIVE chip for a job that was already dead.

Folding the band is the wrong fix: `top` is the app/transcript boundary and the app usually owns
nothing above the frame, so the floor removes the band rather than shortening it. This is the other
shape — the band SAYS it: where a plan was open it shrinks, where it was empty the row covers the
bracket it repeats. The row is a still — a frozen duration and no swell — so it costs nothing at rest."""
from __future__ import annotations

from types import SimpleNamespace

import time

import pytest

from kotoba.cli import state
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.theme import GLYPHS_UNICODE


def a_caps(**over) -> Caps:
    base = dict(color="none", background="dark", unicode=True, interactive=False,
                width=96, height=40, g=dict(GLYPHS_UNICODE))
    base.update(over)
    return Caps(**base)


def a_plan() -> state.Plan:
    return state.Plan({"list_id": "L", "title": "informe", "status": "open",
                       "tasks": [{"id": str(i), "text": f"paso {i}",
                                  "status": "active" if i == 1 else "pending", "order": i}
                                 for i in range(1, 9)]})


def a_job(job_state="interrupted", **over) -> state.Work:
    w = state.Work(goal="el informe largo", n=1)
    w.state, w.stopped, w.landed = job_state, w.t0 + 6.0, True
    for k, v in over.items():
        setattr(w, k, v)
    return w


def at_rest(**over) -> footer.State:
    """The screen the report was made against: no turn, the receipt already printed, the plan still open."""
    fields = dict(at_rest=True, turn_start=0.0, plan=a_plan(), work=a_job())
    fields.update(over)
    return footer.State(**fields)


def band(st: footer.State, **kw) -> list[str]:
    return [r.plain for r in rows.band_rows(a_caps(), st, 96, **kw)]


def test_the_settled_band_says_the_job_stopped():
    """The defect as reported: every row was a plan step, and none of them said it was over."""
    text = band(at_rest())
    assert text, "the band went empty"
    assert "stopped" in text[0], f"row one does not say it stopped: {text[0]!r}"


def test_before_this_the_band_said_only_the_plan():
    """The same state with the ending already shown — which is what every row did before — to keep the
    thing this pin is about visible in the file rather than only in the history."""
    text = band(at_rest(work=a_job(cleared=True)))
    assert not any("stopped" in row for row in text)


@pytest.mark.parametrize("plan", [a_plan(), None])
@pytest.mark.parametrize("size", [(96, 40), (96, 12), (50, 40)])
def test_the_band_takes_at_most_the_one_row_that_says_it(plan, size):
    """Where a plan was open the band SHRINKS, because a head row moves it off the idle allowance. Where
    it was empty — no plan, or a window folded too small for one — the row is one more than there was,
    and it covers the bracket it repeats. That is a swap, not a loss, and one row is the whole of it."""
    width, height = size
    caps = a_caps(width=width, height=height)
    for room in (0, 1, 2, 4, 6):
        held = [r.plain for r in rows.band_rows(caps, at_rest(plan=plan), width, room=room)]
        gone = [r.plain for r in rows.band_rows(
            caps, at_rest(plan=plan, work=a_job(cleared=True)), width, room=room)]
        assert len(held) <= max(len(gone), 1), f"more than one row appeared at room={room}"
        if len(held) > len(gone):
            assert "stopped" in held[0], "the extra row is not the one that says it stopped"


def test_the_row_is_a_still(monkeypatch):
    """Drawn at two different moments it is the same bytes — both halves of it. The duration is the
    job's final one, and the mark is a glyph rather than a swell. A moving row would need a wake, and a
    wake at rest is a cost per second forever."""
    st = at_rest()
    assert band(st) == band(st)
    first = band(st)[0]
    later = time.monotonic() + 97.0
    monkeypatch.setattr(rows.time, "monotonic", lambda: later)
    assert band(st)[0] == first, "something in the row moved with the clock"
    assert "6.0s" in first or "6s" in first, first


def test_the_bar_agrees_with_the_band():
    """One dead job, one story. The bar used to fall through to the open plan and read `step 1 of 8`
    beside a band that knew better."""
    st = at_rest()
    assert footer.bar_kind(st) == "landed"
    assert "stopped" in footer.out_twins(st, "landed")[0]


def test_only_the_ending_she_speaks_to_lets_go_of_the_glass():
    """A job that finished WELL gets her own sentence in the transcript. A failed one does not: every
    failure path sends an empty summary, so it closes with the same bracket a stopped one leaves, under
    the same band. Holding it too was the correction this file exists to remember."""
    ok = at_rest(work=a_job("ok"))
    assert not rows.work_held(ok)
    assert footer.bar_kind(ok) == "plan"

    failed = at_rest(work=a_job("failed"))
    assert rows.work_held(failed)
    assert "didn't work out" in band(failed)[0]


def test_a_running_job_is_untouched():
    """The row this borrows is the running job's own; the live reading must not have moved."""
    live = state.Work(goal="el informe largo", n=1)
    live.tools = [SimpleNamespace(state="running", verb="web_search", arg="pandas")]
    st = footer.State(at_rest=False, turn_start=1.0, work=live, plan=a_plan())
    assert rows.work_is_live(st) and not rows.work_held(st)
    assert "stopped" not in band(st)[0]


@pytest.mark.parametrize("job_state", ["ok", "failed", "interrupted"])
def test_both_surfaces_read_one_vocabulary(job_state):
    """The band and the bar say the same words about the same dead job, because there is one dict.
    Read in the instant before the receipt lands, which is where the bar already carried all three."""
    w = a_job(job_state, landed=False)
    assert footer.work_twins(footer.State(work=w)) == rows.WORK_ENDED_SAID[job_state]
