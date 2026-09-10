"""A work run opens the matching skill before it acts, and acts from there on.

Two rules conflicted: the first tool call must be the real action, never a preparation step, but
the skill must load first, carrying knowledge no run has without it. The fix is a name, not a
deletion -- the skill read is STEP ZERO, one call before acting, and BIAS TO ACTION governs
everything after it. Both reasons are real; these tests fail if either half is deleted.
Measured cost: one extra model call carrying the whole prompt plus skill body, ~24,000 more
characters before the first real action. The language note stays too -- research is the only
skill in the tree that mentions language, so other work runs would lose it if dropped.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import kotoba.core.loop as loop
import kotoba.core.work_runner as wr
from kotoba.core import skill_docs
from kotoba.core.tool_guidance import skills_prompt


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


def _call(name, args, cid):
    return [_Event("response.output_item.done",
                   item=_Item(type="function_call", name=name, call_id=cid,
                              arguments=json.dumps(args)))]


_TEXT = [_Event("response.output_text.delta", delta="Informe listo.")]


class _Client:
    """Records the developer messages of each call at the moment it is made — `input_items` is
    mutated in place across iterations, so a stored reference would show only the final state."""

    def __init__(self, turns):
        self.calls: list[list[str]] = []
        rest, outer = list(turns), self

        class _R:
            async def create(self, **kw):
                outer.calls.append([str(i.get("content") or i.get("output") or "")
                                    for i in kw["input"] if isinstance(i, dict)])
                return _Stream(rest.pop(0) if rest else _TEXT)
        self.responses = _R()


class _DB:
    async def insert_turn(self, *a, **k):
        pass


@pytest.fixture
def run_a_work_job(monkeypatch):
    async def _emit(session_id, kind, **data):
        pass
    monkeypatch.setattr(wr, "emit_task", _emit)

    def go(turns, goal="Investiga X y escribe un informe", request="Investígalo y hazme un informe"):
        client = _Client(turns)
        monkeypatch.setattr(loop, "get_client", lambda: client)
        asyncio.run(wr._run("step-zero", goal, _DB(), {}, None, request=request))
        return client
    return go


def test_the_two_rules_name_each_other(run_a_work_job):
    """Both halves reach the model, in the same developer message, each pointing at the other."""
    sub = wr._WORK_SUBSYSTEM
    assert "STEP ZERO" in sub, "the skill read lost its name and is a 'preparation' step again"
    assert "BIAS TO ACTION" in sub, "the rule that stops a run preparing forever was deleted"
    assert "from step zero onward" in sub, (
        "BIAS TO ACTION must be scoped to what follows step zero, or it forbids it again")
    assert "never a skill_view or memory_recall 'preparation' step" not in sub, (
        "the blanket prohibition is back — this is the collision, not a wording preference")


def test_the_skills_block_and_the_subsystem_agree(run_a_work_job):
    """The two developer messages arrive in the same request. Whatever one asks for, the other must
    not forbid."""
    client = run_a_work_job([_call("skill_view", {"name": "research"}, "s1"), _TEXT])
    first = "\n".join(client.calls[0])

    assert "STEP ZERO" in first
    assert "research" in first, "the skills list never reached the job that is supposed to scan it"
    assert skills_prompt([{"name": "research", "description": "d"}]).count("STEP ZERO") == 1


def test_a_work_run_can_open_the_skill_before_it_acts(run_a_work_job):
    """The mechanism, end to end: the model's step zero is a skill_view, and what comes back is the
    real skill body — including the rule whose absence produced an English report."""
    client = run_a_work_job([_call("skill_view", {"name": "research"}, "s1"),
                             _call("write_file", {"path": "research/x.md", "content": "# X"}, "a1"),
                             _TEXT])

    second = "\n".join(client.calls[1])
    assert "write the ENTIRE report" in second, (
        "the skill body never reached the conversation — step zero bought nothing")
    assert len(client.calls) == 3, "the run did not act after reading the skill"


def test_step_zero_costs_exactly_one_call(run_a_work_job):
    """The accepted price, pinned so it cannot quietly grow: the first real action happens on the
    second model call instead of the first, and no more than that."""
    without = run_a_work_job([_call("write_file", {"path": "a.md", "content": "x"}, "a1"), _TEXT])
    with_zero = run_a_work_job([_call("skill_view", {"name": "research"}, "s1"),
                                _call("write_file", {"path": "a.md", "content": "x"}, "a1"), _TEXT])

    assert len(without.calls) == 2 and len(with_zero.calls) == 3


def test_the_language_note_survives_because_no_skill_covers_it(run_a_work_job):
    """`_LANGUAGE_NOTE` looks redundant once the skill is really read, and is not: research.md is the
    ONLY skill that says anything about language, so a build/browse/file job has nothing to inherit."""
    with_a_rule = [s["name"] for s in skill_docs.list_skills()
                   if "language" in (skill_docs.view_skill(s["name"]) or "").lower()]
    assert with_a_rule == ["research"], (
        f"the set of skills carrying a language rule moved: {with_a_rule} — re-decide the note")

    client = run_a_work_job([_call("write_file", {"path": "a.md", "content": "x"}, "a1")],
                            goal="Construye la página", request="Hazme una página de aterrizaje")
    user_msg = [m for m in client.calls[0] if "verbatim" in m]
    assert user_msg and "the LANGUAGE comes from" in user_msg[0], (
        "a non-research work run lost the only thing telling it which language to deliver in")
