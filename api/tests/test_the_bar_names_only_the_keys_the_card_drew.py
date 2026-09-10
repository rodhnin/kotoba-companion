"""The bar under an approval card, and the two /help rows that said the same thing beside it.

The rail is built FROM the card: `a` comes and goes with one grant, `t` with another — telling someone
to press a key never drawn teaches them to distrust the rail. The bar hard-coded "y, a, n or ?", wrong
both ways: on a dangerous command where both grants are withheld it named two keys the rail never drew,
and on an ordinary one it dropped `t`, which the rail had. `rm -rf` is the case that matters here.

The oracle is the card's own flash, read rather than restated in a second table that would drift the
same way. /help carries the same fixed list twice and cannot be checked this way — it has no card in
hand, so its list is a promise nothing verifies."""
from __future__ import annotations

import re
import time

import pytest

from kotoba.cli import slash
from kotoba.cli.render import cards, footer
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.theme import GLYPHS_UNICODE

SHAPES = [(a, t) for a in (True, False) for t in (True, False)]


def caps_for(width: int = 100) -> Caps:
    return Caps(color="none", background="dark", unicode=True, width=width, height=40,
                g=dict(GLYPHS_UNICODE))


def a_card(can_always: bool, can_always_exact: bool) -> cards.Approval:
    return cards.Approval("npm run build", "", "npm", can_always=can_always,
                          can_always_exact=can_always_exact)


def rail_keys(caps: Caps, card: cards.Approval) -> str:
    """The keys THIS card drew, in the card's own words: `_key_rows` names them when it discards a
    keystroke, which is the one place the rail says its own contents out loud."""
    was, card.flash_until = card.flash_until, time.monotonic() + 5
    try:
        flat = "\n".join(row.plain for row in cards.approval_rows(caps, card, 100))
    finally:
        card.flash_until = was
    found = re.search(r"that key isn't one of them — (.+)", flat)
    assert found, flat
    return found.group(1).strip()


# --- the bar ---------------------------------------------------------------------------------------

def test_the_bar_names_exactly_the_keys_the_rail_drew_on_every_shape_of_card():
    caps = caps_for()
    seen = set()
    for can_a, can_t in SHAPES:
        card = a_card(can_a, can_t)
        keys = rail_keys(caps, card)
        seen.add(keys)
        hint = footer.box_hint(caps, footer.State(approval=card), 96)
        assert hint == f"the card above is waiting — {keys}", (can_a, can_t, hint, keys)
    # All four shapes really are different lists, so the sweep above cannot pass by coincidence.
    assert seen == {"y, a, t, n or ?", "y, a, n or ?", "y, t, n or ?", "y, n or ?"}


def test_the_card_the_rules_exist_for_is_the_one_the_bar_offered_a_key_it_never_drew():
    """`rm -rf` withholds both grants through the live backend rules, not through a flag set by hand."""
    from kotoba.core.approval import command_family, persistable, persistable_exact

    cmd = "rm -rf /tmp/build"
    family = command_family(cmd)
    assert (persistable(cmd, family), persistable_exact(cmd, family)) == (False, False)

    caps = caps_for()
    card = cards.Approval(cmd, "recursive-delete", family, can_always=False, can_always_exact=False)
    assert rail_keys(caps, card) == "y, n or ?"
    assert footer.box_hint(caps, footer.State(approval=card), 96) == \
        "the card above is waiting — y, n or ?"


def test_the_short_twin_is_the_same_list_and_no_width_ellipsises_it_away():
    for can_a, can_t in SHAPES:
        card = a_card(can_a, can_t)
        keys = rail_keys(caps_for(), card)
        for room in range(0, 60):
            hint = footer.box_hint(caps_for(room + 4), footer.State(approval=card), room)
            assert hint in (f"the card above is waiting — {keys}", keys, ""), (can_a, can_t, room)
            assert "…" not in hint


def test_the_two_key_rail_is_untouched():
    """`Confirm` has no grants to withhold — its two keys are the card's own labels."""
    caps = caps_for()
    confirm = footer.State(confirm=cards.Confirm("sandbox local → none", "why", "y do it", "n leave"))
    assert footer.box_hint(caps, confirm, 96) == "the card above is waiting — y or n"


# --- the same list, written down where no card can be read ------------------------------------------

def test_no_static_help_row_hands_out_a_key_list_a_real_card_can_contradict():
    """A key list belongs to ONE card. /help is printed at a prompt with no card open, so a row that
    ends in one is stating as general what is true of at most a quarter of the cards she raises."""
    caps = caps_for()
    lists = set()
    for can_a, can_t in SHAPES:
        keys = rail_keys(caps, a_card(can_a, can_t))
        lists.add(keys)
        lists.add(keys.replace(", ", " ").replace(" or ", " "))

    for section, pairs in slash.HELP:
        for left, right in pairs:
            assert left not in lists, (section, left)
            for one in lists:
                assert not right.endswith(one), (section, right)


def test_and_help_still_says_an_approval_is_answered_by_a_keypress():
    """What the row is FOR survives the repair: the keys are on the card, and no enter is needed."""
    joined = " ".join(f"{left} {right}" for _, pairs in slash.HELP for left, right in pairs)
    assert "no enter" in joined
    assert "approval" in joined


@pytest.mark.parametrize("can_a,can_t", SHAPES)
def test_the_bar_and_the_flash_are_one_sentence_apart_and_never_two_tables(can_a, can_t):
    """Belt and braces on the sweep above: whatever `_key_rows` says, the bar says the tail of it."""
    caps = caps_for()
    card = a_card(can_a, can_t)
    assert footer.box_hint(caps, footer.State(approval=card), 96).endswith(rail_keys(caps, card))
