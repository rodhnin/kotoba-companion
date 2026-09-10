"""She could not see who was in the server at all.

The lookup answered out of her own notes, so somebody she had never written about did not exist to
her — and what she said was that she could not MENTION them, which reads as a rule rather than as a
failed search. Measured live: asked for "Marllop" she tried four spellings, found nothing, and the
person she was looking for was sitting in the channel.

Mentioning the wrong human in public is the thing worth refusing. Being unable to look somebody up
is not, so a near miss is offered rather than swallowed.
"""
from __future__ import annotations

from kotoba.discord import people


class Member:
    def __init__(self, name, display=None, bot=False):
        self.name = name
        self.display_name = display or name
        self.global_name = None
        self.nick = None
        self.bot = bot
        self.id = 9_000_000_000_000_000_000 + abs(hash(name)) % 10**6

    @property
    def mention(self):
        return f"<@{self.id}>"


class Guild:
    def __init__(self, members):
        self.members = members


class Client:
    def __init__(self, members):
        self._guild = Guild(members)

    def get_guild(self, gid):
        return self._guild

    def get_user(self, uid):
        return None


ROOM = [Member("marlopix", "Marlopix"), Member("orsino", "Orsino"),
        Member("quillon", "Quillon"), Member("kotoba", "Kotoba", bot=True)]


def test_a_partial_name_lands_when_only_one_person_can_be_meant():
    client = Client(ROOM)
    assert people.resolve_user(client, 1, "Marlo").name == "marlopix"
    assert people.resolve_user(client, 1, "@Marlopix").name == "marlopix"
    assert people.resolve_user(client, 1, "Orsino").name == "orsino"


def test_a_misspelling_is_offered_rather_than_assumed():
    """Resolving it outright would put a mention of a real person in a public channel off a guess."""
    client = Client(ROOM)
    assert people.resolve_user(client, 1, "Marllop") is None
    assert people.nearest(client, 1, "Marllop")[0].name == "marlopix"


def test_two_plausible_people_is_a_question_not_a_guess():
    client = Client([Member("rowan1"), Member("rowan2")])
    assert people.resolve_user(client, 1, "rowan") is None
    assert [m.name for m in people.nearest(client, 1, "rowan")] == ["rowan1", "rowan2"]


def test_the_near_misses_are_offered():
    client = Client(ROOM)
    close = people.nearest(client, 1, "Marllop")
    assert close and close[0].name == "marlopix"


def test_a_name_nothing_resembles_offers_nothing():
    assert people.nearest(Client(ROOM), 1, "zzzqqq") == []


def test_the_roster_is_the_real_room_minus_the_bots():
    assert [m.name for m in people.roster(Client(ROOM), 1)] == ["marlopix", "orsino", "quillon"]
