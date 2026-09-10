"""Switching a CORE family off has to stop its tools running, not just hide them from the next schema.

`schemas_for` skips a disabled toolset, so the model stops being OFFERED it — but a name it already
picked up earlier in the same turn was still resolved at the dispatch door via `registry().get(name)`,
which reads neither `_disabled_toolsets` nor anything derived from it. A plugin family has no such hole
because disabling one DEREGISTERS its tools; a core family stays filter-only on purpose (its modules
are ours and already imported), so the fix closes the door itself rather than deregistering.

Not through `_passes_check`: it memoises for 30 s, so a family switched back on would stay dead for
half a minute, and that same memo can make an unrelated suite's verdict depend on file order."""
from __future__ import annotations

import asyncio

import pytest

import kotoba.tools.registry as reg
from kotoba.core.loop import execute_with_heartbeat
from kotoba.tools.registry import ToolSpec, register


@pytest.fixture
def probe():
    """A tool in a CORE family (no `plugin:` prefix), with a sentinel that only its body can write."""
    ran: list[str] = []

    class _Mod:
        SCHEMA = {"type": "function", "name": "web_probe", "description": "Probe.",
                  "parameters": {"type": "object", "properties": {}, "required": []}}
        BUILT_IN = False

        @staticmethod
        async def execute(args, ctx):
            ran.append("body")
            return "fetched the page"

    register(ToolSpec(name="web_probe", module=_Mod, schema=_Mod.SCHEMA, toolset="web"))
    saved = set(reg._disabled_toolsets)
    try:
        yield ran
    finally:
        reg.deregister("web_probe")
        reg._disabled_toolsets.clear()
        reg._disabled_toolsets.update(saved)


class _Ctx:
    call_id = "c-off"


def _call(name: str = "web_probe", ctx=None):
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        return await execute_with_heartbeat(name, {}, q, {}, ctx=ctx, timeout=5)

    return asyncio.run(go())


def test_the_family_runs_while_it_is_on(probe):
    ok, result = _call()
    assert ok is True and result == "fetched the page"
    assert probe == ["body"]


def test_a_name_from_earlier_in_the_turn_cannot_reach_a_switched_off_family(probe):
    """The reproduction: the family is off, `schemas_for` no longer offers it, and the model calls the
    name it already had."""
    reg.set_toolset_enabled("web", False)
    assert "web_probe" not in {s.get("name") for s in reg.schemas_for("work")}

    ok, result = _call()
    assert probe == [], "the tool's body ran for a family the user had switched off"
    assert ok is False, "and the turn was told it worked"
    assert "web_probe" in result and "not" in result.lower()


def test_nothing_ran_so_the_audit_trail_gains_no_executed_line(probe):
    """A hallucinated name has no ToolSpec, so `is_action` is false and no row is written. A real tool in
    a switched-off family HAS one, and would have been recorded `executed:failed` for a dispatch that
    never happened — the witness core.loop reads before it writes that row is the one this asserts."""
    from kotoba.core.loop import tool_refused

    reg.set_toolset_enabled("web", False)
    ctx = _Ctx()
    _call(ctx=ctx)
    assert tool_refused(ctx, "c-off")


def test_the_registry_entry_stays_because_a_core_family_is_not_unloaded(probe):
    """Deregistering is the PLUGIN answer — it also takes third-party code out of the process. A core
    module is ours and already imported, so removing its spec would only lose the `check()` state and the
    voice patterns core.loop reads by name elsewhere."""
    reg.set_toolset_enabled("web", False)
    assert "web_probe" in reg.registry()


def test_switching_it_back_on_is_immediate_and_not_cached_for_thirty_seconds(probe):
    """If the gate had gone into `_passes_check`, the toggle would lie for `_CHECK_TTL` seconds."""
    reg.set_toolset_enabled("web", False)
    _call()
    reg.set_toolset_enabled("web", True)

    ok, result = _call()
    assert ok is True and probe == ["body"]
