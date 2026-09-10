"""When there's background work, load_context injects a developer note so the voice turn can report it.

Placement is the defect the second half of this file pins. Prepended at index 0 the note sat in front
of the whole system prompt, thousands of tokens from the point of generation — and on a __work_done__
sentinel turn (whose trigger text is dropped from history) the LAST thing the model read was its own
"I'm on it" line from when the job STARTED, so it announced the start again just as the job ended.
The note goes at the TAIL, where recency makes it win — the same placement _refresh_plan_note uses.
"""
from __future__ import annotations

import kotoba.core.work_state as ws
from kotoba.core.context import inject_work_note


def setup_function():
    ws._state.clear()


def test_inject_work_note_adds_developer_note_when_running():
    ws.start("s1", "build a page")
    items = [{"role": "developer", "content": "SYS"}, {"role": "user", "content": "how's it going?"}]
    out = inject_work_note(items, "s1")
    assert any(m["role"] == "developer" and "BACKGROUND WORK" in m["content"] for m in out)


def test_inject_work_note_noop_when_idle():
    items = [{"role": "developer", "content": "SYS"}, {"role": "user", "content": "hi"}]
    out = inject_work_note(items, "none")
    assert out == items


def test_the_work_note_is_the_last_thing_the_model_reads():
    ws.start("s2", "research the protocol")
    items = [{"role": "developer", "content": "SYS"},
             {"role": "user", "content": "investígalo"},
             {"role": "assistant", "content": "Ya está en marcha, lo estoy repartiendo."}]
    out = inject_work_note(items, "s2")
    assert out[-1]["role"] == "developer" and "BACKGROUND WORK running" in out[-1]["content"]
    assert out[:-1] == items, "the note is appended, never woven into the conversation"


def test_a_finished_job_outranks_her_own_started_line():
    """The sentinel-turn shape itself: the trigger was dropped, history ends on the announcement she
    made when the job began, and the only thing saying it FINISHED must sit after it — in the USER
    position, since nobody spoke this turn."""
    ws.start("s3", "research the protocol")
    ws.set_step("s3", "writing research/protocol.md")  # the job really did draw a row (see work_state.acted)
    ws.finish("s3", "wrote research/protocol.md", ["research/protocol.md"])
    items = [{"role": "developer", "content": "SYS"},
             {"role": "user", "content": "investígalo"},
             {"role": "assistant", "content": "Ya está en marcha, lo estoy repartiendo."}]
    out = inject_work_note(items, "s3", unprompted=True)
    assert out[-1]["role"] == "user"
    assert "BACKGROUND WORK just finished" in out[-1]["content"]
    assert out.index(next(m for m in out if m["role"] == "assistant")) < len(out) - 1
