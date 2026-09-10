"""Asked to summarise the last hour, she summarised the day the channel was created.

Two defects, measured live in one answer. Oldest-first with no starting point walks the channel from
its very first message, so "what did I miss" came back as the oldest hundred it ever held — and because
the content was real, the summary read as competent. Without an anchor the answer is the LAST `limit`.

And she summarised herself in the third person, about a conversation she had been in five minutes
earlier, because her own lines came back looking like another participant's.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kotoba.discord import history

ME = 9000000000000000123


class _Author:
    def __init__(self, uid: int, name: str, bot: bool = False) -> None:
        self.id = uid
        self.name = name
        self.display_name = name
        self.bot = bot


class _Msg:
    def __init__(self, author, content: str, when: datetime) -> None:
        self.author = author
        self.content = content
        self.created_at = when
        self.attachments = []
        self.reference = None
        self.edited_at = None


def _at(minutes: int) -> datetime:
    return datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def test_her_own_lines_are_marked_as_hers():
    line = history.line_for(_Msg(_Author(ME, "Kotoba", bot=True), "hola", _at(0)), ME)
    assert "YOU:" in line
    assert "Kotoba" not in line


def test_somebody_elses_line_keeps_their_name():
    line = history.line_for(_Msg(_Author(77, "wrenlow"), "hola", _at(0)), ME)
    assert "wrenlow" in line
    assert "YOU:" not in line


def test_without_the_bot_id_nothing_changes():
    """The renderer is used before the client is known; it must not guess."""
    line = history.line_for(_Msg(_Author(ME, "Kotoba", bot=True), "hola", _at(0)))
    assert "Kotoba" in line


def test_the_footer_counts_what_was_really_there():
    msgs = [_Msg(_Author(77, "wrenlow"), f"m{i}", _at(i)) for i in range(5)]
    out = history.render(msgs, channel="general", total=214, me_id=ME)
    assert "214 messages" in out
    assert "5 shown" in out


def test_an_empty_stretch_says_so_rather_than_inventing():
    assert "Nothing was said" in history.render([], channel="general", total=0)


def test_the_ordering_flag_follows_the_anchor():
    """The bug in one line: oldest-first is only correct when a starting point was given."""
    import inspect

    from kotoba.tools.action import discord_read_history as tool

    src = inspect.getsource(tool._read)
    assert "oldest_first=from_the_start" in src
    assert "from_the_start = since is not None" in src


class _Channel:
    name = "general"
    id = 5

    def __init__(self, msgs) -> None:
        self._msgs = msgs

    def history(self, *, limit, after, before, oldest_first):
        pool = [m for m in self._msgs
                if (after is None or m.created_at > after) and (before is None or m.created_at < before)]
        pool.sort(key=lambda m: m.created_at, reverse=not oldest_first)

        async def gen():
            for m in pool[:limit]:
                yield m

        return gen()


class _Client:
    user = type("U", (), {"id": ME})()

    def __init__(self, channel) -> None:
        self._channel = channel

    def get_guild(self, gid):
        return None

    def get_channel(self, cid):
        return self._channel


def _read(args):
    import asyncio

    from kotoba.discord import state
    from kotoba.tools.action import discord_read_history as tool

    msgs = [_Msg(_Author(77, "wren"), f"m{i}", _at(i)) for i in range(300)]
    with state.turn(who=None, bot=_Client(_Channel(msgs)), guild=None, channel=5):
        return asyncio.run(tool.execute(args, ctx=None))


def test_hitting_the_cap_after_an_anchor_says_newer_messages_are_missing():
    out = _read({"since": _at(-1).isoformat(), "limit": 100})
    assert "m99" in out and "m299" not in out
    assert "cap, not the end" in out


def test_the_last_n_with_no_anchor_carries_no_such_warning():
    out = _read({"limit": 100})
    assert "m299" in out
    assert "cap, not the end" not in out
