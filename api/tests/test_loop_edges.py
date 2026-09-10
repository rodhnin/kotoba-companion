"""Audit — the agentic loop survives hostile/degenerate model output without dying or looping.

Covers: tool arguments that are valid JSON but NOT an object (null/list/number) crashing the work turn;
the loop helpers (_step_text/_result_text) tolerating any shape; unknown tool; empty result.
"""
from __future__ import annotations

import asyncio

import pytest

from kotoba.core.loop import _parse_tool_args, _result_text, _step_text, execute_with_heartbeat


def test_non_object_json_args_coerced_to_dict():
    """The loop's real arg-parse: bad/non-object JSON must become {} (never crash a .get())."""
    for raw in ["null", "123", '"a string"', "[1,2,3]", "true", "{bad", "", None]:
        assert _parse_tool_args(raw) == {}, raw
    assert _parse_tool_args('{"path": "x.txt"}') == {"path": "x.txt"}


def test_step_and_result_text_tolerate_any_shape():
    # These run on every tool call; a non-dict used to raise AttributeError and kill the turn.
    for bad in ({}, {"command": "ls"}, {"path": "x.txt"}, {"command": None}, {"command": 3}):
        assert isinstance(_step_text("shell", bad), str)
    for bad_result in ("exit=0\nstdout:\nhi", "", "x" * 100_000):
        assert isinstance(_result_text("shell", True, bad_result), str)


def test_unknown_tool_name_is_a_truthful_failure():
    """A name with no registry entry must FAIL with a note the model can recover
    from, never (True, ""). The old no-op here was justified "for built-ins", but web_search runs
    server-side inside the response and never arrives as a function_call — so this branch only ever
    fires for a hallucinated name or an MCP tool whose server died mid-turn, and the empty success
    skipped the fail note, the failure count, and narrated "Done!" over nothing."""
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        return await execute_with_heartbeat("does_not_exist", {}, q, {}, ctx=None, timeout=1)
    ok, result = asyncio.run(go())
    assert ok is False
    assert "does_not_exist" in result
    assert "no tool" in result.lower()
    assert "not call it again" in result.lower()


def test_vanished_mcp_tool_reports_failure_not_empty_success():
    """The real-world shape of the same defect: an MCP server dies mid-turn, its owner deregisters the
    tools, and the already-emitted response still invokes one."""
    from kotoba.tools.registry import ToolSpec, register, deregister, registry

    class _Mod:
        SCHEMA = {"type": "function", "name": "ghost__click"}
        BUILT_IN = False

        @staticmethod
        async def execute(args, ctx):
            return "clicked"

    register(ToolSpec(name="ghost__click", module=_Mod, schema=_Mod.SCHEMA, toolset="mcp:ghost"))
    deregister("ghost__click")
    assert "ghost__click" not in registry()

    async def go():
        q: asyncio.Queue = asyncio.Queue()
        return await execute_with_heartbeat("ghost__click", {"ref": "e12"}, q, {}, ctx=None, timeout=1)
    ok, result = asyncio.run(go())
    assert ok is False and "ghost__click" in result


def test_builtin_tool_name_is_noop_ok():
    """OpenAI built-ins (web_search) are executed server-side by OpenAI → the loop treats them as ok
    with no local execution (not a function tool)."""
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        return await execute_with_heartbeat("web_search", {}, q, {}, ctx=None, timeout=1)
    ok, result = asyncio.run(go())
    assert ok is True


def test_tool_output_is_capped_to_keep_context_bounded():
    """A huge tool result (e.g. an MCP *-fetch returning a whole Notion page) must be capped before it's
    fed back to the model — otherwise results accumulate across iterations and blow the org's tokens/min
    ceiling, 429-ing the stream into a rate-limit loop. The cap keeps the loop sane; a small result
    is untouched."""
    from kotoba.core.loop import _MAX_TOOL_OUTPUT_CHARS, _call_output

    big = "x" * (_MAX_TOOL_OUTPUT_CHARS + 50_000)
    out = _call_output("call_1", big, "")["output"]
    assert isinstance(out, str)
    assert len(out) < len(big)
    assert len(out) <= _MAX_TOOL_OUTPUT_CHARS + 200  # cap + a short truncation note
    assert "truncated" in out

    small = "just a short answer"
    assert _call_output("call_2", small, "")["output"] == small  # under the cap → untouched


def _budget(name: str, channel: str) -> int:
    from kotoba.core.loop import TOOL_TIMEOUT, _tool_budget
    from kotoba.tools.registry import registry

    spec = registry()[name]
    return _tool_budget(spec.module, spec, {}, TOOL_TIMEOUT, channel)


def test_approval_gated_tools_outlive_the_approval_window_on_both_channels():
    """The invariant behind VOICE_APPROVAL_TIMEOUT: a tool that can park on the approval card must time out
    AFTER the card does, so a no-answer becomes a clean in-character deny instead of a compute-cancel
    that abandons the card. Raising the typed window without raising the budget would break it."""
    from kotoba.core import interaction

    for channel in ("voice", "text"):
        window = interaction.approval_timeout(channel)
        for name in ("shell", "execute_code", "mcp_find", "mcp_install", "delegate"):
            assert _budget(name, channel) > window, (name, channel)


def test_channel_widening_leaves_voice_and_read_tools_alone():
    """The widening is surgical: nothing changes on the voice channel, and a read tool keeps the plain
    30s budget on both channels (a hung web_extract must not now hang for three minutes)."""
    from kotoba.core.loop import TOOL_TIMEOUT

    assert _budget("shell", "voice") == 90        # 60s default + the 25s+5 voice headroom, as before
    assert _budget("mcp_install", "voice") == 150  # its own TIMEOUT, untouched
    assert _budget("web_extract", "voice") == _budget("web_extract", "text") == TOOL_TIMEOUT


def test_interrupted_step_carries_a_structured_marker(monkeypatch):
    """A client must not have to string-match a display word to know a step was cut short. The
    flushed frame carries interrupted=True; the text is only what a UI prints (and is now English)."""
    import kotoba.core.loop as loop
    from kotoba.core import events

    monkeypatch.setattr(loop, "get_client", lambda: object())

    async def cancelled_mid_tool(client, ctx, *a, **k):
        ctx._open_steps = {"call_9": "run `sleep 100`"}
        raise asyncio.CancelledError()

    monkeypatch.setattr(loop, "_run_iterations", cancelled_mid_tool)

    class _DB:
        async def insert_audit_log(self, *a, **k): pass
        async def list_approved_commands(self): return []

    async def go():
        q = events.register("intr")
        with pytest.raises(asyncio.CancelledError):
            await loop.agentic_loop([{"role": "user", "content": "x"}], "intr", _DB(), asyncio.Queue(), {})
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("intr")
        return frames

    frames = asyncio.run(go())
    step = next(f for f in frames if f.get("kind") == "step" and f.get("phase") == "done")
    assert step["ok"] is False
    assert step["interrupted"] is True                     # the machine-readable state
    assert step["text"] == loop.INTERRUPTED_TEXT == "(interrupted)"
