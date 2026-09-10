"""skill_list — list the skill documents Kotoba can consult."""
from __future__ import annotations

import asyncio

SCHEMA = {
    "type": "function",
    "name": "skill_list",
    "description": "List the skills (knowledge guides) you have available to consult.",
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}
BUILT_IN = False
TOOLSET = "skills"
RISK = "read"

ANNOUNCE = "Let me see what I know how to do..."
HEARTBEAT = ["Checking my skills..."]
COMPLETE = "Here's what I've got:"
FAIL = "I don't have any skills loaded right now."
EXPRESSIONS = {"focus": "thinking", "done": "happy", "fail": "confused"}


async def execute(args: dict, ctx) -> str:
    from kotoba.core import skill_docs

    skills = await asyncio.to_thread(skill_docs.list_skills)
    if not skills:
        return None
    return "\n".join(f"- {s['name']}: {s['description'] or s['title']}" for s in skills)
