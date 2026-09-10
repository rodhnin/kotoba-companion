"""Whoever spoke first in a channel must not become its permanent approver.

Two independent ways a card that runs on the operator's machine reached somebody who is not the
operator: the asker was checked before the card's own ownership, and a command line the shell could
not parse produced no family at all — which is exactly the card that most needed protecting.
"""
from __future__ import annotations

import pytest

from kotoba.cli.approvals import Card
from kotoba.discord import cards


def _host_card(family: str) -> Card:
    """What the gate raises for a command about to run on this machine: no notice of its own."""
    return Card(request_id="r1", mode="approval", label="rm -rf /tmp/x", detail="", family=family)


def _surface_card() -> Card:
    """What this surface raises for itself: no family, and facts to show."""
    return Card(request_id="r2", mode="approval", label="Change the server?", detail="",
                family="", notice={"head": "Change the server?", "facts": [], "surface": "discord"})


def test_a_host_card_is_the_owners_even_with_no_family():
    assert cards.owner_only(_host_card("rm")) is True
    assert cards.owner_only(_host_card("")) is True, "an unparseable command line is still a command"


def test_a_card_this_surface_raised_is_not_owner_only():
    assert cards.owner_only(_surface_card()) is False


@pytest.mark.parametrize("family", ["rm", ""])
def test_the_asker_cannot_answer_a_host_card(family):
    """The asker shortcut exists so somebody can answer their own question. It may never outrank the
    rule that a command on somebody else's machine is theirs alone to allow."""
    card = _host_card(family)
    assert cards.may_answer(card, 111, asker=111, owner=999, is_admin=False) is False
    assert cards.may_answer(card, 111, asker=111, owner=999, is_admin=True) is False
    assert cards.may_answer(card, 999, asker=111, owner=999, is_admin=False) is True


def test_the_asker_may_still_answer_their_own_surface_card():
    card = _surface_card()
    assert cards.may_answer(card, 111, asker=111, owner=999, is_admin=False) is True
    assert cards.may_answer(card, 222, asker=111, owner=999, is_admin=True) is True
    assert cards.may_answer(card, 222, asker=111, owner=999, is_admin=False) is False
