"""The "⛔ what you genuinely CANNOT do" block named a Discord tool on the terminal.

That block exists so an absent power is admitted instead of faked, and it earns its place because
every line in it is something the person could plausibly ask for right here. A Discord tool on a
terminal is not a power that is switched off; it is somewhere she is not standing. Listed anyway, it
is a line of noise on every single turn, inside the cached prefix, forever.

The rule generalises: a capability bound to a surface is only worth denying while that surface is
under her feet.
"""
from __future__ import annotations

import kotoba.tools  # noqa: F401
from kotoba.soul.prompt import build_system_prompt
from kotoba.tools.registry import schemas_for


def _prompt(available) -> str:
    return build_system_prompt({}, "", [], "s1", [], register="text", available_tools=available)


def _cannot_block(text: str) -> str:
    at = text.find("⛔")
    return "" if at < 0 else text[at:at + 2000]


def test_the_terminal_is_never_told_it_cannot_read_discord():
    here = {t.get("name") or t.get("type") for t in schemas_for("companion")}
    assert "discord_read_history" not in here, "check() should hide it outside the bot process"
    assert "iscord" not in _cannot_block(_prompt(here))


def test_inside_discord_a_switched_off_discord_tool_is_still_admitted():
    """Where the surface IS underfoot, an absent tool is a real absence and has to be owned."""
    inside = {"web_search", "discord_something_else"}
    assert "Discord" in _cannot_block(_prompt(inside))


def test_a_capability_bound_to_no_surface_is_unaffected():
    """The ordinary case keeps behaving exactly as it did."""
    assert "terminal command" in _cannot_block(_prompt({"web_search"}))
