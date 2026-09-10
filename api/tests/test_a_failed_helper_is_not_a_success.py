"""A helper that crashed is not a helper that came back — the "ran and failed" witness.

`delegate` catches the helper's exception and hands the parent a non-empty failure string, so
`execute_with_heartbeat` graded it ok=True: green terminal row, `executed` audit row, duplicate
guard marked done, even the spoken COMPLETE line — all over a crash.

`note_tool_refusal` is the wrong witness (refused = NOTHING ran, no audit row, no reason), so
`note_tool_failure` is a THIRD state: it ran and may have left traces, and the parent needs the
reason to redo the subtask. Scoped rather than keyed on `call_id`, because parallel delegates share
one context with no per-call id and a call_id key would paint one helper's crash onto the other."""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.core.loop as loop
import kotoba.tools.action.delegate as delegate
from kotoba.core import events
from kotoba.core.loop import (
    _after_line, _nothing_ran, _step_outcome, _voice_for, note_tool_failure, note_tool_refusal,
    record_tool_failures,
)
from kotoba.tools import ToolContext


def _done(item):
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _text(chunk):
    return types.SimpleNamespace(type="response.output_text.delta", delta=chunk)


def _msg(chunk):
    return [_text(chunk), _done(types.SimpleNamespace(type="message", content=[]))]


def _call(call_id, args):
    return _done(types.SimpleNamespace(type="function_call", name="delegate",
                                       arguments=json.dumps(args), call_id=call_id))


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _Parent:
    """The model driving the turn: it calls delegate, then writes its closing line."""

    def __init__(self, turns):
        self.turns, self._i = turns, 0
        self.responses = self

    async def create(self, **kw):
        evs = self.turns[min(self._i, len(self.turns) - 1)]
        self._i += 1
        return _Stream(evs)


class _Helper:
    """The model every subagent reaches through llm.get_client. A goal naming CRASH gets a real
    exception out of the provider — the crash `_run_iterations` raises for the parent to survive."""

    def __init__(self):
        self.responses = self

    async def create(self, **kw):
        if "CRASH" in json.dumps(kw.get("input"), default=str):
            raise RuntimeError("upstream 500 from the provider")
        return _Stream(_msg("Found three sources and picked the newest."))


class _DB:
    def __init__(self):
        self.audit = []

    async def insert_audit_log(self, **kw):
        self.audit.append(kw)


@pytest.fixture
def spoken(monkeypatch):
    """A non-reasoning model, so the canned before/after lines are really emitted — the surface the
    defect was loudest on, and the one a reasoning-model configuration leaves dormant."""
    monkeypatch.setattr(loop, "is_reasoning_model", lambda *a, **k: False)
    monkeypatch.setattr("kotoba.core.llm.get_client", lambda *a, **k: _Helper())


def _turn(sid: str, goals: list[str]):
    """Drive the REAL loop over one turn that delegates `goals`, and collect every surface it wrote."""
    turns = [[_call(f"c{i}", {"goal": g, "toolset": "research"}) for i, g in enumerate(goals)],
             _msg("Here's what I have.")]
    db = _DB()

    async def go():
        q = events.register(sid)
        queue: asyncio.Queue = asyncio.Queue()
        ctx = ToolContext(db=db, session_id=sid, client=None, mode="work")
        ctx.approval = None
        try:
            await loop._run_iterations(
                _Parent(turns), ctx, [{"role": "user", "content": "split this up"}], queue, {},
                max_iterations=len(turns), mode="work",
                allow_risk={"read", "write", "exec", "network"}, toolset_filter=None,
            )
            frames = [q.get_nowait() for _ in range(q.qsize())]
            said = [x for x in [queue.get_nowait() for _ in range(queue.qsize())] if isinstance(x, str)]
            return frames, said
        finally:
            events.unregister(sid, q)

    frames, said = asyncio.run(go())
    steps = [f for f in frames if f.get("kind") == "step" and f.get("phase") == "done"]
    return db.audit, steps, said, frames


# --- the witness itself -------------------------------------------------------------------------------

def test_ran_and_failed_is_not_nothing_ran():
    """The whole reason for a third state. Both answers are "this was not a success"; only one of them
    also says the work never happened, and that difference is the audit row."""
    ctx = ToolContext(db=None, session_id="w", mode="work")
    ctx.call_id = "c1"
    with record_tool_failures() as failed:
        note_tool_failure("RuntimeError: boom")
        assert failed == ["RuntimeError: boom"]
        assert _nothing_ran(ctx, "c1") is False, "a failure that ran must never be read as a refusal"
    assert _step_outcome(ctx, "c1", False, False, []) == "failed"

    note_tool_refusal(ctx)
    assert _nothing_ran(ctx, "c1") is True
    assert _step_outcome(ctx, "c1", False, False, []) == "refused", \
        "the two states must not collapse into each other"


def test_a_witness_with_no_block_open_is_a_no_op():
    """Same contract as core.sandbox.base.note_exit: a tool may report a failure from a detached task
    long after the loop stopped listening, and that must not raise."""
    note_tool_failure("nobody is collecting this")


# --- the four surfaces --------------------------------------------------------------------------------

def test_the_crashed_helper_fails_on_every_surface(spoken):
    audit, steps, said, frames = _turn("helper-crash", ["CRASH the provider"])

    assert [s["outcome"] for s in steps] == ["failed"], "the terminal row drew a ✓ over a crash"
    assert steps[0]["ok"] is False
    assert audit and audit[0]["detail"] == "executed:failed", \
        f"the trail said the helper executed cleanly: {audit}"
    assert "upstream 500" in steps[0]["full"], "the row must keep the reason it failed"

    joined = " ".join(said)
    assert delegate.FAIL in joined, "she owes the user the fail line, not silence"
    assert delegate.COMPLETE not in joined, "she narrated COMPLETE over a helper that never came back"
    assert delegate.EXPRESSIONS["fail"] in [f["emotion"] for f in frames if f.get("type") == "emotion"], \
        "a stumble has to show mid-turn, before her closing sentence implies anything"


def test_the_model_is_told_what_broke_and_not_to_repeat_it(spoken):
    """Returning None would have graded it right and thrown away the reason. The parent has to be able
    to do the subtask itself, which means reading why the helper could not."""
    async def go():
        ctx = ToolContext(db=None, session_id="helper-told", mode="work")
        ctx.call_id = "c1"
        return await loop.execute_with_heartbeat(
            "delegate", {"goal": "CRASH it", "toolset": "research"}, asyncio.Queue(), {}, ctx)

    ok, result = asyncio.run(go())
    assert ok is False
    assert "helper failed" in str(result) and "upstream 500" in str(result)


def test_a_helper_that_really_came_back_is_untouched(spoken):
    """The success path keeps every one of its endings — including emitting NO face of its own, because
    the finished turn's face rides the audio tag she speaks."""
    audit, steps, said, frames = _turn("helper-ok", ["find the sources"])

    assert [s["outcome"] for s in steps] == ["ok"] and steps[0]["ok"] is True
    assert audit and audit[0]["detail"] == "executed"
    assert delegate.COMPLETE in " ".join(said)
    faces = [f["emotion"] for f in frames if f.get("type") == "emotion"]
    assert delegate.EXPRESSIONS["done"] not in faces, \
        f"a per-tool success face was added, and it would fight the audio tag: {faces}"
    assert faces == ["determined", delegate.EXPRESSIONS["focus"], "neutral"], \
        f"the only faces are the working chip, the tool's focus, and the closing tag's: {faces}"


def test_the_spoken_line_is_chosen_by_the_same_word_the_row_is():
    """One witness, two surfaces: `_after_line` reads the outcome, so a row that says × cannot be
    narrated as a success."""
    patt = _voice_for("delegate", {})
    assert _after_line(patt, "failed") == delegate.FAIL
    assert _after_line(patt, "ok") == delegate.COMPLETE
    assert _after_line(patt, "refused") == ""


# --- why the witness is scoped and not keyed on the call ----------------------------------------------

def test_one_helper_failing_does_not_condemn_the_other(spoken):
    """The parallel path shares ONE context between concurrently running delegates and sets no
    per-call `call_id`, so a witness keyed like `note_tool_refusal` would mark both. Each call gets its
    own box instead."""
    audit, steps, _, _ = _turn("helper-both", ["CRASH the provider", "find the sources"])

    by_id = {s["id"]: s["outcome"] for s in steps}
    assert sorted(by_id.values()) == ["failed", "ok"], f"the two helpers shared one verdict: {by_id}"
    assert sorted(a["detail"] for a in audit) == ["executed", "executed:failed"]
