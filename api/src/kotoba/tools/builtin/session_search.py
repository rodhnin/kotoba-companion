"""session_search — recall past conversations via SQLite FTS5 over turns."""
from __future__ import annotations

from datetime import datetime, timezone

SCHEMA = {
    "type": "function",
    "name": "session_search",
    "description": "Search past conversations for something the user mentioned before.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keywords to search past turns for."}
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "memory"
RISK = "read"

ANNOUNCE = "Did we talk about this before? Let me check..."
HEARTBEAT = ["Looking back through our chats...", "I think it was recent..."]
COMPLETE = "Okay — here's what I remember about that:"
FAIL = "I couldn't dig that one up just now. Want to remind me how it went?"


# Below this length a trailing `*` costs far more than it buys: `"la"` matches 212 turns, `"la" *` 937.
# At 4+ the wildcard is what makes an inflected language searchable ("latencia" → "latencias").
_PREFIX_MIN = 4


def _sanitize(query: str) -> str:
    """FTS5 MATCH expression: every token quoted (so `AND`, `NEAR`, `(`, `*` are literal strings, never
    operators), and every token of _PREFIX_MIN chars or more turned into a PREFIX query.

    Spanish inflects by suffix, so an exact-token index loses the plural of every singular the user
    types: `latencia` found 0 turns while the word sat inside `latencias` in 2 of them. `"tok" *` is
    FTS5's prefix form and works on the existing index — no migration, no re-index.
    """
    tokens = [t for t in query.replace('"', " ").split() if t]
    return " ".join(f'"{t}" *' if len(t) >= _PREFIX_MIN else f'"{t}"' for t in tokens)


_MAX_CONTENT = 200


def _parse_ts(value) -> datetime | None:
    """SQLite CURRENT_TIMESTAMP is naive UTC 'YYYY-MM-DD HH:MM:SS'; tolerate ISO 'T'/'Z'/fractions too."""
    if not value:
        return None
    s = str(value).strip().replace("T", " ").rstrip("Z").split(".")[0]
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(s[:size], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _age(then: datetime, now: datetime) -> str:
    """Relative age in words. A bare date is not enough: she has to KNOW whether a hit is
    yesterday's plan or a three-month-old aside before she leans on it."""
    days = (now.date() - then.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 31:
        weeks = days // 7
        return "a week ago" if weeks == 1 else f"{weeks} weeks ago"
    if days < 365:
        months = max(1, days // 30)
        return "a month ago" if months == 1 else f"{months} months ago"
    years = days // 365
    return "a year ago" if years == 1 else f"{years} years ago"


def _speaker(role: str) -> str:
    return "you said" if (role or "user") == "user" else "I said"


def _format_hit(row: dict, now: datetime) -> str:
    when = _parse_ts(row.get("created_at")) or _parse_ts(row.get("started_at"))
    stamp = f"{when:%Y-%m-%d} ({_age(when, now)})" if when else "date unknown"
    session = (row.get("session_id") or "")[:8] or "unknown"
    content = " ".join((row.get("content") or "").split())
    if len(content) > _MAX_CONTENT:
        content = content[:_MAX_CONTENT].rstrip() + "…"
    return f"- {stamp}, session {session} — {_speaker(row.get('role'))}: {content}"


async def execute(args: dict, ctx) -> str:
    query = (args or {}).get("query", "").strip()
    if not query:
        return "No search terms were given."
    rows = await ctx.db.search_turns(_sanitize(query), limit=5)
    if not rows:
        return "Nothing came up in our past conversations about that."
    now = datetime.now(timezone.utc)
    return "\n".join(_format_hit(r, now) for r in rows)
