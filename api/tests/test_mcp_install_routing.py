"""Routing guard: 'install an MCP for X' must be handled as BACKGROUND work (start_work → mcp_find in work
mode), NOT as companion chit-chat. The bug: she web-searched the docs and asked 'which client?' instead of
installing it into herself, never entering work mode (terminal stayed 'Nothing running yet')."""
from __future__ import annotations

from kotoba.soul.prompt import build_system_prompt


def _prompt():
    soul = {"name": "Kotoba", "personality": "warm", "address_style": "name",
            "emotional_rules": "", "quirks": ""}
    return build_system_prompt(soul, "", [], session_id="s", skills=["operating-websites — drive sites"])


def test_companion_prompt_routes_mcp_install_to_work_mode():
    p = _prompt().lower()
    # The companion prompt must connect "install an MCP / new capability" → start_work.
    assert "mcp" in p and "start_work" in p
    # It must say she installs into HERSELF, not the user's other client apps.
    assert "your own toolset" in p or "into yourself" in p or "own toolset" in p


def test_start_work_description_mentions_installing_mcp():
    import kotoba.tools.builtin.start_work as sw
    desc = sw.SCHEMA["description"].lower()
    assert "mcp" in desc and "capability" in desc


def test_oauth_service_not_yet_pending_routes_to_install_then_signin():
    """A fresh OAuth service (not connected, not waiting) must be INSTALLED via start_work (which lands
    it under 'Needs connection'), THEN the user finishes it with the Settings 'Sign in' button. The old prompt
    forbade start_work/search for ANY OAuth service, which dead-ended a clean install (nothing to sign in)."""
    p = _prompt()
    flat = " ".join(p.lower().split())  # collapse line-wrap whitespace
    # The OAuth guidance now both installs (start_work) AND points to the Settings Sign-in button.
    assert "sign in" in flat and "needs connection" in flat
    assert "install the notion mcp" in flat  # the concrete ready-it-first instruction
    assert "start_work" in flat
    # The old absolute prohibition is gone.
    assert "do not start_work / search the registry for it" not in flat
