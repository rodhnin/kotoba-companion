"""A cancellation nobody requested has to say where it was RAISED, not where it was noticed.

The witness for this sat after the `await task` that follows the heartbeat loop and could never run:
the loop waits on `wait_for(shield(task))`, and a bare CancelledError is a BaseException, so it passes
through both handlers and leaves the function. It also named a logger the module does not define.

`shield` keeps the tool task's own exception, so the raise site survives in one place: asking the
finished task for its result, ONCE, before anything above unwinds.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from kotoba.core import loop as kloop


class _Ctx:
    session_id = "s-stray"
    register = "voice"
    model_role = None


def _spec(name, body):
    from kotoba.tools import ToolSpec

    class _Mod:
        SCHEMA = {"name": name, "description": "d", "parameters": {}}

        @staticmethod
        async def execute(args, ctx):
            return await body()

    return ToolSpec(name=name, module=_Mod, schema=_Mod.SCHEMA, toolset="core", risk="read")


def _run(spec):
    """Drive the real `execute_with_heartbeat` against one registered tool."""
    from kotoba.tools import registry

    saved = dict(registry._REGISTRY)
    registry.register(spec)
    try:
        return asyncio.run(kloop.execute_with_heartbeat(spec.name, {}, asyncio.Queue(), {}, _Ctx()))
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)


def test_the_log_carries_the_raise_site_not_the_await(caplog):
    """The whole point: the reported traceback must name the tool's own frame."""
    async def raises_inside():
        await asyncio.sleep(0)
        raise asyncio.CancelledError("Cancelled by scope 0xdead, deadline exceeded")

    with caplog.at_level(logging.ERROR, logger="kotoba"):
        with pytest.raises(asyncio.CancelledError):
            _run(_spec("stray_tool", raises_inside))

    stray = [r for r in caplog.records if "nobody requesting it" in r.getMessage()]
    assert stray, "a cancellation nobody asked for went unreported"
    record = stray[0]
    assert "stray_tool" in record.getMessage()
    assert "deadline exceeded" in record.getMessage(), \
        "the message the raiser stamped was dropped, and that is the half that names the scope"
    assert record.exc_info and record.exc_info[1] is not None
    frames = []
    tb = record.exc_info[2]
    while tb is not None:
        frames.append(tb.tb_frame.f_code.co_name)
        tb = tb.tb_next
    assert "raises_inside" in frames, f"the traceback names the await, not the raise: {frames}"


def test_a_cancellation_somebody_asked_for_says_nothing(caplog):
    """A turn the user stops travels the same path. Reporting it would train the reader to ignore it."""
    async def slow():
        await asyncio.sleep(30)

    async def inner():
        from kotoba.tools import registry

        saved = dict(registry._REGISTRY)
        registry.register(_spec("slow_tool", slow))
        try:
            await kloop.execute_with_heartbeat("slow_tool", {}, asyncio.Queue(), {}, _Ctx())
        finally:
            registry._REGISTRY.clear()
            registry._REGISTRY.update(saved)

    async def go():
        task = asyncio.create_task(inner())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with caplog.at_level(logging.ERROR, logger="kotoba"):
        asyncio.run(go())
    assert not [r for r in caplog.records if "nobody requesting it" in r.getMessage()]


def test_a_tool_that_simply_works_is_untouched():
    async def fine():
        return "all good"

    assert _run(_spec("fine_tool", fine)) == (True, "all good")
