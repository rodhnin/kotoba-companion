"""She refused to hand out a role she could hand out, and never called the tool that would have.

The check was right by then and the data was right: `above_me` said False. What lied was the list she
read it from. Roles tie on position constantly, and sorting a tie group by that integer leaves an
arbitrary order — so a role she outranked was printed ABOVE her own, under a heading that said
"highest first". She believed the order, which was the reasonable thing to do, and stopped.

A correct value nobody can see loses to an incorrect layout everybody can. So the list is ordered the
way Discord orders it, her own role is marked in it, and the rule is spelled out underneath rather
than left to be inferred from position.
"""
from __future__ import annotations

from kotoba.discord import guild as guild_mod


class _Role:
    """Ties on position, orders by age — the real rule, and the one an integer compare loses."""

    def __init__(self, name, position, age, managed=False):
        self.name = name
        self.id = age
        self.position = position
        self.managed = managed
        self.members = []

    def __lt__(self, other):
        if self.position != other.position:
            return self.position < other.position
        return self.id > other.id

    def __ge__(self, other):
        return not self < other


class _Guild:
    id = 500
    name = "srv"
    owner_id = 1
    member_count = 3

    def __init__(self):
        self.mine = _Role("Kotoba", 1, age=100, managed=True)
        self.above = _Role("Admin", 1, age=50)          # older, so higher
        self.below = _Role("Founders", 1, age=900)        # younger, so lower
        self.roles = [_Role("@everyone", 0, age=1), self.below, self.mine, self.above]
        self.me = type("M", (), {"top_role": self.mine})()
        self.channels = []


def _snap():
    return guild_mod.snapshot(_Guild(), with_overwrites=False)


def test_the_order_is_discords_own_not_the_position_number():
    names = [r["name"] for r in _snap()["roles"]]
    assert names.index("Admin") < names.index("Kotoba") < names.index("Founders")


def test_a_role_she_outranks_is_not_marked_out_of_reach():
    by_name = {r["name"]: r for r in _snap()["roles"]}
    assert by_name["Founders"]["above_me"] is False
    assert by_name["Admin"]["above_me"] is True


def test_her_own_role_is_marked_so_the_boundary_is_visible():
    text = guild_mod.describe(_snap(), complete=True)
    assert "Kotoba  [<- THIS IS YOU" in text


def test_the_rule_is_written_out_not_left_to_be_inferred():
    text = guild_mod.describe(_snap(), complete=True)
    assert "BELOW your own role is yours" in text
    assert "trust these marks rather than the numbers" in text


def test_a_role_she_can_use_carries_no_warning_beside_it():
    """A stray “out of your reach” beside a usable role is the whole defect, in one line."""
    for line in guild_mod.describe(_snap(), complete=True).splitlines():
        if line.strip().startswith("Founders"):
            assert "out of your reach" not in line
            break
    else:
        raise AssertionError("the role is missing from the list entirely")
