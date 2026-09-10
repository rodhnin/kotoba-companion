"""skill_view — load the full text of a skill document to follow its guidance."""
from __future__ import annotations

import asyncio

SCHEMA = {
    "type": "function",
    "name": "skill_view",
    "description": (
        "Read the full guidance of one of your skills by name (use skill_list first to see names). "
        "Follow what it says when doing the related task."
    ),
    "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The skill name, e.g. 'research'."}},
        "required": ["name"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "skills"
RISK = "read"

ANNOUNCE = "Let me brush up on that..."
HEARTBEAT = ["Reading through it..."]
COMPLETE = "Got it, I know how to approach this now."
FAIL = "I don't have a skill by that name yet."
EXPRESSIONS = {"focus": "thinking", "done": "determined", "fail": "confused"}


async def execute(args: dict, ctx) -> str:
    from kotoba.core import skill_docs

    name = (args or {}).get("name", "").strip()
    if not name:
        return None
    body = await asyncio.to_thread(skill_docs.view_skill, name)
    return body  # None → graceful FAIL line
