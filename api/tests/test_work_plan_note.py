"""She was told to tick her own plan exactly once, and never if she made it inside the run.

The plan note was injected once, before the loop (or the first model call) started, so a plan
opened before the job began was described only at iteration 0, and one opened mid-run was never
described at all -- live, a plan sat at 0/2 with step 1 "active" while both steps were really done.

The note now belongs to the thing that iterates, in every mode, and nothing else. These tests read
the input list AS SENT on each call: there is never more than ONE note (the refresh MOVES it, never
adds), it is the last thing she reads before acting, and a plan born mid-run shows on the next call.
"""
from __future__ import annotations

import asyncio
import json
import types

import kotoba.core.loop as loop
from kotoba.core import task_list as tl
from kotoba.tools import ToolContext

_MARK = "TASK LIST"


def _done(item):
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _tool(call_id, args, name="todo"):
    return _done(types.SimpleNamespace(type="function_call", name=name,
                                       arguments=json.dumps(args), call_id=call_id))


def _spoke():
    return _done(types.SimpleNamespace(type="message", content=[]))


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _Client:
    """Snapshots the input list as it went out. The note's COUNT and POSITION are per-call facts, and
    the list is mutated in place all run long, so reading it at the end answers neither question."""

    def __init__(self, turns):
        self.turns, self._i = turns, 0
        self.sent: list[list] = []
        self.responses = self

    async def create(self, **kw):
        self.sent.append(list(kw["input"]))
        evs = self.turns[min(self._i, len(self.turns) - 1)]
        self._i += 1
        return _Stream(evs)


class _DB:
    async def insert_audit_log(self, **kw):
        pass


def _notes(sent: list) -> list[str]:
    return [str(i.get("content", "")) for i in sent
            if isinstance(i, dict) and _MARK in str(i.get("content", ""))]


def _last_is_note(sent: list) -> bool:
    return bool(sent) and isinstance(sent[-1], dict) and _MARK in str(sent[-1].get("content", ""))


def _drive(sid, calls, monkeypatch, on_call=None, mode="work", sub=None):
    """Run `calls` tool-calling iterations then one that just talks. `on_call(n)` fires while the tool
    of iteration n runs — that is where she opens or ticks her plan from inside the job.

    Each call carries different arguments on purpose: the loop answers a REPEATED call from its own
    anti-loop note without running the tool, and an iteration that never executes never gets to move
    the plan."""
    client = _Client([[_tool(f"c{i}", {"title": f"t{i}"})] for i in range(calls)] + [[_spoke()]])

    async def fake_exec(name, args, queue, patterns, ctx, timeout=0):
        if on_call is not None:
            on_call(len(client.sent))
        return True, "ok"

    monkeypatch.setattr(loop, "execute_with_heartbeat", fake_exec)
    ctx = ToolContext(db=_DB(), session_id=sid, client=None, mode=mode, subagent_id=sub)
    ctx.approval = None
    asyncio.run(loop._run_iterations(
        client, ctx, [{"role": "user", "content": "do the job"}], asyncio.Queue(), {},
        max_iterations=calls + 1, mode=mode,
        allow_risk={"read", "write", "exec", "network"}, toolset_filter=None,
    ))
    return client.sent


def test_no_plan_no_note(monkeypatch):
    sid = "plan-none"
    tl.clear(sid)
    for sent in _drive(sid, 2, monkeypatch):
        assert _notes(sent) == []


def test_a_plan_opened_INSIDE_the_job_reaches_her(monkeypatch):
    """The half that was simply missing: work_runner read task_list before the job existed, so a plan
    she made once it was running was never described to her at all."""
    sid = "plan-inside"
    tl.clear(sid)
    sent = _drive(sid, 3, monkeypatch,
                  on_call=lambda n: tl.open_list(sid, ["find the numbers", "write it up"], "latency")
                  if n == 2 else None)
    assert _notes(sent[0]) == [] and _notes(sent[1]) == []
    assert len(_notes(sent[2])) == 1
    assert "latency" in _notes(sent[2])[0] and "find the numbers" in _notes(sent[2])[0]
    tl.clear(sid)


def test_the_note_follows_the_plan_and_is_never_duplicated(monkeypatch):
    """One note per call, always: refreshing removes the old one before appending the new."""
    sid = "plan-ticks"
    tl.clear(sid)
    tl.open_list(sid, ["one", "two"], "the plan")

    def tick(n):
        if n == 1:
            tl.update(sid, done=[1])

    sent = _drive(sid, 2, monkeypatch, on_call=tick)
    for call in sent:
        assert len(_notes(call)) == 1
    assert "0/2 done" in _notes(sent[0])[0]
    assert "1/2 done" in _notes(sent[1])[0]
    assert "step 1" not in _notes(sent[1])[0]
    tl.clear(sid)


def test_closing_the_plan_takes_the_note_away(monkeypatch):
    sid = "plan-close"
    tl.clear(sid)
    tl.open_list(sid, ["one"], "the plan")

    def close(n):
        if n == 1:
            tl.update(sid, done=[1], close=True)

    sent = _drive(sid, 2, monkeypatch, on_call=close)
    assert len(_notes(sent[0])) == 1
    assert _notes(sent[1]) == [] and _notes(sent[2]) == []
    tl.clear(sid)


def test_a_plan_that_never_moves_is_put_back_in_front_of_her(monkeypatch):
    """The other half. A note appended at iteration 0 is still in the context at iteration 30 — buried
    under thirty tool results, which is how a plan silently never gets ticked. It is re-placed at the
    tail every _PLAN_NOTE_EVERY quiet iterations, and re-placed is not re-added: still one copy."""
    sid = "plan-quiet"
    tl.clear(sid)
    tl.open_list(sid, ["one", "two"], "the plan")
    monkeypatch.setattr(loop, "_PLAN_NOTE_EVERY", 2)

    sent = _drive(sid, 4, monkeypatch)
    for call in sent:
        assert len(_notes(call)) == 1
    assert [_last_is_note(c) for c in sent] == [True, False, True, False, True]
    assert len({_notes(c)[0] for c in sent}) == 1
    tl.clear(sid)


def test_a_tick_resets_the_quiet_stretch(monkeypatch):
    sid = "plan-reset"
    tl.clear(sid)
    tl.open_list(sid, ["one", "two", "three"], "the plan")
    monkeypatch.setattr(loop, "_PLAN_NOTE_EVERY", 3)

    def tick(n):
        if n == 2:
            tl.update(sid, done=[1])

    sent = _drive(sid, 3, monkeypatch, on_call=tick)
    assert [_last_is_note(c) for c in sent] == [True, False, True, False]
    tl.clear(sid)


def test_a_companion_turn_carries_the_note_exactly_once(monkeypatch):
    """A live turn iterates too, so it gets the same treatment — and exactly one copy of it.

    This test used to assert the opposite (that a companion turn was never touched, because
    core/context owned the note there). That ownership is what live QA measured as broken: context builds
    the note before the first model call, so the turn that opens the plan is the one turn that never
    hears about it again. core/context no longer injects it, which is what keeps "once" true here."""
    sid = "plan-companion"
    tl.clear(sid)
    tl.open_list(sid, ["one"], "the plan")
    for sent in _drive(sid, 2, monkeypatch, mode="companion"):
        assert len(_notes(sent)) == 1
    tl.clear(sid)


def test_a_plan_opened_INSIDE_a_companion_turn_reaches_her(monkeypatch):
    """The finding's exact shape: she opens the plan at iteration 1, then does the work. Before the fix the
    note arrived on the NEXT turn — by which time the work was over and the note said "keep working
    it" — and the panel sat at 0/2 with every step really done."""
    sid = "plan-companion-inside"
    tl.clear(sid)
    sent = _drive(sid, 3, monkeypatch, mode="companion",
                  on_call=lambda n: tl.open_list(sid, ["check the weather", "draft the report"],
                                                 "Lisbon weather report") if n == 1 else None)
    assert _notes(sent[0]) == []
    assert len(_notes(sent[1])) == 1 and _last_is_note(sent[1])
    assert "Lisbon weather report" in _notes(sent[1])[0]
    assert "check the weather" in _notes(sent[1])[0]
    tl.clear(sid)


def test_the_note_names_the_state_that_actually_happens(monkeypatch):
    """"Keep working it, tick steps done as you go" describes a habit. The state that occurs is a step
    that is already finished and still shows pending, and the note has to ask for THAT — in one call,
    not one call per step."""
    sid = "plan-wording"
    tl.clear(sid)
    tl.open_list(sid, ["one", "two"], "the plan")
    note = tl.prompt_note(sid)
    assert "ALREADY" in note and "mark it NOW" in note
    assert "ONE call" in note
    assert "Never mark a step you have not done" in note, "the nag must not buy a false progress bar"
    assert "never resend `steps`" in note, "the note is what pushes extra todo calls — they must be safe"
    tl.clear(sid)


def test_core_context_no_longer_injects_the_plan(monkeypatch):
    """The other half of "exactly once": two owners meant two notes. core/context keeps the background
    work / reminder / deferred notes and hands the plan to the loop."""
    from kotoba.core.context import inject_work_note

    sid = "plan-context"
    tl.clear(sid)
    tl.open_list(sid, ["one"], "the plan")
    items = [{"role": "developer", "content": "SYS"}, {"role": "user", "content": "hola"}]
    assert inject_work_note(items, sid) == items
    tl.clear(sid)


def test_a_helper_is_not_told_to_tick_its_parents_plan(monkeypatch):
    """A subagent shares the session and so would read the same list, but the plan is not its work and
    its toolset may not even contain todo."""
    sid = "plan-helper"
    tl.clear(sid)
    tl.open_list(sid, ["one"], "the plan")
    for sent in _drive(sid, 2, monkeypatch, sub="h1"):
        assert _notes(sent) == []
    tl.clear(sid)


def test_revision_moves_exactly_when_the_note_does():
    """A re-mark of an already-settled step applies nothing, so it restates nothing; and a REPLACED
    list is a different list_id rather than the next rev, which is what the note has to say too."""
    sid = "plan-rev"
    tl.clear(sid)
    assert tl.revision(sid) is None
    tl.open_list(sid, ["one", "two"], "the plan")
    first = tl.revision(sid)
    assert first is not None
    assert tl.revision(sid) == first
    tl.update(sid, done=[1])
    ticked = tl.revision(sid)
    assert ticked != first
    tl.update(sid, done=[1])
    assert tl.revision(sid) == ticked
    tl.open_list(sid, ["other"], "a different plan")
    assert tl.revision(sid)[0] != first[0]
    tl.update(sid, close=True)
    assert tl.revision(sid) is None
    tl.clear(sid)
