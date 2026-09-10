"""memory_recall — read the details Kotoba saved under a topic (Memory v2.1).

The system prompt always carries the Recent facts + a list of topics. When the user asks about
something whose detail lives in a topic file, she calls this to pull that topic's facts (and its path,
for verification). Read-only and jailed to her own memory store, so it's companion-safe.
"""
from __future__ import annotations

import asyncio

SCHEMA = {
    "type": "function",
    "name": "memory_recall",
    "description": (
        "Recall or SEARCH what you've saved about the user. Pass `query` — a word or short phrase (e.g. "
        "'facebook', 'railway project') — to search across EVERYTHING (it finds the facts even if they're "
        "filed under a topic you wouldn't guess). Or pass `topic` to read one topic's facts. Omit both to "
        "list the topics you have. Use this whenever the user asks about something the prompt only summarized."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A word/phrase to search memory for, across all topics."},
            "topic": {"type": "string", "description": "A specific topic to read (e.g. 'work'). Omit to search/list."},
        },
        "required": [],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "memory"
RISK = "read"

ANNOUNCE = "Let me remember what I know about that..."
HEARTBEAT = ["Looking through what you've told me...", "One sec..."]
COMPLETE = "Right, here's what I've got:"
FAIL = "I don't think I've saved anything about that yet."
EXPRESSIONS = {"focus": "thinking", "done": "happy", "fail": "confused"}


async def execute(args: dict, ctx) -> str:
    from kotoba.core import user_memory

    args = args or {}
    query = (args.get("query") or "").strip()
    topic = (args.get("topic") or "").strip()

    # Free-text search across all topics (preferred when the user asks "what do you know about X").
    if query and not topic:
        hits = await asyncio.to_thread(user_memory.search_facts, query)
        if hits:
            lines = "\n".join(f"- {h['fact']} ({h['topic']})" for h in hits)
            return f"What I remember about '{query}':\n{lines}"
        topics = await asyncio.to_thread(user_memory.list_topics)
        if topics:
            return f"I don't have anything on '{query}'. Topics I do have: " + ", ".join(s for s, _ in topics) + "."
        return None

    if not topic:
        topics = await asyncio.to_thread(user_memory.list_topics)
        if not topics:
            return "I haven't saved anything about you yet."
        return "Topics I remember: " + ", ".join(f"{slug} ({n})" for slug, n in topics)

    # Read a specific topic. recall() adds the cross-topic hits beside it — a topic name is a starting
    # point, not the whole answer — and falls back to a pure search when the topic itself is empty.
    result = await asyncio.to_thread(user_memory.recall, topic)
    if result["facts"]:
        where = f"From {result['path']}:" if result.get("path") else f"Searching for '{topic}', I found:"
        lines = [where] + [f"- {f}" for f in result["facts"]]
        if result.get("related"):
            lines.append(f"Filed elsewhere but related to '{topic}':")
            lines += [f"- {h['fact']} ({h['topic']})" for h in result["related"]]
        return "\n".join(lines)
    avail = result.get("available_topics") or []
    if avail:
        return f"Nothing under '{topic}' yet. I do have: {', '.join(avail)}."
    return None  # nothing saved → graceful FAIL line
