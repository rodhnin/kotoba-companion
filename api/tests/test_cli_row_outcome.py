"""The terminal row's status mark in the CLI: two findings, both measured live.

The row was read off `ok`, which answers whether the tool returned usable text. A command that
exits 2, one killed at its timeout, and an action the user refused all drew the same `✓` as a clean
run. The fold now reads the loop's `outcome` word instead, and an unplaceable word becomes `unknown`
rather than the nearest guess.

Two halves are pinned: the FOLD, driven over the real events channel; and the MARK — `─` is what an
action that did not happen gets, `?` is the ask glyph, both dim because red is reserved for something
that broke."""
from __future__ import annotations

import asyncio


from kotoba.cli import state
from kotoba.cli.events_bridge import OUTCOMES, EventBridge, Step
from kotoba.cli.render import rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.theme import GLYPHS_ASCII, GLYPHS_UNICODE
from kotoba.core import events

STATES = OUTCOMES + ("unknown",)


def caps_for(*, unicode: bool = True, color: str = "none", width: int = 76) -> Caps:
    return Caps(color=color, background="dark", unicode=unicode, interactive=False,
                width=width, g=dict(GLYPHS_UNICODE if unicode else GLYPHS_ASCII))


def folded(sid: str, *frames: dict) -> Step:
    """One row, folded by the real bridge off the real event channel."""
    async def go():
        queue = events.register(sid)
        bridge = EventBridge(queue)
        try:
            for frame in frames:
                await events.emit_task(sid, "step", **frame)
            bridge.settle()
        finally:
            events.unregister(sid, queue)
        return bridge.steps[frames[-1]["id"]]

    return asyncio.run(go())


def landed(sid: str, **done) -> Step:
    return folded(sid, {"phase": "start", "id": "c1", "step_kind": "shell", "action": "pytest -q"},
                  {"phase": "done", "id": "c1", **done})


def test_a_command_that_exited_non_zero_is_not_folded_as_done():
    """The first of the three ✓ measured live. `ok` stays True on purpose — the tool DID answer."""
    step = landed("row-1", ok=True, outcome="failed", result="exit=2\nstderr:\nno such file")
    assert step.state == "failed"


def test_a_command_killed_at_its_timeout_is_not_folded_as_done():
    step = landed("row-2", ok=True, outcome="failed",
                  result="exit=124\nstderr:\ntimed out after 10s and was killed")
    assert step.state == "failed"
    assert "timed" in step.result, "the words are shown, and never read for the mark"


def test_an_action_the_user_refused_is_neither_a_failure_nor_a_success():
    """Inline (the CLI's own path) a refusal comes back `ok=True` with her sentence in it, so this
    row read as finished; deferred it now arrives with the same word from core.deferred_exec."""
    step = landed("row-3", ok=True, outcome="refused",
                  result="I held off on that one — it looked risky and I didn't get the go-ahead.")
    assert step.state == "refused"


def test_a_command_that_worked_is_still_folded_as_done():
    assert landed("row-4", ok=True, outcome="ok", result="exit=0").state == "ok"


def test_an_ending_this_build_has_never_heard_of_is_never_folded_as_success():
    assert landed("row-5", ok=True, outcome="quarantined", result="?").state == "unknown"


def test_the_four_endings_a_frame_can_carry_without_an_outcome_still_fold():
    """A backend that predates `outcome` — and `core/loop`'s own `interrupted`, which travels as a flag
    beside the word — must keep folding exactly as before."""
    assert landed("row-6", ok=True, result="exit=0").state == "ok"
    assert landed("row-7", ok=False, result="the tool errored").state == "failed"
    assert landed("row-8", ok=False, interrupted=True, result="(interrupted)").state == "interrupted"
    assert landed("row-9", ok=True, pending=True, result="I asked you on screen.").state == "pending"


def test_a_deferred_row_that_finally_ran_loses_the_status_it_was_parked_with():
    step = folded("row-10",
                  {"phase": "start", "id": "c7", "step_kind": "shell", "action": "pip install requests"},
                  {"phase": "done", "id": "c7", "ok": True, "outcome": "pending", "pending": True,
                   "result": "I asked you on screen."},
                  {"phase": "done", "id": "c7", "ok": True, "outcome": "failed", "pending": False,
                   "result": "I ran `pip install requests` (exit code 1)."})
    assert step.state == "failed", "the real ending replaces the parked one, never merges with it"


def test_a_deferred_row_stopped_while_it_ran_lands_as_interrupted():
    """The fifth ending core.deferred_exec can send: the command was approved and RUNNING when the user
    said stop. It closes the parked row from outside the turn, with the same word and the same older flag
    a cut turn carries — so the fold must take it exactly as it takes core.loop's own."""
    step = folded("row-11",
                  {"phase": "start", "id": "c8", "step_kind": "shell", "action": "sleep 300"},
                  {"phase": "done", "id": "c8", "ok": True, "outcome": "pending", "pending": True,
                   "result": "I asked you on screen."},
                  {"phase": "done", "id": "c8", "ok": False, "outcome": "interrupted", "interrupted": True,
                   "pending": False, "result": "stopped mid-run"})
    assert step.state == "interrupted"


def test_every_ending_the_bridge_can_fold_has_a_mark_of_its_own():
    """The bar, and the CLI's answer to the web's exhaustive Record: a state the renderer cannot place
    must not borrow another's mark. `_mark`'s fallback is the question mark, so an ending invented after
    this file was written draws `?` — never the ✓ of a success, and never the ▸ of one still running."""
    caps = caps_for()
    marks = {s: rows.tool_row(caps, Step("1", "shell", "pytest -q", s), 70).plain[0] for s in STATES}
    assert len(set(marks.values())) == len(STATES), marks
    assert marks["ok"] == caps.g["ok"] and marks["failed"] == caps.g["fail"]
    assert rows.tool_row(caps, Step("1", "shell", "x", "a-word-from-2027"), 70).plain[0] == caps.g["ask"]


def test_neither_of_the_two_that_never_ran_is_drawn_as_a_fault():
    """Red is for something that broke. A refusal is the user's own decision and an unplaceable ending is
    nobody's, so both are dim — and neither borrows the mark of a success or of a failure."""
    caps = caps_for(color="truecolor", width=76)
    for state in ("refused", "unknown"):
        row = rows.tool_row(caps, Step("1", "shell", "rm -rf build", state, "she left it alone"), 76)
        assert row.plain[0] != caps.g["fail"] and row.plain[0] != caps.g["ok"]
        assert {row.plain[s.start:s.end]: s.style for s in row.spans}[row.plain[0]] == "chrome"
        assert "live" not in [s.style for s in row.spans], "nothing on this row is painted as an error"


def test_a_refused_row_still_says_what_it_was_and_why_it_did_not_happen():
    caps = caps_for()
    row = rows.tool_row(caps, Step("1", "code", "primes.py", "refused", "you said no"), 76).plain
    assert "CODE" in row and "primes.py" in row and "you said no" in row


def test_the_long_jobs_closing_bracket_falls_back_the_same_way_every_other_row_does():
    """`work_row` kept its own fallback: the RUNNING mark and the word `done`. So a job that ended in a
    way this build cannot place drew a job still going AND called it finished — both halves of the one
    thing the outcome vocabulary may never do. It reads the same two tables as every other row now."""
    caps = caps_for()
    job = state.Work(goal="tidy the repo", n=1)
    job.stopped = job.t0 + 12
    for ending in ("refused", "unknown", "a-word-from-2027", ""):
        job.state = ending
        row = rows.work_row(caps, job, 76).plain
        assert row[0] != caps.g["give"], f"{ending!r} drew a job still going"
        assert row[0] != caps.g["ok"], f"{ending!r} drew a clean finish"
        assert not row.rstrip().endswith("done"), f"{ending!r} was reported as done"
    job.state = "ok"
    assert rows.work_row(caps, job, 76).plain.rstrip().endswith("done")


def test_the_bracket_and_the_panel_say_the_same_word_about_the_same_ending():
    """`Roster` puts `state_word` in the dim column beside the row `work_row` draws. Two vocabularies
    for one ending is how a panel and the receipt under it start disagreeing."""
    caps = caps_for()
    job = state.Work(goal="tidy", n=1)
    job.stopped = job.t0 + 3
    for ending in ("ok", "failed", "interrupted", "refused", "unknown"):
        job.state = ending
        tail = rows.work_row(caps, job, 76).plain.rstrip().rsplit(" ", 1)[-1]
        assert tail == rows.state_word(ending), (ending, tail)


def test_the_ascii_ladder_reaches_the_two_new_marks_as_well():
    caps = caps_for(unicode=False)
    for state in STATES:
        row = rows.tool_row(caps, Step("1", "shell", "pytest -q", state), 70).plain
        assert [ch for ch in row if ord(ch) > 127] == []
