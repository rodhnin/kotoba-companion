"""A call the loop refuses must not read — to her or to the log — as a call that ran.

Live QA asked for three reminders to be deleted in one turn. `cronjob` was used four times
(one `list`, three `remove`s) against a per-tool cap of 3, so the third removal was swallowed by the guard
at `_run_iterations`: no execution, no DB write, no step row, no `toolresult`. The only trace was a
`toolcall` line logged BEFORE the guard, which is indistinguishable from a log line lost in transit — and
the feedback the guard handed the model ended "if the task is done, answer the user now in your own words",
so she reported all three deleted. Two had been.
"""
from __future__ import annotations

import re

from kotoba.core import loop as loop_mod

_SRC = (loop_mod.__file__ or "")
_TEXT = open(_SRC, encoding="utf-8").read()


def _cap_message() -> str:
    """The output handed back when the per-tool cap refuses a call."""
    i = _TEXT.index("per_tool[tc.name] > tool_cap")
    return _TEXT[i:i + 1200]


def test_the_cap_says_the_call_did_not_run():
    """The wording handed back to the model has to state plainly that nothing happened."""
    msg = _cap_message()
    assert "DID NOT RUN" in msg, "the refusal must state plainly that nothing happened"


def test_the_cap_never_invites_a_completion_claim():
    """The old wording ended '…or, if the task is done, answer the user now in your own words' — an
    instruction to declare success, delivered immediately after silently discarding the call."""
    msg = _cap_message()
    assert "if the task is done" not in msg
    flat = re.sub(r'["\s]+', " ", msg)
    assert "do NOT tell the user this one is done" in flat, msg


def test_both_silent_guards_log_that_they_skipped():
    """`toolcall` is logged for every call including the swallowed ones; without these a skip cannot be
    told apart from a lost line."""
    assert _TEXT.count('"toolskip %s') == 2, "each guard that `continue`s must log its own skip"
    i = _TEXT.index("if attempts[sig] > 1:")
    j = _TEXT.index("per_tool[tc.name] > tool_cap")
    assert "toolskip" in _TEXT[i:i + 400], "the exact-repeat guard logs nothing"
    assert "toolskip" in _TEXT[j:j + 400], "the per-tool cap logs nothing"


def test_cronjob_gets_the_workflow_cap_not_the_bare_one():
    """One `list` plus N `remove`s is the ordinary shape; at a cap of 3 a three-item cleanup loses its
    last item, which is the exact live failure."""
    assert loop_mod._TODO_TOOL_LIMIT > loop_mod._PER_TOOL_LIMIT
    i = _TEXT.index("tool_cap = ")
    assert '("todo", "cronjob")' in _TEXT[i:i + 400], "cronjob must share todo's workflow cap"
    assert loop_mod._TODO_TOOL_LIMIT >= 4, "a list + three removes needs at least 4"
