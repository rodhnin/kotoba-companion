"""One live-QA trace, pinned end to end.

A request carried a link in the sentence, asking her to browse to it and read back the headline.
`start_work` got a goal describing the message, not the URL, so the job started on nothing
executable, asked `clarify` for a URL already given, and got no screen: the question came back as
the tool result and was printed as the final answer, closing the run AS a finished summary.

Three assertions: the URL survives the handoff, a work-mode question opens a real card, and a job
that touched nothing is not announced as a finished result.
"""
from __future__ import annotations

import asyncio
import types

import pytest

import kotoba.core.work_runner as wr
import kotoba.core.work_state as ws
import kotoba.tools.builtin.clarify as clarify
import kotoba.tools.builtin.start_work as sw
from kotoba.core import events, interaction
from kotoba.tools import ToolContext

MESSAGE = ("Usa tu propio navegador de verdad: entra navegando en https://news.ycombinator.com y dime "
           "el titular que salga el primero de la lista. Nada de buscarlo, quiero que lo abras tú.")
GOAL = ("Open Hacker News in the browser, navigate to the page the user provided, and report the first "
        "story title at the top of the list.")


class _DB:
    async def insert_turn(self, *a, **k): pass
    async def ensure_session(self, *a, **k): pass


def setup_function():
    ws._state.clear(); ws._tasks.clear(); events.event_queues.clear()


def test_the_url_survives_a_goal_that_dropped_it(monkeypatch):
    seen = {}

    def fake_start(session_id, goal, db, soul_patterns, mcp, request="", **kw):
        seen["goal"], seen["request"] = goal, request
        ws.start(session_id, goal)

    monkeypatch.setattr(sw.work_runner, "start", fake_start)
    ctx = ToolContext(db=_DB(), session_id="s1", mode="companion")
    ctx.user_text = MESSAGE
    asyncio.run(sw.execute({"goal": GOAL}, ctx))

    assert "https://news.ycombinator.com" not in seen["goal"], "the goal is exactly as the model wrote it"
    assert "https://news.ycombinator.com" in seen["request"], "the datum reaches the runner anyway"


def test_the_job_runs_on_a_message_that_still_has_the_link(monkeypatch):
    captured = {}

    async def fake_emit(session_id, kind, **data): pass

    async def fake_loop(input_items, *a, **k):
        captured["items"] = input_items
        return "done"

    monkeypatch.setattr(wr, "emit_task", fake_emit)
    monkeypatch.setattr("kotoba.core.loop.agentic_loop", fake_loop)

    ws.start("s2", GOAL)
    asyncio.run(wr._run("s2", GOAL, _DB(), {}, mcp=None, request=MESSAGE))

    user = [m for m in captured["items"] if m["role"] == "user"]
    assert len(user) == 1, "one user item, so `latest user message` readers see the request AND the goal"
    assert "https://news.ycombinator.com" in user[0]["content"]
    assert GOAL in user[0]["content"]


def test_the_bare_goal_is_kept_when_there_is_nothing_to_carry():
    assert wr._brief(GOAL, "") == GOAL
    assert wr._brief(GOAL, GOAL) == GOAL


def test_a_long_paste_is_clipped_but_still_carried():
    request = "open " + ("x" * 4000)
    out = wr._brief("do it", request)
    quoted = out[:out.index(wr._LANGUAGE_NOTE)]
    assert len(quoted) < 2600 and quoted.endswith("…\"") and "open xxx" in quoted


def test_the_job_is_told_which_language_the_deliverable_is_in(monkeypatch):
    """Asked in Spanish for an investigation, the job saved `research/pepe-the-frog.md` and wrote it in
    English. Its whole world is 5,900 characters of English instructions and a goal the companion turn
    paraphrased into English; the user's own words were the only Spanish in it, and nothing said what
    they meant. The rule that does say it lives in the research skill — which the same instructions tell
    the job not to open before acting."""
    spanish = "hazme una investigación a fondo sobre el Pepe, el meme de la rana"
    brief = wr._brief(GOAL, spanish)
    assert spanish in brief and GOAL in brief
    assert brief.index(spanish) < brief.index("LANGUAGE"), "the rule points at words already quoted"
    assert "summary" in wr._LANGUAGE_NOTE and "file" in wr._LANGUAGE_NOTE
    assert "not the user asking for English" in wr._LANGUAGE_NOTE

    captured = {}

    async def fake_emit(session_id, kind, **data): pass

    async def fake_loop(input_items, *a, **k):
        captured["items"] = input_items
        return "done"

    monkeypatch.setattr(wr, "emit_task", fake_emit)
    monkeypatch.setattr("kotoba.core.loop.agentic_loop", fake_loop)
    ws.start("s-lang", GOAL)
    asyncio.run(wr._run("s-lang", GOAL, _DB(), {}, mcp=None, request=spanish))
    user = [m for m in captured["items"] if m["role"] == "user"]
    assert wr._LANGUAGE_NOTE in user[0]["content"], "it reaches the run, not just the helper"


def test_the_goal_is_asked_for_in_the_users_language_before_anything_paraphrases_it():
    goal = sw.SCHEMA["parameters"]["properties"]["goal"]["description"]
    assert "LANGUAGE THE USER IS SPEAKING" in goal
    assert "English report" in goal, "say what an English goal costs, not just that it is wrong"


def test_clarify_in_work_mode_opens_a_card_and_waits(monkeypatch):
    asked = {}

    async def fake_request_input(sid, label, kind="text", **kw):
        asked["card"] = (sid, label, kind)
        return "https://news.ycombinator.com"

    monkeypatch.setattr(interaction, "request_input", fake_request_input)
    monkeypatch.setattr(events, "has_listener", lambda sid: True)
    ctx = types.SimpleNamespace(session_id="s3", mode="work")

    out = asyncio.run(clarify.execute({"question": "Which Hacker News page or URL should I open?"}, ctx))

    assert asked["card"][0] == "s3" and "Hacker News" in asked["card"][1]
    assert "https://news.ycombinator.com" in out, "the answer goes back to the model, not the question"


def test_clarify_in_work_mode_never_answers_itself(monkeypatch):
    """The defect exactly: the question came back as the tool result and became the run's summary."""
    async def unanswered(*a, **k):
        return None

    monkeypatch.setattr(interaction, "request_input", unanswered)
    monkeypatch.setattr(events, "has_listener", lambda sid: True)
    question = "Which Hacker News page or URL should I open?"
    out = asyncio.run(clarify.execute({"question": question}, types.SimpleNamespace(
        session_id="s4", mode="work")))
    # The defect was the question coming back AS THE ANSWER, so the run summarised itself with it.
    # It may still be named — that is what expired — but the sentence has to say nothing happened.
    assert out.strip() != question and not out.strip().startswith(question)
    assert "did not happen" in out
    assert "not their choice" in out, "a card nobody answered is not a decision anybody made"


def test_clarify_refuses_to_ask_a_screen_that_does_not_exist(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("must not open a card nobody can answer")

    monkeypatch.setattr(interaction, "request_input", boom)
    monkeypatch.setattr(events, "has_listener", lambda sid: False)
    out = asyncio.run(clarify.execute({"question": "which page?"}, types.SimpleNamespace(
        session_id="s5", mode="work")))
    assert "not asked" in out.lower() and "do not ask again" in out.lower()


def test_clarify_in_a_companion_turn_is_still_just_the_question(monkeypatch):
    """She reads it aloud and their reply is the next turn — no card, no blocking."""
    async def boom(*a, **k):
        raise AssertionError("a voice turn must not block on a card")

    monkeypatch.setattr(interaction, "request_input", boom)
    out = asyncio.run(clarify.execute({"question": "¿cuál de los dos?"}, types.SimpleNamespace(
        session_id="s6", mode="companion")))
    assert out == "¿cuál de los dos?"


def test_the_card_really_reaches_the_wire_and_the_answer_comes_back():
    """No doubles under clarify: the real interaction layer, the real SSE queue, the real POST /input
    resolution. This is the delivery path the question never had."""
    async def scenario():
        queue = events.register("s-live")
        ctx = types.SimpleNamespace(session_id="s-live", mode="work", channel="text")
        task = asyncio.create_task(clarify.execute({"question": "Which page should I open?"}, ctx))
        frame = await asyncio.wait_for(queue.get(), 2)
        while frame.get("kind") != "need_input":
            frame = await asyncio.wait_for(queue.get(), 2)
        assert frame["mode"] == "input" and frame["wait"] is True
        assert frame["label"] == "Which page should I open?"
        assert interaction.resolve("s-live", "https://news.ycombinator.com", frame["request_id"])
        return await asyncio.wait_for(task, 2)

    out = asyncio.run(scenario())
    assert "https://news.ycombinator.com" in out


def test_the_loop_runs_clarify_as_an_interactive_tool(monkeypatch):
    """INTERACTIVE, so the 30 s compute timeout doesn't cancel the card out from under the user."""
    from kotoba.core.loop import execute_with_heartbeat

    async def slow_answer(sid, label, kind="text", **kw):
        await asyncio.sleep(0.05)
        return "the second one"

    monkeypatch.setattr(interaction, "request_input", slow_answer)
    monkeypatch.setattr(events, "has_listener", lambda sid: True)
    assert clarify.INTERACTIVE is True
    ctx = ToolContext(db=_DB(), session_id="s13", mode="work", channel="text")
    ok, result = asyncio.run(
        execute_with_heartbeat("clarify", {"question": "which one?"}, asyncio.Queue(), {}, ctx)
    )
    assert ok and "the second one" in result


def test_a_job_that_touched_nothing_is_not_announced_as_a_result():
    ws.start("s7", GOAL)
    ws.finish("s7", "Which Hacker News page or URL should I open?", [])
    note = ws.prompt_note("s7")
    assert "ended without doing anything" in note
    assert "just finished" not in note
    assert "watch the screen" in note and "correct that" in note.lower()


def test_a_job_that_did_something_reports_its_result():
    ws.start("s8", GOAL)
    ws.set_step("s8", "browser_navigate news.ycombinator.com")
    ws.finish("s8", "The top story is Spaghettifying DRAM.", [])
    note = ws.prompt_note("s8")
    assert "just finished" in note and "Spaghettifying DRAM" in note
    assert "ended without doing anything" not in note


def test_every_ending_says_the_job_is_over():
    """Only the CLI's written prompt carried this rule; the spoken register never got it, and the frozen
    voice snapshot is not the place to add it. The note is read by both."""
    ws.start("s9", "x"); ws.set_step("s9", "shell ls"); ws.finish("s9", "listed the folder", [])
    ws.start("s10", "y"); ws.fail("s10", "it took too long and I stopped it")
    for sid in ("s9", "s10"):
        note = ws.prompt_note(sid)
        assert "That job is OVER" in note and "watch the screen" in note


def test_start_work_only_claims_the_launch(monkeypatch):
    monkeypatch.setattr(sw.work_runner, "start", lambda *a, **k: None)
    ctx = ToolContext(db=_DB(), session_id="s11", mode="companion")
    out = asyncio.run(sw.execute({"goal": GOAL}, ctx))
    low = out.lower()
    assert "launched" in low and "nothing has been opened" in low


@pytest.mark.parametrize("run_did", [True, False])
def test_a_web_search_counts_as_something_the_user_saw(run_did):
    """A research job's only visible rows are its web searches — it must not be told it did nothing."""
    ws.start("s12", "research it")
    if run_did:
        ws.note_acted("s12")
    ws.finish("s12", "Here's what I found.", [])
    assert ("just finished" in ws.prompt_note("s12")) is run_did


class _Stream:
    def __init__(self, evs): self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _Client:
    def __init__(self, scripts):
        outer = self

        class _R:
            def __init__(self): self.i = 0

            async def create(self, **kw):
                evs = scripts[min(self.i, len(scripts) - 1)]
                self.i += 1
                return _Stream(evs)

        self.responses = _R()
        outer  # noqa: B018 — the closure is the point


def _drive(mode: str, sid: str, evs: list) -> None:
    from kotoba.core import loop as core_loop

    events.register(sid)
    ctx = ToolContext(db=_DB(), session_id=sid, client=None, mode=mode)
    ctx.approval = None
    asyncio.run(core_loop._run_iterations(
        _Client([evs, [types.SimpleNamespace(type="response.output_text.delta", delta="done")]]),
        ctx, [{"role": "user", "content": "look it up"}], asyncio.Queue(), {},
        max_iterations=3, mode=mode, allow_risk={"read", "write", "exec", "network"},
        toolset_filter=None,
    ))
    events.unregister(sid)


def _web_search_event(i: int):
    item = types.SimpleNamespace(type="web_search_call", id=f"ws{i}",
                                 action=types.SimpleNamespace(query="hacker news"))
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def test_the_loop_marks_a_work_run_that_searched(monkeypatch):
    monkeypatch.setattr("kotoba.core.emotions.extract_emotion", lambda *a, **k: "neutral", raising=False)
    ws.start("s-web", "find the headline")
    _drive("work", "s-web", [_web_search_event(1)])
    assert ws.get("s-web")["acted"] is True


def test_the_loop_marks_nothing_for_a_run_that_only_talked():
    ws.start("s-quiet", "find the headline")
    _drive("work", "s-quiet", [types.SimpleNamespace(type="response.output_text.delta", delta="hmm")])
    assert ws.get("s-quiet")["acted"] is False


def test_a_companion_turn_never_writes_the_job_witness():
    ws.start("s-companion", "a job someone else started")
    _drive("companion", "s-companion", [_web_search_event(1)])
    assert ws.get("s-companion")["acted"] is False
