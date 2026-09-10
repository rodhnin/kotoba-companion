"""The turn engine's own limits, and the seam a third caller hits first.

1. The cap check runs at the TOP of an iteration, so `max_iterations` must exceed
   `max_tool_calls` by one, or the loop stops mid-chain and work_runner reports "Done." as success.
2. Companion must not inherit the work knob — lowering a work limit must not silence voice.
3. `turns.supersede` must swallow only the superseded task's cancellation, never the caller's.
4. The reaction line replacing a forbidden phrase must not leak a raw tag when tags are off.
5. The turn builder raises a plain error, not an HTTP one — it also runs outside FastAPI.
6. `attachments.take()` is one-shot: a pending part must never be silently dropped.
"""
from __future__ import annotations

import asyncio

import pytest

from kotoba.core import turns
from kotoba.core.loop import _caps_for, _default_max_iterations


# --- 1 + 2. the caps -------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["work", "companion"])
def test_there_is_always_an_iteration_left_to_answer_without_tools(mode):
    cap, _fail = _caps_for(mode)
    assert _default_max_iterations(mode) > cap, (
        "the tool-withdrawal layer is evaluated at the top of an iteration, so it needs one more"
    )


def test_a_work_limit_cannot_silence_the_voice(monkeypatch):
    """Companion must not inherit the work knob: at work_max_iter=2 a voice turn ran out of iterations
    before its own 8-call budget and returned "" — which the server turns into skip_turn, i.e. silence."""
    monkeypatch.setenv("KOTOBA_WORK_MAX_ITER", "2")
    import importlib

    import kotoba.core.loop as loop

    importlib.reload(loop)
    try:
        companion_cap, _ = loop._caps_for("companion")
        assert loop._default_max_iterations("companion") > companion_cap
    finally:
        monkeypatch.delenv("KOTOBA_WORK_MAX_ITER", raising=False)
        importlib.reload(loop)


def test_work_still_honours_a_raised_limit(monkeypatch):
    monkeypatch.setenv("KOTOBA_WORK_MAX_ITER", "120")
    import importlib

    import kotoba.core.loop as loop

    importlib.reload(loop)
    try:
        assert loop._default_max_iterations("work") == 120
    finally:
        monkeypatch.delenv("KOTOBA_WORK_MAX_ITER", raising=False)
        importlib.reload(loop)


# --- 3. supersede tells the two cancellations apart ------------------------------------------------

def test_supersede_swallows_the_old_turns_cancellation():
    """The behaviour it exists for: cancel the previous turn and wait out its teardown, quietly."""
    async def main():
        async def old():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)   # working off + sandbox kill take real time
                raise

        task = asyncio.create_task(old())
        turns.register("sup-old", task)
        await asyncio.sleep(0)
        await turns.supersede("sup-old")     # must NOT raise
        assert task.cancelled() or task.done()

    asyncio.run(main())


def test_supersede_propagates_the_callers_own_cancellation():
    async def main():
        trace = []

        async def old():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await asyncio.sleep(0.3)
                raise

        turns.register("sup-me", asyncio.create_task(old()))
        await asyncio.sleep(0)

        async def new_turn():
            try:
                await turns.supersede("sup-me")
            except asyncio.CancelledError:
                trace.append("propagated")
                raise
            trace.append("kept going after being cancelled")

        t = asyncio.create_task(new_turn())
        await asyncio.sleep(0.05)   # catch it parked inside supersede
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        assert trace == ["propagated"], trace

    asyncio.run(main())


# --- 4. the injected reaction line -----------------------------------------------------------------

@pytest.mark.parametrize("expressive,expect_tag", [(True, True), (False, False)])
def test_the_reaction_line_only_carries_a_tag_when_tags_are_performed(monkeypatch, expressive, expect_tag):
    import kotoba.core.stream as sse

    monkeypatch.setattr(sse, "expressive_mode", lambda: expressive)
    f = sse.ForbiddenPhraseFilter()
    out = f.feed("I can't access that link, sorry. ") + f.flush()
    assert out, "the claim must still be replaced by a reaction"
    assert ("[" in out) is expect_tag, out


# --- 5. the builder is HTTP-free -------------------------------------------------------------------

def test_the_turn_builder_raises_a_plain_error():
    from kotoba.core.context import NoUserMessage, latest_user_message

    with pytest.raises(NoUserMessage):
        latest_user_message([{"role": "assistant", "content": "hi"}])
    assert issubclass(NoUserMessage, ValueError)


def test_the_turn_builder_does_not_import_fastapi():
    """The CLI drives load_context in process; a FastAPI exception there has nobody to translate it."""
    import ast
    from pathlib import Path

    import kotoba.core.context as ctx

    tree = ast.parse(Path(ctx.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "fastapi" not in (node.module or "")
        elif isinstance(node, ast.Import):
            assert all("fastapi" not in a.name for a in node.names)


def test_the_route_still_answers_400():
    """The HTTP shape belongs to the HTTP layer, and must not have been lost in the move."""
    from pathlib import Path

    import kotoba.server as main

    src = Path(main.__file__).read_text(encoding="utf-8")
    assert "except NoUserMessage" in src and "status_code=400" in src


# --- 6. an attachment is never consumed into nothing -----------------------------------------------

def test_a_pending_attachment_always_lands_somewhere():
    """take() is irreversible, so 'no user message to attach to' must not mean 'silently dropped'."""
    from pathlib import Path

    import kotoba.core.context as ctx

    src = Path(ctx.__file__).read_text(encoding="utf-8")
    block = src.split("pending = attachments.take", 1)[1].split("soul = await", 1)[0]
    assert "if last_user_idx is None:" in block
    assert 'history.append({"role": "user", "content": list(pending)})' in block
