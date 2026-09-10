"""She was mentioned and said nothing, with no error anywhere.

Measured live: `@Kotoba holaaaaa` arrived as 32 characters of content with `Message.mentions` EMPTY.
discord.py resolves that list against the guild's member cache and drops a miss in silence, so the
one signal that decides whether she answers at all can be absent from a message that plainly
mentions her. The raw token in the content cannot go missing the same way.

The rest of the routing is here too, because "does she answer this" is the first thing a stranger
meets and a bot that answers everything is a bot people mute.
"""
from __future__ import annotations

import time

from kotoba.discord.client import mentions_me, wants_reply

ME = 9000000000000000001
MINE = frozenset()


class _Me:
    id = ME


class _Author:
    def __init__(self, uid: int, bot: bool = False) -> None:
        self.id = uid
        self.bot = bot


class _Channel:
    def __init__(self, cid: int = 10) -> None:
        self.id = cid


class _Msg:
    def __init__(self, content: str, *, mentions=(), guild=object(), author=None,
                 channel=None, reference=None, everyone=False) -> None:
        self.content = content
        self.mentions = list(mentions)
        self.guild = guild
        self.author = author or _Author(77)
        self.channel = channel or _Channel()
        self.reference = reference
        self.mention_everyone = everyone


def test_a_mention_with_an_empty_cache_is_still_a_mention():
    """The exact live failure: content carries the token, the parsed list is empty."""
    msg = _Msg(f"<@{ME}> holaaaaa", mentions=[])
    assert mentions_me(msg, _Me())
    assert wants_reply(msg, me=_Me(), home=MINE, last_spoke={})


def test_the_nickname_form_of_the_token_counts_too():
    assert mentions_me(_Msg(f"<@!{ME}> hey", mentions=[]), _Me())


def test_someone_elses_mention_is_not_hers():
    assert not mentions_me(_Msg("<@999> hey", mentions=[]), _Me())


def test_a_plain_line_in_a_busy_channel_is_left_alone():
    assert not wants_reply(_Msg("qué tal ayer"), me=_Me(), home=MINE, last_spoke={})


def test_she_never_answers_another_bot():
    msg = _Msg(f"<@{ME}> hola", author=_Author(5, bot=True))
    assert not wants_reply(msg, me=_Me(), home=MINE, last_spoke={})


def test_an_empty_message_is_not_a_turn():
    assert not wants_reply(_Msg("   ", mentions=[]), me=_Me(), home=MINE, last_spoke={})


def test_a_direct_message_is_always_for_her():
    assert wants_reply(_Msg("hola", guild=None), me=_Me(), home=MINE, last_spoke={})


def test_a_home_channel_answers_without_the_name():
    msg = _Msg("hola", channel=_Channel(42))
    assert wants_reply(msg, me=_Me(), home=frozenset({42}), last_spoke={})


def test_the_attention_window_keeps_one_conversation_going():
    """A back-and-forth should not need her name on every line, and must not outlive the moment."""
    fresh = {10: (77, time.monotonic())}
    stale = {10: (77, time.monotonic() - 600)}
    other = {10: (999, time.monotonic())}
    assert wants_reply(_Msg("¿y eso?"), me=_Me(), home=MINE, last_spoke=fresh)
    assert not wants_reply(_Msg("¿y eso?"), me=_Me(), home=MINE, last_spoke=stale)
    assert not wants_reply(_Msg("¿y eso?"), me=_Me(), home=MINE, last_spoke=other)


def test_the_window_closes_the_moment_somebody_else_speaks():
    """Four people were talking. She answered one of them once, and from then on every line he
    wrote landed as if it had been addressed to her — including two that were plainly not.

    The window assumed "his next line is probably still for me", which holds in a chat of two and
    is false in a room. Someone else speaking is the room moving on without her."""
    fresh = {10: (77, time.monotonic())}
    interrupted = wants_reply(_Msg("ese hueco 2d le falta profundidad"),
                              me=_Me(), home=MINE, last_spoke=fresh, prev_author=555)
    assert not interrupted


def test_the_window_survives_her_own_reply_in_between():
    """Her answer is the other half of the same conversation, not an interruption of it."""
    fresh = {10: (77, time.monotonic())}
    assert wants_reply(_Msg("¿y eso?"), me=_Me(), home=MINE, last_spoke=fresh, prev_author=ME)


def test_an_explicit_mention_survives_an_interruption():
    """Continuity closes the window, never the door."""
    msg = _Msg(f"<@{ME}> oye")
    assert wants_reply(msg, me=_Me(), home=MINE, last_spoke={}, prev_author=555)


def test_an_announcement_to_the_room_does_not_ride_the_attention_window():
    """`@everyone Kotoba no respondes aca?` was answered because she had spoken to him a minute
    before. An announcement to the whole room is not the next line of anyone's conversation, and a
    bot that answers every `@everyone` is a bot the server removes."""
    fresh = {10: (77, time.monotonic())}
    assert not wants_reply(_Msg("hola a todos", everyone=True),
                           me=_Me(), home=MINE, last_spoke=fresh)


def test_naming_her_inside_an_announcement_still_reaches_her():
    """The window is what closes, not the door: an explicit mention always counts."""
    msg = _Msg(f"<@{ME}> mira esto", everyone=True)
    assert wants_reply(msg, me=_Me(), home=MINE, last_spoke={})


class _Role:
    def __init__(self, rid: int) -> None:
        self.id = rid


class _Guild:
    id = 500

    def __init__(self, roles=()) -> None:
        self.me = type("M", (), {"roles": list(roles)})()


ROLE = 9000000000000000002


def test_the_integration_role_carries_her_name_too():
    """Typing her name offers two things: the bot, and the role the integration created with the
    same name. Half the room picks the role, whose token is neither the user token nor in `mentions`.
    Measured live: a member mentioned her four times and she never answered one of them."""
    msg = _Msg(f"<@&{ROLE}> ejecuta el comando", mentions=[],
               guild=_Guild(roles=[_Role(ROLE)]))
    assert mentions_me(msg, _Me())
    assert wants_reply(msg, me=_Me(), home=MINE, last_spoke={})


def test_a_role_she_does_not_wear_is_not_her():
    msg = _Msg("<@&999> oigan todos", mentions=[], guild=_Guild(roles=[_Role(ROLE)]))
    assert not mentions_me(msg, _Me())


def test_the_everyone_role_is_never_a_mention_of_her():
    """The default role shares the guild id, and it is on every member including her."""
    guild = _Guild(roles=[_Role(500), _Role(ROLE)])
    assert not mentions_me(_Msg("<@&500> hola", mentions=[], guild=guild), _Me())


def test_a_guild_with_no_roles_cached_does_not_crash():
    assert not mentions_me(_Msg("hola", mentions=[], guild=_Guild()), _Me())


def test_a_reply_to_something_of_hers_is_for_her():
    class _Ref:
        resolved = type("M", (), {"author": _Me()})()

    msg = _Msg("y eso por qué", reference=_Ref())
    msg.mentions = []
    assert wants_reply(msg, me=_Me(), home=MINE, last_spoke={})
