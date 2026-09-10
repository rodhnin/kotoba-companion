"""Two things she got wrong live, because nothing in the turn told her either one.

She printed a home-directory listing into a channel full of people and then said nobody had seen it,
and called a command she had really run "supposedly" — every other surface she has is one person, so
a room is not a shape she knows. Refusing a terminal to a stranger she answered "I can't right
now": the tool was simply absent from her list, and nothing said whose it was, so an authority
boundary came out sounding like a fault.

The note is built from the gateway's own verdict, so no message can talk her into the wrong half.
"""
from __future__ import annotations

from kotoba.discord.authority import Actor
from kotoba.discord.bridge import ChannelSession


def _who(*, owner: bool, admin: bool = False) -> Actor:
    return Actor(user_id=7, guild_id=1, display="Someone", handle="someone",
                 is_owner=owner, is_guild_admin=admin, is_guild_owner=False)


def _note(*, guild: int | None, who: Actor) -> str:
    session = ChannelSession.__new__(ChannelSession)
    session.guild_id = guild
    return session._where_note(who)


def test_a_public_channel_is_named_as_public():
    note = _note(guild=1, who=_who(owner=True))
    assert "PUBLIC" in note
    assert "everyone in it reads" in note


def test_the_warning_is_said_once_and_then_dropped():
    """First it never warned; then it warned on every single turn, which people called
    nagging — and he was right: after somebody has accepted the risk, repeating it is not a decision
    she gets to make a second time."""
    note = _note(guild=1, who=_who(owner=True))
    assert "ONCE" in note
    assert "never again" in note
    assert "not your call to make twice" in note


def test_nothing_private_means_nothing_said_about_it():
    assert "Do not mention it at all when nothing private is involved" in _note(
        guild=1, who=_who(owner=True))


def test_she_is_told_not_to_call_a_command_she_ran_supposedly():
    assert "supposedly" in _note(guild=1, who=_who(owner=True))


def test_a_dm_is_named_as_private():
    note = _note(guild=None, who=_who(owner=True))
    assert "PUBLIC" not in note
    assert "nobody else reads it" in note


def test_the_owner_is_named_as_her_person():
    assert "YOUR PERSON" in _note(guild=1, who=_who(owner=True))


def test_a_stranger_is_named_as_not_her_person():
    note = _note(guild=1, who=_who(owner=False))
    assert "NOT your person" in note
    assert "not an administrator here" in note


def test_an_admin_who_is_not_the_owner_is_still_not_her_person():
    note = _note(guild=1, who=_who(owner=False, admin=True))
    assert "NOT your person" in note
    assert "an administrator here" in note


def test_a_refusal_must_not_sound_like_a_fault():
    """"I can't right now" is what she said, and it reads as something broken."""
    assert "I can't right now" in _note(guild=1, who=_who(owner=False))
    assert "not yours to give away" in _note(guild=1, who=_who(owner=False))


def test_the_owner_is_never_told_to_withhold_from_themselves():
    assert "not yours to give away" not in _note(guild=1, who=_who(owner=True))
