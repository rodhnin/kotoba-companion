"""She answered a spoken question with 2 198 characters — about two minutes of uninterruptible
monologue in a channel with three people in it.

Nobody can skim speech or scroll past it, and while she talks the room stops. The written surface has
no such cost, so the rule cannot live in the shared prompt: it rides on the turn, like the note that
tells her a guild channel is a room full of people.
"""
from __future__ import annotations

import inspect

from kotoba.discord import bridge


def test_the_length_rule_only_rides_on_a_spoken_turn():
    src = inspect.getsource(bridge.ChannelSession.ask)
    assert 'register == "voice"' in src
    assert "_SPOKEN_ROOM" in src


def test_it_asks_for_sentences_and_offers_the_rest():
    said = bridge._SPOKEN_ROOM
    assert "TWO OR THREE SENTENCES" in said
    assert "offer the rest" in said
