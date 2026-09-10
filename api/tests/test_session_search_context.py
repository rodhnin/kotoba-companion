"""Live QA found session_search returning `content[:200]` and nothing else.

With no date and no session on a hit she cannot tell yesterday's plan from a three-month-old aside, so
a correct recall still came out as a vague "I didn't find anything useful". The row already carried
`turns.created_at` and `turns.session_id` — the query selected them and the formatter dropped them.

These tests run against a REAL sqlite FTS5 index through the REAL Database, and backdate `created_at`
so the relative-age wording is exercised on actual stored timestamps, not on a stub clock.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import kotoba.tools.builtin.session_search as ss
from kotoba.db.database import Database


class _Ctx:
    def __init__(self, db):
        self.db = db


_n = 0


def _search(tmp_path, query: str, corpus, sessions=None):
    """corpus: list of (session_id, role, text, days_ago)."""
    global _n
    _n += 1

    async def go():
        db = Database("sqlite:///" + str(tmp_path / f"ctx{_n}.db"))
        await db.connect()
        try:
            for sid in sessions or sorted({c[0] for c in corpus}):
                await db.conn.execute("INSERT OR IGNORE INTO sessions (id) VALUES (?)", (sid,))
            for sid, role, text, days_ago in corpus:
                await db.insert_turn(sid, role, text)
                when = (datetime.now(timezone.utc) - timedelta(days=days_ago))
                await db.conn.execute(
                    "UPDATE turns SET created_at = ? WHERE content = ?",
                    (when.strftime("%Y-%m-%d %H:%M:%S"), text),
                )
            await db.conn.commit()
            return await ss.execute({"query": query}, _Ctx(db))
        finally:
            await db.close()

    return asyncio.run(go())


TODAY = [("s1", "user", "La latencia de la voz con flash", 0)]


def test_a_hit_carries_an_absolute_date(tmp_path):
    out = _search(tmp_path, "latencia", TODAY)
    assert datetime.now(timezone.utc).strftime("%Y-%m-%d") in out, out


def test_a_hit_carries_its_session(tmp_path):
    out = _search(tmp_path, "latencia", TODAY)
    assert "session s1" in out, out


def test_a_hit_still_carries_the_content(tmp_path):
    assert "flash" in _search(tmp_path, "latencia", TODAY)


@pytest.mark.parametrize("days_ago,expected", [
    (0, "today"),
    (1, "yesterday"),
    (3, "3 days ago"),
    (9, "a week ago"),
    (20, "2 weeks ago"),
    (45, "a month ago"),
    (95, "3 months ago"),
    (500, "a year ago"),
])
def test_relative_age_places_the_hit_in_time(tmp_path, days_ago, expected):
    """The failure mode was not a missing date — it was not knowing whether the hit was recent."""
    out = _search(tmp_path, "latencia", [("s1", "user", "La latencia de la voz", days_ago)])
    assert expected in out, out


def test_yesterday_and_three_months_ago_are_distinguishable(tmp_path):
    """The exact QA complaint: two hits on the same word must not read identically."""
    out = _search(tmp_path, "latencia", [
        ("s1", "user", "La latencia de ayer estaba fatal", 1),
        ("s2", "user", "La latencia de hace tiempo era otra cosa", 95),
    ])
    assert "yesterday" in out and "3 months ago" in out, out
    assert "session s1" in out and "session s2" in out, out


def test_who_said_it_is_marked(tmp_path):
    out = _search(tmp_path, "latencia", [
        ("s1", "user", "Oye, la latencia sigue mal", 2),
        ("s1", "assistant", "Ya bajé la latencia con flash", 2),
    ])
    assert "you said:" in out and "I said:" in out, out


def test_content_is_still_capped_and_single_line(tmp_path):
    long_turn = "latencia " + ("palabra " * 200)
    out = _search(tmp_path, "latencia", [("s1", "user", long_turn, 0)])
    assert len(out.splitlines()) == 1, out
    assert "…" in out
    assert len(out) < 400, len(out)


def test_a_multiline_turn_does_not_break_the_list(tmp_path):
    out = _search(tmp_path, "latencia", [("s1", "user", "La latencia\nde la voz\nbaja", 0)])
    assert len(out.splitlines()) == 1, out
    assert "La latencia de la voz baja" in out


def test_no_hits_is_unchanged(tmp_path):
    assert "Nothing came up" in _search(tmp_path, "blender", TODAY)


def test_empty_query_is_unchanged():
    assert "No search terms" in asyncio.run(ss.execute({"query": "   "}, None))


# --- the formatter in isolation ----------------------------------------------------------------------

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def test_a_row_with_no_timestamp_still_formats():
    """Old rows predate nothing here, but a NULL must never take the tool down."""
    line = ss._format_hit({"content": "algo", "session_id": "abcdef1234", "role": "user"}, NOW)
    assert "date unknown" in line and "session abcdef" in line


def test_started_at_is_the_fallback_when_the_turn_has_no_created_at():
    line = ss._format_hit(
        {"content": "algo", "session_id": "s1", "role": "user", "started_at": "2026-07-27 09:00:00"},
        NOW,
    )
    assert "2026-07-27" in line and "yesterday" in line


@pytest.mark.parametrize("raw", [
    "2026-07-27 09:00:00",
    "2026-07-27T09:00:00",
    "2026-07-27T09:00:00Z",
    "2026-07-27 09:00:00.123456",
    "2026-07-27",
])
def test_timestamp_shapes_sqlite_and_iso_both_parse(raw):
    assert ss._parse_ts(raw).date() == datetime(2026, 7, 27).date()


def test_a_garbage_timestamp_is_not_fatal():
    assert ss._parse_ts("not a date") is None
    assert ss._parse_ts(None) is None


def test_session_id_is_shortened_not_dropped():
    line = ss._format_hit(
        {"content": "x", "session_id": "0cd10955aabbccdd", "role": "user",
         "created_at": "2026-07-28 09:00:00"},
        NOW,
    )
    assert "session 0cd10955" in line
