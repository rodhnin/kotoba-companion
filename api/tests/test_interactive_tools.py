"""Audit regression — human-in-the-loop tools (ask_user) are not killed by the compute timeout.

Bug found in audit: ask_user waits on interaction.request_input (up to 180s), but the loop's
execute_with_heartbeat cancelled every tool at TOOL_TIMEOUT=30s AND narrated a 3s "still working"
heartbeat the whole time. So a user typing an API key was cut off at 30s (and spammed). Interactive
tools now wait QUIETLY, governed by their own interaction timeout.
"""
from __future__ import annotations

import asyncio
import types

import pytest

import kotoba.tools.registry as reg
from kotoba.core.loop import execute_with_heartbeat
from kotoba.tools.registry import ToolSpec


@pytest.fixture
def clean_registry():
    saved = dict(reg._REGISTRY)
    saved_cache = dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved)
        reg._check_cache.clear()
        reg._check_cache.update(saved_cache)


def _register(name, execute, **attrs):
    fields = {"ANNOUNCE": "", "HEARTBEAT": [], "COMPLETE": "", "FAIL": "oops"}
    fields.update(attrs)  # caller overrides (INTERACTIVE, HEARTBEAT, …)
    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": name}, __name__=f"tools.action.{name}",
        execute=execute, **fields,
    )
    reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA, toolset="core", risk="read"))


def test_interactive_tool_not_cancelled_by_compute_timeout(clean_registry):
    """Even with timeout=0 (which would instantly cancel a normal tool), an INTERACTIVE tool that
    takes a moment to get its answer completes and returns its value."""
    async def slow_human(args, ctx):
        await asyncio.sleep(0.15)  # stands in for the user typing
        return 'The user replied: "https://example.com/very/long/link"'

    _register("fake_ask", slow_human, INTERACTIVE=True)

    async def go():
        q: asyncio.Queue = asyncio.Queue()
        return await execute_with_heartbeat("fake_ask", {}, q, {}, ctx=None, timeout=0)

    ok, result = asyncio.run(go())
    assert ok is True and "example.com" in result


def test_interactive_tool_waits_quietly_no_heartbeat_spam(clean_registry):
    """While waiting for the person, the loop must NOT push heartbeat phrases onto the voice queue."""
    async def slow_human(args, ctx):
        await asyncio.sleep(0.2)
        return "done"

    _register("fake_ask2", slow_human, INTERACTIVE=True, HEARTBEAT=["Whenever you're ready..."])

    async def go():
        q: asyncio.Queue = asyncio.Queue()
        ok, result = await execute_with_heartbeat("fake_ask2", {}, q, {}, ctx=None, timeout=0)
        return ok, q.qsize()

    ok, queued = asyncio.run(go())
    assert ok is True
    assert queued == 0, "interactive wait should not narrate compute heartbeats"


def test_interactive_tool_none_is_graceful_fail(clean_registry):
    """No answer (the tool returns None on its own timeout) → a clean (False, …) so the FAIL line plays."""
    async def no_answer(args, ctx):
        return None

    _register("fake_ask3", no_answer, INTERACTIVE=True)

    async def go():
        q: asyncio.Queue = asyncio.Queue()
        return await execute_with_heartbeat("fake_ask3", {}, q, {}, ctx=None, timeout=0)

    ok, _ = asyncio.run(go())
    assert ok is False


def test_ask_user_is_interactive_for_work_mode():
    # ask_user is mode-aware: in COMPANION (voice) it opens the card and returns at once (non-blocking, so
    # it never kills the ElevenLabs WebSocket); in WORK (background loop) there is no "next turn", so it
    # BLOCKS on the answer like ask_secret. The INTERACTIVE flag stops the loop compute-cancelling that
    # ~180s wait at 30s — the instant companion path is unaffected (it returns immediately anyway).
    from kotoba.tools.builtin import ask_user

    assert getattr(ask_user, "INTERACTIVE", False) is True
