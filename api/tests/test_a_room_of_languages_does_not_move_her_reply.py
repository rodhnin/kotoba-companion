"""One channel session carries everybody's turns, so "match the user" stops being one answer.

Live: a German voice conversation and a Spanish written one ran in the same guild minutes apart, and
her Spanish replies finished with an English sentence. The message she is answering is the only thing
that settles it, and it is per-turn — nothing about the room's other speakers may move it.
"""
from __future__ import annotations

import pytest

from kotoba.discord.authority import Actor
from kotoba.discord.bridge import ChannelSession


def _note(*, guild: int | None, owner: bool):
    session = object.__new__(ChannelSession)
    session.guild_id = guild
    who = Actor(1, guild, "Ana", "ana", owner, False, False)
    return session._where_note(who)


@pytest.mark.parametrize("guild", [7, None])
@pytest.mark.parametrize("owner", [True, False])
def test_every_discord_turn_is_told_which_language_to_answer_in(guild, owner):
    note = _note(guild=guild, owner=owner)
    assert "[LANGUAGE]" in note
    assert "language of the message you are answering" in note
    assert "Never switch part-way" in note


def test_another_speakers_language_is_named_as_irrelevant():
    """The rule has to survive the room, or it reads as "match whoever spoke last"."""
    assert "changes nothing about THIS reply" in _note(guild=7, owner=False)


@pytest.mark.parametrize("guild", [7, None])
def test_she_is_told_she_is_in_a_chat_and_not_a_terminal(guild):
    """The written rules describe a terminal, because that is what they were written for. Here that
    is false, and its closing-offer register turned every reply into a helpdesk menu."""
    note = _note(guild=guild, owner=False)
    assert "[HOW YOU WRITE HERE]" in note
    assert "not a terminal" in note
    assert "if you want, I can" in note
    # The first cut of this note said "say your thing and STOP", which bought terseness by spending
    # the warmth. Pruning the service register may never become permission to be curt.
    assert "not permission to be curt" in note


def _drain_all(chunks, *, keep_tags):
    """Run the real filter chain the Discord surface uses, and return what a listener gets."""
    import asyncio

    from kotoba.core import stream as _stream
    from kotoba.discord.bridge import ChannelSession

    session = object.__new__(ChannelSession)
    q: asyncio.Queue = asyncio.Queue()
    for c in chunks:
        q.put_nowait(c)
    q.put_nowait(_stream.DONE_SENTINEL)
    said: list[str] = []
    return asyncio.run(session._drain(q, said, None, None, keep_tags=keep_tags))


def test_a_voice_turn_does_not_read_the_link_out_loud():
    """The web's spoken chain has always stripped URLs; this one never did, so a cited source came
    out of her mouth as the whole address, tracking parameter and all."""
    spoken = _drain_all(["Mis favoritos son estos ",
                         "((https://www.forbes.com/sites/x/article/best-fragrance/?utm_source=openai))"],
                        keep_tags=True)
    assert "http" not in spoken and "forbes" not in spoken.lower()
    assert "Mis favoritos" in spoken


def test_a_written_turn_keeps_the_link():
    """In a chat the link is the useful half — it is clickable, and taking it away helps nobody."""
    written = _drain_all(["Mira esto: https://example.com/a"], keep_tags=False)
    assert "https://example.com/a" in written


def test_a_citation_leaves_no_empty_brackets_behind():
    """The spoken chain takes the address out and the citation's own brackets stay, so a sentence
    ends in a bare "()" — in the voice caption, which is the one place nobody was cleaning."""
    from kotoba.discord.text import tidy_links

    assert tidy_links("no propaganda barata. ()") == "no propaganda barata."
    assert tidy_links("uno se pone la banderita. (())") == "uno se pone la banderita."
    assert tidy_links("texto (con parentesis) intacto") == "texto (con parentesis) intacto"
    assert tidy_links("((https://x.test/a?utm_source=openai))") == "<https://x.test/a>"
