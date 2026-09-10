"""On an announce turn she reports finished work — she must not be sent round in circles.

`steps` is stripped on an announce turn so reporting a finished job can't re-derive it into a new plan.
The strip only bailed out when NO list existed: with a closed list it fell through to "No plan open yet.
Pass steps=[...] to start one.", she complied, and the strip hit again — the same exchange until the
per-tool cap stopped it. Each round is a real API call.

Her step notes are hers: the schema promises the user does not read them, so they must not ride out
over SSE either.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from kotoba.core import task_list as tl
import kotoba.tools.builtin.todo as todo


def _ctx(announce: bool):
    return types.SimpleNamespace(session_id="ann", mode="companion", announce_turn=announce)


def _run(args, announce=True):
    return asyncio.run(todo.execute(args, _ctx(announce)))


@pytest.fixture(autouse=True)
def clean():
    tl.clear("ann")
    yield
    tl.clear("ann")


def test_a_closed_list_does_not_ask_her_for_steps_she_is_not_allowed_to_give():
    tl.open_list("ann", [{"text": "uno"}], "plan")
    tl.update("ann", done=[1], close=True)
    assert tl.get("ann")["status"] == "done"

    out = _run({"steps": [{"text": "otra vez"}], "title": "replan"})
    assert "Pass steps" not in out, "this is the loop: she is told to do the thing that was just stripped"
    assert "work is done" in out


def test_no_list_at_all_behaves_the_same():
    out = _run({"steps": [{"text": "algo"}]})
    assert "Pass steps" not in out
    assert "work is done" in out


def test_an_open_list_can_still_be_ticked_and_closed_on_an_announce_turn():
    """The strip must not disarm the legitimate ending — that is the whole point of allowing the call."""
    tl.open_list("ann", [{"text": "uno"}, {"text": "dos"}], "plan")
    out = _run({"done": [1, 2], "close": True})
    assert tl.get("ann")["status"] == "done"
    assert "2/2" in out


def test_a_normal_turn_may_still_open_a_plan():
    out = _run({"steps": [{"text": "uno"}, {"text": "dos"}], "title": "plan"}, announce=False)
    assert tl.get("ann")["status"] == "open"
    assert "Plan open" in out


# --- her private notes stay private ----------------------------------------------------------------

def test_step_notes_never_leave_the_backend():
    tl.open_list("ann", [{"text": "buscar", "detail": "empezar por el registro oficial"}], "plan")
    frame = tl.frame("ann")
    assert all("detail" not in t for t in frame["tasks"]), "her notes must not ride out over SSE"


def test_she_still_gets_her_own_notes_back():
    """Stripping the frame must not take the notes away from HER — that is what they are for."""
    tl.open_list("ann", [{"text": "buscar", "detail": "empezar por el registro oficial"}], "plan")
    tl.update("ann", active=1)
    assert "registro oficial" in tl.prompt_note("ann")
