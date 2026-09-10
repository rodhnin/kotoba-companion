"""The panel surface: what `^r` opens over the transcript, and the four things it may not cost.

Bytes are read at the surface's own write, since bytes-per-second is what a pty measures. The clock
is frozen wherever two builds of the same row are compared, but deliberately NOT frozen in the cost
tests: a row that ends in a clock costs bytes because real time passed between renders, and freezing
it there would hide the defect those tests exist to catch.

A panel shrinking is a write, not a rate — the frame that loses rows carries their restore, the one
after it does not — so the cost tests settle the surface first, then measure."""
from __future__ import annotations

import io
import re
import time
from types import SimpleNamespace

import pytest
from conftest import needs_posix_terminal
from prompt_toolkit.document import Document
from prompt_toolkit.filters import to_filter
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kotoba.cli import settings_view, slash, state
from kotoba.cli.app import App
from kotoba.cli.input import menu as panels
from kotoba.cli.input.menu import Picker, Roster
from kotoba.cli.input.prompt import Prompt, SlashCompleter
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.text import duration
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

NOW = 10_000.0
TAIL = ["she wrote the file out", "› and then you asked her something", "one more transcript row"]
ALT_SCREEN = ("\x1b[?1049", "\x1b[?47", "\x1b[?1047")
DECSTBM = re.compile(r"\x1b\[[0-9]*(;[0-9]*)?r")
SETTINGS_DATA = {
    "security": {"approvals": [], "trust": "ask"}, "browser_cdp": "",
    "personality": {}, "mcp_servers": [], "pending_mcp": [], "skills": [],
    "memory": {"topics": []}, "reminders": [], "toolsets": [], "keys": [], "plugins": [],
}


def _bones(line: str) -> str:
    """A row with its alignment collapsed: what it says, in order, without the padding that puts the
    tail on the right edge. Two rows built at the same width differ in that padding alone."""
    return " ".join(line.split())


def a_caps() -> Caps:
    return Caps(color="none", background="dark", unicode=True, interactive=False, width=96,
                g=dict(GLYPHS_UNICODE))


def plain() -> tuple[App, io.StringIO]:
    """The app with no line editor behind it: everything `_event` folds, drawn into a buffer."""
    caps, buf = a_caps(), io.StringIO()
    console = build_console(caps, file=buf)
    console.width = caps.width
    screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=Prompt(caps))
    app.session = SimpleNamespace(session_id="s1",
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app, buf


def a_job() -> state.Work:
    job = state.Work("get the real latency numbers", 1, t0=NOW - 72.0)
    job.tools = [state.Tool("shell", "python measure.py", started=NOW - 40.0),
                 state.Tool("file", "research/latency.md", started=NOW - 20.0, stopped=NOW - 18.0,
                            state="ok", detail="2.1 kB", full="wrote 2.1 kB")]
    job.helpers = [state.Helper("h1", "research", "read both pages", state="running",
                                started=NOW - 30.0,
                                steps=[("opened the first one", None), ("read it", True),
                                       ("opened the second", None)]),
                   state.Helper("h2", "web", "find the issue", state="running", started=NOW - 10.0,
                                steps=[("searched", True)])]
    return job


class Wired:
    """One App with a real line editor behind it, and the three answers only a live terminal gives: the
    window size, where the frame's top border is, and how wide the console it prints through is."""

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
        self.box.validate_while_typing = to_filter(False)
        self.written: list[str] = []
        monkeypatch.setattr(panels, "_emit", self.written.append)
        self.screen.kept(TAIL)

    @property
    def box(self):
        return self.prompt.session.default_buffer

    def typed(self, text: str) -> None:
        self.box.text = text
        self.box.cursor_position = len(text)

    def sync(self) -> str:
        before = len(self.written)
        self.app.menu.sync()
        return "".join(self.written[before:])

    def press_ctrl_r(self) -> str:
        self.app._line_up(self.box)
        return self.sync()

    def press_esc(self) -> str:
        before = len(self.written)
        if self.prompt.owns_list():
            if not self.prompt.picker.back(self.box):
                self.prompt.picker = None
        self.box.complete_state = None
        self.app.menu.sync()
        return "".join(self.written[before:])

    def press_enter(self) -> None:
        self.prompt.picked = self.prompt.picker.enter(self.box)
        self.sync()
        self.sync()

    def shown(self) -> list[str]:
        return [c.display_text.rstrip() for c in self.box.complete_state.completions]

    def metas(self) -> list[str]:
        return [c.display_meta_text for c in self.box.complete_state.completions]

    def resting_cost(self, frames: int = 12) -> int:
        spent = 0
        for _ in range(frames):
            time.sleep(0.02)
            spent += len(self.sync())
        return spent


@pytest.fixture
def wired(monkeypatch):
    with create_pipe_input() as pipe:
        yield Wired(pipe, monkeypatch)


# --- batch 0: the two one-line fixes --------------------------------------------------------------

def test_the_line_up_is_off_the_list_before_a_single_row_of_it_is_printed(monkeypatch):
    """Every `_commit` reprints the region under it and `Parts.roster_rows` reads the same list, so a
    line-up emptied after the loop is on the glass twice for the length of the print."""
    caps = a_caps()
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=Prompt(caps))
    app.helpers = [state.Helper("h1", "research", "one", state="ok"),
                   state.Helper("h2", "web", "two", state="ok")]
    seen: list[int] = []
    real = screen.row
    monkeypatch.setattr(screen, "row", lambda r: (seen.append(len(app.helpers)), real(r))[1])
    app._commit_roster()
    assert seen == [0, 0]
    assert [h.sid for h in app.last_helpers] == ["h1", "h2"] and app.helpers == []


def test_a_landed_step_keeps_the_whole_result_the_row_could_not_hold():
    from types import SimpleNamespace

    from kotoba.cli.events_bridge import Step

    caps = a_caps()
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=Prompt(caps))
    step = Step("s1", "shell", "ls -la", state="ok", result="total 8\ndrwxr-xr-x 2 you\nfile.py")
    app.session = SimpleNamespace(session_id="s",
                                  events=SimpleNamespace(steps={"s1": step}, settle=lambda: None))
    app.turn_start = time.monotonic()
    app._event("step", {"kind": "step", "id": "s1", "phase": "end"})
    tool = app.tools[-1]
    assert tool.full == step.result and "\n" in tool.full
    assert "\n" not in rows.tool_text(caps, tool, 94).plain


# --- batch 1: the surface -------------------------------------------------------------------------

@needs_posix_terminal
def test_ctrl_r_paints_one_saved_block_over_the_transcript_and_repeats_write_nothing(wired):
    wired.app.work = a_job()
    opened = wired.press_ctrl_r()
    assert opened.startswith("\x1b7") and opened.endswith("\x1b8")
    assert opened.count("\x1b7") == 1 and opened.count("\x1b8") == 1
    assert "get the real latency numbers" in opened and "WORK" in opened
    assert wired.resting_cost() == 0


@needs_posix_terminal
def test_esc_puts_back_the_transcript_row_that_was_under_it_byte_for_byte(wired):
    wired.app.work = a_job()
    wired.press_ctrl_r()
    back = wired.press_esc()
    assert wired.prompt.picker is None
    assert f"\x1b[21;1H\x1b[0m{wired.screen.ansi(TAIL[-1])}\x1b[0m\x1b[K" in back
    assert f"\x1b[20;1H\x1b[0m{wired.screen.ansi(TAIL[-2])}\x1b[0m\x1b[K" in back


@needs_posix_terminal
def test_the_panel_borrows_rows_it_gives_back_and_asks_for_no_terminal_mode_at_all(wired):
    """No alternate screen, no scroll region. Every move is an absolute one inside a save/restore, none
    of them reaches below the frame's top border, and the app's own height never changes — so nothing
    scrolls and nothing has to come back."""
    wired.app.work = a_job()
    payload = wired.press_ctrl_r() + wired.press_esc()
    assert not any(mode in payload for mode in ALT_SCREEN)
    assert DECSTBM.search(payload) is None
    touched = {int(m) - 1 for m in re.findall(r"\x1b\[([0-9]+);1H", payload)}
    assert touched and max(touched) <= 20


@needs_posix_terminal
def test_a_panel_that_gives_way_hands_the_list_to_a_slash_and_takes_it_back(wired):
    wired.app.work = a_job()
    wired.press_ctrl_r()
    wired.typed("/hel")
    assert wired.prompt.picker is not None and not wired.prompt.owns_list()
    assert wired.sync() != "" and wired.box.complete_state is None
    offered = SlashCompleter(prompt=wired.prompt).get_completions(Document("/hel", 4), None)
    assert [c.text for c in offered] == ["/help", "/helpers"]
    wired.typed("")
    assert wired.prompt.owns_list()
    assert list(SlashCompleter(prompt=wired.prompt).get_completions(Document("", 0), None)) == []
    assert wired.sync() != "" and wired.shown()[0].endswith("get the real latency numbers")


@needs_posix_terminal
def test_a_launch_menu_never_gives_way_so_a_path_typed_into_it_stays_a_value(wired):
    """`--settings` uses the one box as the editor for a value, and `base_url`, `SOUL_PATH` and every
    path under them start with the two characters that would otherwise open a list over that row."""
    picker = Picker(wired.app, "settings")
    wired.prompt.picker = picker
    for text in ("/home/me/soul.md", "@thing", ""):
        assert not picker.cedes(Document(text, len(text)))
    wired.typed("/home/me/soul.md")
    assert wired.prompt.owns_list()
    completer = SlashCompleter(prompt=wired.prompt)
    assert list(completer.get_completions(Document("/home/me", 8), None)) == []


@needs_posix_terminal
def test_the_settings_menu_still_walks_its_levels_and_esc_comes_back_out(wired):
    """The class underneath `--settings` changed; what it does may not."""
    picker = Picker(wired.app, "settings")
    picker.data = SETTINGS_DATA
    wired.prompt.picker = picker
    assert picker.level == "settings" and picker.trail() == "settings"
    assert [r[0] for r in picker.rows("")] == [name for name, _ in settings_view.SECTIONS]
    picker.sync(wired.box)
    picker.index = 1
    section = picker.ids[1]
    assert picker.enter(wired.box) == ()
    assert picker.level == "keys" and picker.arg == section and wired.box.text == ""
    assert picker.trail() == f"settings · {section}"
    assert picker.back(wired.box) is True
    assert picker.level == "settings" and picker.index == 1
    assert picker.back(wired.box) is False


@needs_posix_terminal
def test_a_filtered_launch_menu_still_narrows_on_what_you_type(wired):
    picker = Picker(wired.app, "settings")
    picker.data = SETTINGS_DATA
    assert [r[0] for r in picker.rows("brain")] == ["BRAIN"]
    assert picker.rows("zzz")[0][1] == "no match"


@needs_posix_terminal
def test_a_list_with_nothing_in_it_says_so_instead_of_blaming_a_filter_nobody_typed(wired):
    """A fresh install has no conversations and no servers, and the miss line accused the person of a
    filter they had not typed — `nothing here says ''`, with nothing to backspace. Three branches and
    three sentences: an empty source speaks in the words of its own subject, a filter that missed keeps
    the line it earned, and a filter that hits still narrows."""
    empty = Picker(wired.app, "sessions")
    assert empty.sessions == []
    assert empty.rows("") == [("", "no conversations",
                               "this is your first one \u2014 esc to leave and talk to her")]
    assert empty.rows("zzz") == empty.rows("")

    bare = Picker(wired.app, "settings")
    bare.data = SETTINGS_DATA
    bare.stack.append(("keys", "MCP"))
    assert bare.rows("") == [("", "no servers yet", "nothing of yours is connected to her")]
    bare.stack[-1] = ("keys", "REMINDERS")
    assert bare.rows("")[0][1] == "no reminders"

    had = Picker(wired.app, "sessions")
    had.sessions = [{"id": "s1", "started_at": "2026-01-02 03:04:05", "turns": 3,
                     "opened": "about the garden"}]
    assert had.rows("zzz") == [("", "no match", "nothing here says 'zzz' \u2014 backspace to widen "
                                "it, esc to leave it")]
    assert [r[0] for r in had.rows("garden")] == ["s1"]


# --- batch 2: the roster, and the byte invariant --------------------------------------------------

def test_no_row_the_panel_draws_carries_a_clock(monkeypatch):
    """The whole design rests on this: a payload that cannot change is a payload `Menu` never writes."""
    caps, job, at = a_caps(), a_job(), [NOW]
    monkeypatch.setattr(time, "monotonic", lambda: at[0])

    def drawn(clock: bool) -> list[str]:
        return [rows.work_row(caps, job, 80, opening=True, clock=clock).plain,
                rows.tool_text(caps, job.tools[0], 80, still=True, clock=clock).plain,
                rows.helper_text(caps, job.helpers[0], 1, 80, still=True, clock=clock).plain]

    still, ticking = drawn(False), drawn(True)
    at[0] = NOW + 47.0
    assert drawn(False) == still
    assert drawn(True) != ticking


def test_the_clock_is_the_only_thing_a_panel_row_gives_up(monkeypatch):
    caps, job = a_caps(), a_job()
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    job.state, job.stopped = "ok", NOW
    assert "1m 12s" in rows.work_row(caps, job, 80).plain
    assert "1m 12s" not in rows.work_row(caps, job, 80, clock=False).plain
    assert " 2.0s" in rows.tool_text(caps, job.tools[1], 80, still=True).plain
    bare = rows.tool_text(caps, job.tools[1], 80, still=True, clock=False).plain
    assert " 2.0s" not in bare and "research/latency.md" in bare and "2.1 kB" in bare
    assert "30.0s" in rows.helper_text(caps, job.helpers[0], 1, 80, still=True).plain
    bare = rows.helper_text(caps, job.helpers[0], 1, 80, still=True, clock=False).plain
    assert "30.0s" not in bare and "read both pages" in bare and "3 steps" in bare
    assert rows.state_word("ok") == "done" and rows.state_word("running") == "running"


@needs_posix_terminal
def test_the_panel_lists_the_job_every_tool_running_or_landed_and_every_helper(wired):
    wired.app.work = a_job()
    wired.press_ctrl_r()
    said = wired.shown()
    assert len(said) == 5
    assert "WORK" in said[0] and said[0].endswith("get the real latency numbers")
    assert "BASH" in said[1] and said[1].endswith("python measure.py")
    assert "FILE" in said[2] and said[2].endswith("research/latency.md · 2.1 kB")
    assert "research" in said[3] and said[3].endswith("3 steps")
    assert "web" in said[4] and said[4].endswith("1 step")
    assert wired.metas() == ["running", "running", "done", "running", "running"]


@needs_posix_terminal
def test_a_helper_that_finishes_greys_in_its_own_place_without_shoving_the_others(wired):
    wired.app.work = a_job()
    wired.press_ctrl_r()
    before = wired.shown()
    wired.app.work.helpers[0].state = "ok"
    wired.app.work.helpers[0].stopped = NOW
    wired.sync()
    after = wired.shown()
    assert len(after) == len(before) and after[4] == before[4]
    assert wired.metas()[3] == "done" and wired.metas()[4] == "running"


@needs_posix_terminal
def test_an_open_panel_over_a_running_job_costs_nothing_per_frame(wired):
    """Measured at the prompt through a pty: the job alone is 169 B/s and the job with a list open over
    it is 198. Those extra bytes are the bar's, not the list's: the list writes when a row changes,
    never else."""
    wired.app.work = a_job()
    assert wired.press_ctrl_r() != ""
    assert wired.resting_cost(24) == 0
    wired.app.work.helpers[1].steps.append(("found it", True))
    moved = wired.sync()
    assert "\x1b7" in moved and "2 steps" in moved
    assert wired.resting_cost() == 0


@needs_posix_terminal
def test_the_panel_lets_go_of_the_prompt_the_frame_the_job_lands(wired):
    wired.app.work = a_job()
    wired.press_ctrl_r()
    assert isinstance(wired.prompt.picker, Roster)
    wired.app.work.state, wired.app.work.landed = "ok", True
    wired.sync()
    assert wired.prompt.picker is None and wired.box.complete_state is None


@needs_posix_terminal
def test_ctrl_r_with_nothing_running_opens_nothing_and_a_second_press_closes_it(wired):
    assert wired.press_ctrl_r() == "" and wired.prompt.picker is None
    wired.app.work = a_job()
    assert wired.press_ctrl_r() != "" and isinstance(wired.prompt.picker, Roster)
    closed = wired.press_ctrl_r()
    assert wired.prompt.picker is None and "\x1b7" in closed


@needs_posix_terminal
def test_a_panel_over_a_message_you_are_halfway_through_never_takes_the_message(wired):
    wired.app.work = a_job()
    wired.typed("half a sentence")
    wired.press_ctrl_r()
    assert wired.shown()[0].endswith("get the real latency numbers")
    wired.press_esc()
    assert wired.box.text == "half a sentence"


# --- batch 3: the leaf ----------------------------------------------------------------------------

@needs_posix_terminal
def test_enter_on_a_helper_shows_every_step_it_took_and_not_the_three_of_the_peek(wired):
    job = a_job()
    job.helpers[0].steps = [(f"step {n}", n % 2 == 0) for n in range(1, 8)]
    job.helpers[0].summary = "both pages said the same thing"
    wired.app.work = job
    wired.press_ctrl_r()
    wired.prompt.picker.index = 3
    wired.press_enter()
    said = wired.shown()
    assert wired.prompt.picker.level == "helper" and wired.prompt.picker.trail() == "running · 1"
    assert "read both pages" in said[0]
    marks = [wired.caps.g["ok"] if n % 2 == 0 else wired.caps.g["fail"] for n in range(1, 8)]
    assert [s.strip() for s in said[1:8]] == [f"{m} step {n}" for n, m in enumerate(marks, 1)]
    assert said[-1].strip() == "both pages said the same thing"


@needs_posix_terminal
def test_the_job_leaf_draws_the_rows_slash_work_prints_off_the_same_builders(monkeypatch, wired):
    """The receipt and the panel are the same three functions, called in the same order with the same
    arguments. The clock is the one field the panel drops, and it is the last thing on every row."""
    monkeypatch.setattr(time, "monotonic", lambda: NOW)
    job = a_job()
    wired.app.work, wired.app.works = job, [job]
    rw = wired.screen.rw
    slash._work(wired.app, "")
    printed = [ln for ln in wired.buf.getvalue().splitlines() if ln.strip()]
    receipt = [rows.work_row(wired.caps, job, rw, opening=True,
                             word=duration(job.elapsed)).plain.rstrip(),
               *[rows.tool_text(wired.caps, t, rw, still=True).plain.rstrip() for t in job.tools],
               *[rows.helper_text(wired.caps, h, i, rw, still=True).plain.rstrip()
                 for i, h in enumerate(job.helpers, 1)]]
    assert printed[:len(receipt)] == receipt

    monkeypatch.setattr(Roster, "wide", lambda self, caps: rw)
    wired.press_ctrl_r()
    wired.prompt.picker.index = 0
    wired.press_enter()
    assert wired.prompt.picker.level == "job"
    bare = [rows.work_row(wired.caps, job, rw, opening=True, clock=False).plain.rstrip(),
            *[rows.tool_text(wired.caps, t, rw, still=True, clock=False).plain.rstrip()
              for t in job.tools],
            *[rows.helper_text(wired.caps, h, i, rw, still=True, clock=False).plain.rstrip()
              for i, h in enumerate(job.helpers, 1)]]
    assert wired.shown() == bare
    assert len(bare) == len(receipt)
    clocks = [duration(job.elapsed), *[rows._clock(t.elapsed).strip() for t in job.tools],
              *[rows._clock(h.elapsed).strip() for h in job.helpers]]
    assert [_bones(line) for line in receipt] == [
        f"{_bones(said)} {clock}".strip() for said, clock in zip(bare, clocks)]


@needs_posix_terminal
def test_a_shell_leaf_says_what_was_kept_and_that_nothing_else_was(wired):
    job = a_job()
    job.tools[0].state, job.tools[0].stopped = "ok", NOW
    job.tools[0].full = "total 8\ndrwxr-xr-x  2 you you\n\nmeasure.py\nlatency.md"
    job.tools[0].detail = "exit 0"
    wired.app.work = job
    wired.press_ctrl_r()
    wired.prompt.picker.index = 1
    wired.press_enter()
    said = wired.shown()
    assert "python measure.py" in said[0]
    assert [s.strip() for s in said[1:5]] == ["total 8", "drwxr-xr-x  2 you you", "measure.py",
                                              "latency.md"]
    assert said[-1].strip().startswith("what she's thinking isn't kept")


@needs_posix_terminal
def test_esc_out_of_a_leaf_puts_you_back_on_the_row_you_opened(wired):
    wired.app.work = a_job()
    wired.press_ctrl_r()
    wired.prompt.picker.index = 4
    wired.press_enter()
    assert wired.prompt.picker.level == "helper" and wired.prompt.picker.arg == "2"
    wired.press_esc()
    assert wired.prompt.picker is not None
    assert wired.prompt.picker.level == "running" and wired.prompt.picker.index == 4
    wired.press_esc()
    assert wired.prompt.picker is None


# --- the columns the wire now fills, read at this end ----------------------------------------------

def test_a_read_only_tool_finally_says_something_instead_of_looking_like_a_hang():
    """`what do you know about me` fires five reads, none of which earns a `step` frame. Before `peek`
    the bar said nothing at all for the length of them."""
    app, _ = plain()
    app.turn_start = time.monotonic()
    app.status = "thinking"
    app._event("peek", {"kind": "peek", "tool": "memory_recall"})
    assert app.peek == "memory_recall"
    assert footer.bar_kind(app._state()) == "peek"
    assert "recalling…" in footer.bar_text(app.caps, app.screen.face, app._state()).plain
    app._event("peek", {"kind": "peek", "tool": ""})
    assert app.peek == "" and footer.bar_kind(app._state()) == "think"


def test_a_read_inside_the_long_job_never_takes_the_bar_off_the_job():
    """Out of turn the bar is the job's one surface, and `bar_kind` reads `peek` ahead of everything out
    there — so a read in the job would cost it its phrase, its pulse and its clock."""
    app, _ = plain()
    app.work = a_job()
    app._event("peek", {"kind": "peek", "tool": "memory_recall"})
    assert app.peek == ""
    assert footer.bar_kind(app._state()) == "long"
    assert "long job" in footer.out_twins(app._state(), "long")[0]


def test_a_helper_wears_the_toolset_it_was_actually_given():
    app, _ = plain()
    app.turn_start = time.monotonic()
    app._event("subagent_spawned", {"kind": "subagent_spawned", "id": "h1",
                                    "goal": "read both pages", "toolset": "research"})
    app._event("subagent_spawned", {"kind": "subagent_spawned", "id": "h2", "goal": "find it"})
    assert [h.role for h in app.helpers] == ["research", "helper"]
    drawn = rows.helper_text(app.caps, app.helpers[0], 1, 80, still=True).plain
    assert "research" in drawn


def test_a_helpers_step_keeps_whether_it_worked_and_the_row_shows_it():
    app, caps = plain()[0], a_caps()
    app.turn_start = time.monotonic()
    app._event("subagent_spawned", {"kind": "subagent_spawned", "id": "h1", "goal": "g",
                                    "toolset": "web"})
    for text, ok in (("searching", None), ("found seven", True), ("that page is gone", False)):
        app._event("subagent_step", {"kind": "subagent_step", "id": "h1", "text": text, "ok": ok})
    assert app.helpers[0].steps == [("searching", None), ("found seven", True),
                                    ("that page is gone", False)]
    drawn = [rows.step_line(caps, s, 80) for s in app.helpers[0].steps]
    assert drawn[0].strip().startswith(caps.g["bullet"])
    assert drawn[1].strip().startswith(caps.g["ok"])
    assert drawn[2].strip().startswith(caps.g["fail"])


def test_the_job_takes_its_goal_off_the_wire_and_not_out_of_the_process():
    app, _ = plain()
    app._event("work_started", {"kind": "work_started", "goal": "get the real latency numbers"})
    assert app.work is not None and app.work.goal == "get the real latency numbers"
    assert "get the real latency numbers" in rows.work_row(
        app.caps, app.work, 80, opening=True, clock=False).plain


def test_a_reminder_reaches_the_bar_lands_as_a_row_and_invents_no_time():
    app, buf = plain()
    frame = {"kind": "reminder", "id": "r7", "message": "the standup is in five minutes"}
    app._event("reminder", frame)
    app._event("reminder", dict(frame))
    assert [d.rid for d in app.due] == ["r7"]
    st = app._state()
    assert footer.bar_kind(st) == "due"
    assert footer.out_twins(st, "due")[0].endswith("the standup is in five minutes")
    assert footer.out_right(app.caps, st, "due") == ""
    assert app.due[0].when == "" and app.due[0].every == ""
    assert app._land_due() is True
    out = buf.getvalue()
    assert "DUE" in out and "the standup is in five minutes" in out and "/open" not in out
    assert app.due[0].told is True and footer.bar_kind(app._state()) == "idle"
    assert app._land_due() is False


def test_a_reminder_outranks_the_long_job_on_the_bar_and_gives_it_back():
    app, _ = plain()
    app.work = a_job()
    assert footer.bar_kind(app._state()) == "long"
    app._event("reminder", {"kind": "reminder", "id": "r1", "message": "call her back"})
    assert footer.bar_kind(app._state()) == "due"
    app._land_due()
    assert footer.bar_kind(app._state()) == "long"


@needs_posix_terminal
def test_every_leaf_is_as_clockless_as_the_list_it_was_opened_from(wired):
    wired.app.work = a_job()
    wired.press_ctrl_r()
    for row in range(5):
        wired.prompt.picker.index = row
        wired.press_enter()
        assert wired.resting_cost(6) == 0, wired.prompt.picker.level
        wired.press_esc()
