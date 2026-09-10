"""Single source of tool-usage guidance + tool-aware injection.

Guidance must be injected ONLY when the relevant tools are actually offered (so the model isn't told how
to use a browser it doesn't have), and the work-loop must always carry the anti-hallucination/verification
block (the cause of the "¡Listo! Inicié sesión" fabrication). The browser workflow text lives here ONCE and
is reused by the MCP schema enrichment (no more 3 scattered hardcodes).
"""
from __future__ import annotations

import kotoba.core.tool_guidance as tg


def test_browser_block_only_when_a_browser_tool_is_offered():
    with_browser = tg.guidance_for({"browser__browser_click", "web_search"}, "work")
    without_browser = tg.guidance_for({"web_search", "write_file"}, "work")
    assert "snapshot" in with_browser.lower() and "target" in with_browser.lower()
    assert "snapshot" not in without_browser.lower()  # no browser tool → no browser workflow text


def test_verification_block_present_in_work_mode_always():
    g = tg.guidance_for({"write_file"}, "work")
    # Substrings unique to VERIFICATION_GUIDANCE — "verif"/"never" also appear in the other
    # blocks, so matching those would pass with this block deleted.
    assert "GROUNDING & HONESTY" in g
    assert "NEVER fabricate or assume success" in g


def test_action_enforcement_present_in_work_mode_always():
    # The "act, don't describe" block must ship on EVERY work task — it's
    # the fix for the model narrating intent ("now type your email") instead of acting. Tool-agnostic.
    g = tg.guidance_for({"write_file"}, "work").lower()
    assert "act, don't describe" in g or "act, dont describe" in g
    assert "same turn" in g            # call the tool in the same turn, no promise of future action
    assert "ask the user to do something you can do" in g  # don't offload tool work to the user


def test_companion_mode_does_not_inject_work_guidance():
    # Companion is left untouched (its own SOUL prompt governs); guidance_for targets the work-loop.
    g = tg.guidance_for({"browser__browser_click"}, "companion")
    assert g == ""


def test_browser_guidance_constant_is_the_single_source():
    # The MCP schema enrichment must reuse THIS constant, not a private copy.
    import kotoba.core.mcp.client as mc

    assert mc._BROWSER_WORKFLOW is tg.BROWSER_GUIDANCE
    assert "snapshot" in tg.BROWSER_GUIDANCE.lower()


def test_enriched_schema_uses_shared_constant():
    import types

    import kotoba.core.mcp.client as mc

    schema = {"type": "object", "properties": {"target": {"type": "string", "description": "x"}}}
    # The full workflow now lives on the anchor tool (browser_snapshot) only — verify it reuses the constant.
    tool = types.SimpleNamespace(name="browser_snapshot", description="Capture a snapshot", inputSchema=schema)
    out = mc._to_function_schema("browser__browser_snapshot", tool)
    assert tg.BROWSER_GUIDANCE.strip()[:30] in out["description"]
