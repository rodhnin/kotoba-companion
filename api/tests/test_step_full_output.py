"""What a tool actually returned, on the wire.

Asked four times how many files were in a folder, she answered 23, then 14+9, then 92, then 25 — the
tool output was byte-perfect every time, so the fixable half was not hers: a 443-character listing
arrived as one 18-character line; a 2887-character count arrived as 13; a 1527-character listing as 29.

`_result_text` truncating a generic tool to one line (four for a shell) is not a bug — the card is a
compact status row. What was missing is the rest ever leaving the process: these pin a second `full`
copy on the step frame, and pin the preview beside it unchanged, since widening it would undo the reason
it exists. They drive the real agentic loop over a fake model, with every other surface production."""
from __future__ import annotations

import asyncio
import json

from conftest import shell_that
from kotoba.core import events, interaction, sandbox, workspace
from kotoba.core.loop import _MAX_TOOL_OUTPUT_CHARS, _output_cap, _result_text
from kotoba.tools.registry import ToolSpec, deregister, register

# The folder as `filesystem__list_directory` really returned it: 9 folders + 14 files = the 23 things
# the question was about, and the one answer she got right.
LISTING = "\n".join(
    [f"[DIR] {d}" for d in ("benchmarks", "reports", "screenshots", "notes", "drafts", "tmp",
                            "visual-memory", "exports", "archive-2026")]
    + [f"[FILE] {f}" for f in ("kotoba.log", "todo.md", "latency-numbers.csv", "budget-2026.xlsx",
                               "readme.md", "plan-fase-3.md", "soul-notes.md", "session-4213.json",
                               "avatar-sketch.png", "garden_night.glb", "invoice-july.pdf",
                               "prices.csv", "meeting-notes.md", "scratch-pad.txt")]
)

# 169 lines of 16 characters: `exit=0\nstdout:\n` + that is 2887 characters, the shell measurement.
WIDE_COMMAND = shell_that("many_wide_lines")


class _Item:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Event:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for event in self._evs:
                yield event
        return gen()


class _Client:
    def __init__(self, turns):
        rest = list(turns)
        self.responses = type("R", (), {"create": lambda _s, **kw: _made(_Stream(rest.pop(0)))})()


def _made(value):
    async def _c():
        return value
    return _c()


class _DB:
    async def list_approved_commands(self):
        return []

    async def insert_audit_log(self, **kw):
        return None


_SAID = [_Event("response.output_text.delta", delta="[happily] Ahí lo tienes.")]


def _call(name: str, **args):
    return [_Event("response.output_item.done",
                   item=_Item(type="function_call", name=name, call_id="c1",
                              arguments=json.dumps(args)))]


def _frames(monkeypatch, tmp_path, sid: str, turns, *, approved: bool = True) -> list[dict]:
    import kotoba.core.loop as loop

    async def _answer(*a, **k):
        return (approved, False)

    monkeypatch.setattr(sandbox, "backend_name", lambda: "local")
    monkeypatch.setattr(workspace, "resolve_workdir", lambda _sid: tmp_path)
    monkeypatch.setattr(interaction, "request_approval", _answer)
    monkeypatch.setattr(loop, "get_client", lambda: _Client(turns))

    async def go():
        queue = events.register(sid)
        try:
            await loop.agentic_loop([{"role": "user", "content": "cuántas cosas hay"}], sid, _DB(),
                                    asyncio.Queue(), {}, mode="work", channel="text")
        finally:
            events.unregister(sid, queue)
        return [queue.get_nowait() for _ in range(queue.qsize())]

    return asyncio.run(go())


def _landed(frames: list[dict]) -> dict:
    done = [f for f in frames if f.get("kind") == "step" and f.get("phase") == "done"]
    assert len(done) == 1, done
    return done[0]


def _stub(name: str, returns, *, toolset: str = "mcp:filesystem", asks: bool = False):
    """An action tool that answers with `returns`, registered the way an MCP server's tools are."""
    class _Module:
        @staticmethod
        async def execute(args, ctx):
            if asks:
                verdict = await interaction.ask_approval(ctx, "install the thing")
                if verdict != interaction.APPROVED:
                    return "I held off on that one — you didn't give me the go-ahead."
            return returns

    register(ToolSpec(name=name, module=_Module, schema={"name": name}, toolset=toolset,
                      risk="network", built_in=False, check=lambda: True))
    return name


# --- the listing that could not be counted ----------------------------------------------------------

def test_a_23_line_listing_reaches_the_panel_whole(monkeypatch, tmp_path):
    """The measurement itself. Every MCP tool falls in `_result_text`'s generic branch, which keeps the
    FIRST LINE — so the one reading that would have settled "how many things are in there" reached the
    screen as `[DIR] benchmarks` and nothing else."""
    name = _stub("filesystem__list_directory", LISTING)
    try:
        frame = _landed(_frames(monkeypatch, tmp_path, "full-listing", [_call(name, path="."), _SAID]))
    finally:
        deregister(name)

    assert len(LISTING.splitlines()) == 23 and len(LISTING) == 443
    assert len(frame["result"]) == 18 and len(frame["result"].splitlines()) == 1
    assert frame["full"] == LISTING
    assert len(frame["full"].splitlines()) == 23, "the panel gets every entry, or you cannot count them"


def test_the_listings_preview_is_byte_for_byte_what_it_always_was(monkeypatch, tmp_path):
    """The trim is load-bearing — a compact status row in a narrow panel — so the second copy must not
    widen the first one by a character."""
    name = _stub("filesystem__list_directory", LISTING)
    try:
        frame = _landed(_frames(monkeypatch, tmp_path, "full-preview", [_call(name, path="."), _SAID]))
    finally:
        deregister(name)

    assert frame["result"] == _result_text(name, True, LISTING) == frame["text"]


# --- the shell output that was thirteen characters --------------------------------------------------

def test_a_shell_result_longer_than_four_lines_reaches_the_panel_whole(monkeypatch, tmp_path):
    """`_result_text` keeps four lines of a shell result and 160 characters of each. The command below
    leaves 2887, which is the size of the `find … | wc -l` output that reached the panel as 13."""
    frames = _frames(monkeypatch, tmp_path, "full-shell", [_call("shell", command=WIDE_COMMAND), _SAID])
    frame = _landed(frames)

    # Counted with the line endings normalised: the number is the MEASUREMENT that arrived as 13, and
    # PowerShell hands back CRLF, which would make the same output 3055 characters and the same point.
    assert len(frame["full"].replace("\r\n", "\n")) == 2887, frame["full"][:80]
    assert len(frame["full"].splitlines()) == 171, "exit=, stdout: and 169 lines of output"
    assert frame["full"].startswith("exit=0\nstdout:\n")
    assert frame["full"].rstrip().endswith("0000000000000169"), "the LAST line is there, not just the head"
    assert len(frame["result"].splitlines()) == 4, "the preview stays four lines"


def test_a_command_that_exited_non_zero_carries_the_stderr_the_preview_cut(monkeypatch, tmp_path):
    """It matters MORE on a failure, and for a reason the preview cannot help: four lines are taken off
    the HEAD, and the line that says what went wrong is at the tail."""
    cmd = shell_that("many_wide_lines_then_fails")
    frame = _landed(_frames(monkeypatch, tmp_path, "full-fail", [_call("shell", command=cmd), _SAID]))

    assert frame["outcome"] == "failed"
    assert "cannot stat the last one" not in frame["result"], "the reason is not in the four lines"
    assert "cannot stat the last one" in frame["full"]
    assert "0000000000000169" in frame["full"]


def test_a_bang_prefixed_failure_carries_the_rest_of_its_explanation(monkeypatch, tmp_path):
    """The other failure shape: a tool that refused its own arguments comes back ok=False, so the row is
    `  ! ` and ONE line — of an explanation written to be read."""
    import kotoba.core.loop as loop

    said = ("I can't install that one.\n"
            "The server asks for a token I don't have.\n"
            "Give me one in Settings and ask me again.")

    class _Module:
        @staticmethod
        async def execute(args, ctx):
            loop.note_tool_refusal(ctx)
            return said

    register(ToolSpec(name="filesystem__mount", module=_Module, schema={"name": "filesystem__mount"},
                      toolset="mcp:filesystem", risk="network", built_in=False, check=lambda: True))
    try:
        frame = _landed(_frames(monkeypatch, tmp_path, "full-bang",
                                [_call("filesystem__mount", path="."), _SAID]))
    finally:
        deregister("filesystem__mount")

    assert frame["result"] == "  ! I can't install that one."
    assert frame["full"] == said


# --- where it stops, and saying so ------------------------------------------------------------------

def test_the_panel_copy_stops_exactly_where_the_model_copy_stops(monkeypatch, tmp_path):
    """The cap is the model's own, per tool. Below it and there would be output she acted on that you
    cannot read — this same defect one level down; above it and the panel would show what she never saw."""
    from kotoba.core.loop import _full_result

    name = _stub("filesystem__list_directory", "x" * (_MAX_TOOL_OUTPUT_CHARS + 5_000))
    try:
        frame = _landed(_frames(monkeypatch, tmp_path, "full-cap", [_call(name, path="."), _SAID]))
    finally:
        deregister(name)

    assert _output_cap(name) == _MAX_TOOL_OUTPUT_CHARS
    assert frame["full"].count("x") == _MAX_TOOL_OUTPUT_CHARS
    clipped = _full_result("y" * 10, cap=4)
    assert clipped.startswith("yyyy") and "6 more characters" in clipped, \
        "the second truncation happened in silence"


def test_a_clipped_panel_copy_says_it_was_clipped(monkeypatch, tmp_path):
    """A silent second truncation would recreate the whole defect one level down, so the text says where
    the panel stopped and how much it is not showing."""
    from kotoba.core.loop import _full_result

    clipped = _full_result("z" * (_MAX_TOOL_OUTPUT_CHARS + 1_234))
    assert "1234" in clipped.replace(",", "")
    assert clipped.rstrip().endswith("]") and "\n" in clipped[-200:]
    assert _full_result("z" * 40) == "z" * 40, "an output under the cap gains nothing"


# --- the rows that have no output at all ------------------------------------------------------------

def test_a_row_that_never_ran_carries_no_output(monkeypatch, tmp_path):
    """`_refusal_line` keeps its precedence, and its rows carry no second copy: nothing ran, and a "show
    the whole output" button over the gate's own sentence invites reading it as output."""
    name = _stub("filesystem__install", "unused", asks=True)
    try:
        frame = _landed(_frames(monkeypatch, tmp_path, "full-refused",
                                [_call(name, path="."), _SAID], approved=False))
    finally:
        deregister(name)

    assert frame["outcome"] == "refused"
    assert frame["result"] == "  you said no — it never ran"
    assert frame["full"] == ""


# --- the deferred row, which is the one a shell approval lands on -------------------------------------

def test_a_deferred_command_carries_its_whole_output(monkeypatch, tmp_path):
    """The voice path cannot hold a turn open on a card, so the row is completed later by
    core.deferred_exec — which cut the result to 600 characters with nothing saying so. That row is the
    one an APPROVED shell command lands on, which is the commonest long output on the panel.

    What arrives here is the deferred runner's own sentence, and IT caps the command's stdout at 2000
    characters — that cap is what the model and the user are told, so it is not
    a display decision and this changes nothing about it. `full` is everything that reached us."""
    import kotoba.core.loop as loop
    from kotoba.core import deferred_exec, transport

    async def _answer(*a, **k):
        return (True, False)

    monkeypatch.setattr(sandbox, "backend_name", lambda: "local")
    monkeypatch.setattr(workspace, "resolve_workdir", lambda _sid: tmp_path)
    monkeypatch.setattr(deferred_exec, "request_approval", _answer)
    monkeypatch.setattr(loop, "get_client",
                        lambda: _Client([_call("shell", command=WIDE_COMMAND), _SAID]))
    sid = "full-deferred"

    async def go():
        queue = events.register(sid)
        try:
            # Deferral needs the transport an ElevenLabs agent turn carries, not just the channel.
            with transport.el_call_turn():
                await loop.agentic_loop([{"role": "user", "content": "hazlo"}], sid, _DB(),
                                        asyncio.Queue(), {}, mode="work", channel="voice")
            await asyncio.gather(*list(deferred_exec._tasks.get(sid, ())))
        finally:
            events.unregister(sid, queue)
            deferred_exec.forget_session(sid)
        return [queue.get_nowait() for _ in range(queue.qsize())]

    done = [f for f in asyncio.run(go()) if f.get("kind") == "step" and f.get("phase") == "done"]
    landed = [f for f in done if not f.get("pending")]
    assert len(landed) == 1, done

    assert len(landed[0]["result"]) == 600, "the preview there is unchanged"
    assert len(landed[0]["full"]) > 2000
    assert landed[0]["full"].startswith("I ran `")
    assert "0000000000000100" not in landed[0]["result"], "the 600-char cut lands before line 100"
    assert "0000000000000100" in landed[0]["full"]


# --- the terminal, which has its own scrollback -------------------------------------------------------

def test_the_cli_bridge_keeps_the_whole_output():
    """`/last` promises "print the last tool result in full" and could not: `Tool.full` was fed the same
    four lines the row already showed. The bridge carries both now — the row keeps the trimmed line."""
    from kotoba.cli.events_bridge import EventBridge

    bridge = EventBridge(asyncio.Queue())
    bridge.handle({"kind": "step", "phase": "start", "id": "c1", "step_kind": "shell", "action": "$ ls"})
    bridge.handle({"kind": "step", "phase": "done", "id": "c1", "outcome": "ok",
                   "result": "  exit=0", "full": LISTING})

    step = bridge.steps["c1"]
    assert step.result == "  exit=0"
    assert step.full == LISTING


def test_a_backend_that_sends_no_full_still_renders():
    """The field is additive: an older producer sends the frame it always sent."""
    from kotoba.cli.events_bridge import EventBridge

    bridge = EventBridge(asyncio.Queue())
    bridge.handle({"kind": "step", "phase": "done", "id": "c1", "ok": True, "result": "  done"})

    assert bridge.steps["c1"].full == ""


def _cli_app():
    import io
    from types import SimpleNamespace

    from kotoba.cli.app import App
    from kotoba.cli.render.caps import Caps
    from kotoba.cli.render.portrait import Portrait
    from kotoba.cli.render.screen import Screen
    from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

    caps = Caps(color="none", background="dark", unicode=True, interactive=False,
                width=96, g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1", run_id="turn0001",
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app, buf


def test_the_terminal_keeps_its_row_short_and_its_scrollback_whole():
    """What the CLI chose. The row it commits is the trimmed line, because a terminal that dumped 171
    lines under every command would bury her reply — and the whole output goes where a terminal already
    puts long things: `/last`, which prints into scrollback, and the tool leaf under `^r`."""
    import time

    from kotoba.cli.events_bridge import Step

    app, buf = _cli_app()
    app.turn_start = time.monotonic()
    for phase, frame in (("start", {}), ("done", {"ok": True, "full": LISTING})):
        app.session.events.steps["t1"] = Step("t1", "mcp", "list_directory",
                                              "running" if phase == "start" else "ok",
                                              result="  [DIR] benchmarks", full=frame.get("full", ""))
        app._event("step", {"id": "t1", "phase": phase, "run_id": "turn0001", **frame})

    assert "benchmarks" in buf.getvalue()
    assert "scratch-pad.txt" not in buf.getvalue(), "the committed row stays one line"
    assert app.last_result == LISTING, "/last says 'in full' and now can be"
    assert app.tools[-1].full == LISTING, "and so can the tool leaf under ^r"
    assert app.tools[-1].detail == "  [DIR] benchmarks", "the row's own phrase is untouched"
