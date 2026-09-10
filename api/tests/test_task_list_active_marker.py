"""A bad index must not destroy the plan's state, and must not be reported as success.

`update()` cleared the previous active marker before resolving the new target, so an out-of-range or
already-settled `active` demoted the running step and set nothing: on screen the panel dropped from
"working" back to idle mid-plan, and `todo` still returned "Plan open: … Next: step 2." A model that
counts from 0 hit this on its first call.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from kotoba.core import task_list as tl


@pytest.fixture
def plan():
    tl.clear("s")
    tl.open_list("s", [{"text": "uno"}, {"text": "dos"}, {"text": "tres"}], "plan")
    tl.update("s", done=[1], active=2)
    yield
    tl.clear("s")


def _statuses():
    return [(t["order"], t["status"]) for t in tl.get("s")["tasks"]]


def test_an_already_done_step_cannot_steal_the_active_marker(plan):
    before, rev = _statuses(), tl.get("s")["rev"]
    tl.update("s", active=1)
    assert _statuses() == before, "the running step must stay active"
    assert tl.get("s")["rev"] == rev, "nothing changed, so no revision bump"
    assert tl.get("s")["ignored"] == ["active=1"]


def test_an_out_of_range_active_changes_nothing(plan):
    before = _statuses()
    tl.update("s", active=99)
    assert _statuses() == before
    assert tl.get("s")["ignored"] == ["active=99"]


def test_zero_based_indices_are_reported_not_swallowed(plan):
    """Steps are 1-based. A model counting from 0 must be told, or it believes the step is done."""
    tl.update("s", done=[0, 1, 2])
    assert tl.get("s")["ignored"] == ["done=0"]
    assert _statuses() == [(1, "done"), (2, "done"), (3, "pending")]


def test_re_asserting_the_current_active_is_a_clean_no_op(plan):
    rev = tl.get("s")["rev"]
    tl.update("s", active=2)
    assert _statuses() == [(1, "done"), (2, "active"), (3, "pending")]
    assert tl.get("s")["rev"] == rev
    assert tl.get("s")["ignored"] == []


def test_moving_the_active_marker_forward_still_works(plan):
    tl.update("s", done=[2], active=3)
    assert _statuses() == [(1, "done"), (2, "done"), (3, "active")]
    assert tl.get("s")["ignored"] == []


def test_the_panel_frame_never_carries_the_internal_ignored_list(plan):
    tl.update("s", active=99)
    assert "ignored" not in tl.frame("s")


def test_the_tool_says_what_it_ignored(plan):
    """A success line for a call that applied nothing is the lie this whole list exists to prevent."""
    import kotoba.tools.builtin.todo as todo

    async def go():
        ctx = types.SimpleNamespace(session_id="s", mode="companion", announce_turn=False)
        return await todo.execute({"active": 99}, ctx)

    out = asyncio.run(go())
    assert "Ignored" in out and "active=99" in out
