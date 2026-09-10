"""mcp_find opens an on-screen approval card and a paste-token box, then WAITS for the human.

It must therefore be treated as INTERACTIVE, so `execute_with_heartbeat` does not compute-cancel it
at the loop's tool timeout (`core.loop.TOOL_TIMEOUT`) while the user is still deciding or pasting.
The length of a human wait is governed by the interaction layer, never by the loop."""
from __future__ import annotations

import asyncio
import types

import kotoba.tools.action.mcp_find as mcp_find
from kotoba.core.loop import execute_with_heartbeat
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec
import kotoba.tools.registry as reg


def test_mcp_find_declares_interactive():
    assert getattr(mcp_find, "INTERACTIVE", False) is True


def test_interactive_tool_not_compute_cancelled(monkeypatch):
    """An INTERACTIVE tool whose execute() outlasts the tool timeout — shrunk here to 0.05 s — still
    returns its result: the interactive branch bypasses the compute-cancel that would otherwise abort
    a slow human wait."""
    saved = dict(reg._REGISTRY)
    try:
        async def slow(args, ctx):
            await asyncio.sleep(0.2)
            return "connected 'spotify'"

        mod = types.SimpleNamespace(INTERACTIVE=True, execute=slow,
                                    ANNOUNCE="", HEARTBEAT=[], COMPLETE="", FAIL="")
        reg.register(ToolSpec(name="mcp_find", module=mod,
                              schema={"type": "function", "name": "mcp_find"},
                              toolset="mcp", risk="exec", built_in=False))

        ctx = ToolContext(db=None, session_id="s", client=None, mode="work")

        async def go():
            return await execute_with_heartbeat(
                "mcp_find", {"query": "spotify"}, asyncio.Queue(), {}, ctx, timeout=0.05
            )

        ok, result = asyncio.run(go())
        assert ok is True
        assert "connected" in result
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved)
