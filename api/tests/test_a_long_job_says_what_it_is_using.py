"""The status band's job row: what she is DOING, not what kind of thing it is — and a mark that moves.

Out at the prompt the long job is the only thing row one can be about, and it threw the argument
away: a search, a file write and a browser hop all reached the same twelve words out of `WORK_VERB`
while `/work 1` had the query in hand two rows down. The coarse phrase stays as the fallback for a
narrow window and as the bar's own phrase, so the two rows say two things.

The mark is the other half: nothing passed a `Spin` at the prompt, so row one wore a still `▸` for
the whole job. It now moves one frame per beat of the clock already beside it — asserted here is that
the movement costs the surface nothing it was not already paying."""
from __future__ import annotations

import io
import re
import time
from types import SimpleNamespace

import pytest
from conftest import needs_posix_terminal
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kotoba.cli import state
from kotoba.cli.app import App
from kotoba.cli.input import menu as panels
from kotoba.cli.input.prompt import Prompt
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_ASCII, GLYPHS_UNICODE, build_console

NOW = 10_000.0
TAIL = ["she wrote the file out", "› and then you asked her something", "one more transcript row"]
AT = re.compile(r"\x1b\[([0-9]+);1H")
QUERY = "web_search 'site:modelcontextprotocol.io official specification'"


def a_caps(**over) -> Caps:
    base = dict(color="none", background="dark", unicode=True, interactive=False,
                width=96, height=24, g=dict(GLYPHS_UNICODE))
    base.update(over)
    return Caps(**base)


def a_job(verb: str = "web", arg: str = QUERY, at: float = NOW) -> state.Work:
    job = state.Work("find the official spec", 1, t0=at - 72.0)
    if verb:
        job.tools.append(state.Tool(verb, arg, started=at - 20.0))
    return job


def band(caps, job, width: int = 94) -> str:
    drawn = rows.band_rows(caps, footer.State(work=job), width)
    return drawn[0].plain if drawn else ""


# --- what the row says -----------------------------------------------------------------------------

def test_the_job_row_names_the_running_tools_argument_and_not_only_its_category(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    said = band(a_caps(), a_job())
    assert QUERY in said, said
    assert said[1:].startswith(" " + QUERY) and said.rstrip().endswith("1m 12s")


def test_the_job_row_says_the_same_thing_the_turns_own_rung_says(monkeypatch):
    """One vocabulary. In a turn the row is the tool's argument; out of turn it was the category."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    caps = a_caps()
    tool = state.Tool("web", QUERY, started=NOW - 20.0)
    inside = rows.band_rows(caps, footer.State(turn_start=NOW - 30.0, status="web", tools=(tool,)),
                            94)[0].plain
    outside = band(caps, a_job())
    assert inside.split("  ")[0][1:] == outside.split("  ")[0][1:], "same words, own mark"


def test_every_kind_of_step_reaches_its_own_argument(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    caps = a_caps()
    for verb, arg in (("file", "write_file reports/mcp.md"),
                      ("shell", "bash -lc 'ls -la ~/.kotoba/files'"),
                      ("code", "run Python: import statistics"),
                      ("mcp", "browser__navigate https://modelcontextprotocol.io/")):
        assert arg in band(caps, a_job(verb, arg)), (verb, arg)


def test_a_step_with_no_argument_keeps_the_phrase_it_always_had(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    caps = a_caps()
    assert "looking that up…" in band(caps, a_job("web", ""))
    assert "getting going…" in band(caps, a_job(""))
    landed = a_job()
    landed.tools[0].state, landed.tools[0].stopped = "ok", NOW
    assert "on it…" in band(caps, landed), "a search that landed is not one she is still running"


def test_a_helper_out_is_named_by_its_rank_and_its_goal(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    job = a_job("")
    job.helpers.append(state.Helper("h1", "research", "read both pages and compare",
                                    state="running", started=NOW - 30.0))
    said = band(a_caps(), job)
    assert "research" in said and "read both pages and compare" in said
    job.helpers.append(state.Helper("h2", "web", "check the changelog", state="queued"))
    both = band(a_caps(), job)
    assert both[1:].startswith(" 2 helpers out"), both   # re-recorded: one phrase, bar and band
    assert "read both pages and compare" in both, "the oldest one's goal, not a bare count"


def test_helpers_out_outrank_the_delegate_step_that_sent_them(monkeypatch):
    """`delegate` is a running STEP for exactly as long as its helpers are out, so read tool-first the
    row said `delegate` — a tool's name, no count, no goal — for a whole live line-up of helpers, while
    the bar said `on it…`. The helpers are the step; they are read first."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    job = a_job("tool", "delegate")
    for i, name in enumerate(("ElevenLabs", "OpenAI"), 1):
        job.helpers.append(state.Helper(f"h{i}", "research", f"prices of {name}", state="running",
                                        started=NOW - 5.0))
    said = band(a_caps(), job)
    assert said[1:].startswith(" 2 helpers out · prices of ElevenLabs"), said
    assert "delegate" not in said
    assert rows.work_verb(job) == "sending someone else to look…"


def test_a_helper_with_no_goal_on_the_wire_falls_back_to_the_coarse_phrase(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    job = a_job("")
    job.helpers.append(state.Helper("h1", "research", "", state="running", started=NOW - 30.0))
    assert "sending someone else to look…" in band(a_caps(), job)


def test_the_bar_keeps_the_coarse_phrase_so_the_two_rows_say_two_things(monkeypatch):
    """`work_verb` is the bar's, and it may not follow the band into the argument: the bar's byte budget
    is measured against the twins, which are fixed phrases, and a bar phrase carrying a model-written
    query is a phrase that cannot shorten."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    job = a_job()
    st = footer.State(work=job)
    assert rows.work_verb(job) == "looking that up…"
    assert footer.out_twins(st, footer.bar_kind(st))[0] == "the long job — looking that up…"
    assert QUERY not in " ".join(footer.out_twins(st, footer.bar_kind(st)))


def test_the_coarse_phrase_is_the_twin_a_narrow_window_falls_back_to(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    caps = a_caps(width=64, height=30)
    said = band(caps, a_job(), rows.ROSTER_MIN_W)
    assert "looking that up…" in said and QUERY not in said


def test_the_job_row_still_fits_the_width_it_was_asked_for(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    job = a_job("file", "write_file " + "/very-long-directory-name" * 9 + "/report.md")
    for width in (119, 95, 71, 62):
        for row in rows.band_rows(a_caps(width=width + 1, height=30), footer.State(work=job), width):
            assert row.cell_len <= width, (width, row.plain)


def test_the_ascii_tier_says_it_in_seven_bits(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    caps = a_caps(unicode=False, g=dict(GLYPHS_ASCII))
    job = a_job("")
    job.helpers.append(state.Helper("h1", "research", "read both pages", state="running",
                                    started=NOW - 30.0))
    for drawn in (band(caps, a_job()), band(caps, job)):
        assert all(ord(c) < 128 for c in drawn), drawn


# --- the mark --------------------------------------------------------------------------------------

def test_the_mark_moves_a_frame_every_beat(monkeypatch):
    """Re-recorded. This used to assert the mark could NOT move on the half-second, which is what the
    old once-a-second phase bought and what made the loop take three seconds — read live as the
    animation being slow. It now moves every `1 / BEAT_HZ`, which is also what `app._wake` wakes at.

    Sampled at frame CENTRES: on a boundary this measures float division, not the design."""
    at = [NOW]
    monkeypatch.setattr(time, "monotonic", lambda: at[0])
    caps, job = a_caps(), a_job()
    step = 1.0 / rows.BEAT_HZ
    marks = []
    for n in range(len(rows.BEAT_FRAMES) + 1):
        at[0] = NOW + (n + 0.5) * step
        marks.append(band(caps, job)[0])
    assert marks[0] != marks[1], "a beat did not turn the frame over — the mark is slower than the beat"
    assert len(set(marks)) == len(set(rows.BEAT_FRAMES)), "the cycle does not draw all of its frames"
    assert marks[0] == marks[-1], "the cycle does not close where it started"


def test_calm_keeps_the_still_mark(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    caps = a_caps(reduced_motion=True)
    assert band(caps, a_job()).startswith("▸ "), "/calm means nothing on screen moves"
    assert band(a_caps(), a_job())[0] != "▸", "and the tier that did not ask for it does move"


def test_a_plan_at_an_idle_prompt_is_still_zero_bytes_a_second(monkeypatch):
    """No job means no row one, so there is nothing for a mark to move on and nothing to repaint."""
    at = [NOW]
    monkeypatch.setattr(time, "monotonic", lambda: at[0])
    plan = state.Plan({"list_id": "L", "title": "informe", "status": "open",
                       "tasks": [{"id": "1", "text": "paso 1", "status": "active", "order": 1}]})
    first = [r.plain for r in rows.band_rows(a_caps(), footer.State(plan=plan), 94)]
    at[0] = NOW + 7.0
    assert [r.plain for r in rows.band_rows(a_caps(), footer.State(plan=plan), 94)] == first


# --- what it costs on the glass --------------------------------------------------------------------

class Live:
    """A real line editor over a pipe, a Menu with its emissions captured, and a transcript bank."""

    def __init__(self, pipe, monkeypatch) -> None:
        self.caps = Caps(color="none", background="dark", unicode=True, interactive=True,
                         width=96, height=24, g=dict(GLYPHS_UNICODE))
        monkeypatch.setattr(self.caps, "sync_size", lambda: None)
        console = build_console(self.caps, file=io.StringIO())
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


@pytest.fixture
def live(monkeypatch):
    with create_pipe_input() as pipe:
        yield Live(pipe, monkeypatch)


@needs_posix_terminal
def test_the_moving_mark_costs_one_row_per_frame_and_never_two(live, monkeypatch):
    """What the animation costs, stated rather than avoided: ONE row rewritten per frame, and only the
    row the mark is on — never the whole band. This file used to assert the mark could not move at all
    between seconds, which is the trade that was reversed; the invariant that survives is that a frame
    rewrites one row and that an idle prompt is still untouched."""
    at = [NOW]
    monkeypatch.setattr(time, "monotonic", lambda: at[0])
    step = 1.0 / rows.BEAT_HZ
    live.app.work = a_job()
    live.sync()
    for n in range(1, len(rows.BEAT_FRAMES)):
        at[0] = NOW + (n + 0.5) * step
        moved = live.sync()
        assert len(AT.findall(moved)) == 1, f"frame {n} rewrote {len(AT.findall(moved))} rows, not one"
    at[0] = NOW + 1.5
    assert "1m 13s" in live.sync(), "the clock beside it stopped turning over"
