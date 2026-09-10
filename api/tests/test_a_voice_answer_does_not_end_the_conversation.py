"""Her name, every time, from everybody — and nothing else wakes her in a voice channel.

There was a window here: after she answered somebody, that person could carry on without saying the
name again. It went in on live evidence and came out on live evidence. In a game channel two people
talk at once, and a rule about "the person she just answered" either shuts the door on them when the
other one speaks or starts answering the room. The operator's call: one rule, no state, say the name.

What remains is the pre-existing window and only it: called with her name and NO question, she makes
a sound instead of spending a model turn on "dime", and the question that follows needs no name.
"""
from __future__ import annotations

import asyncio
import time

from kotoba.discord import voice


class _VC:
    def __init__(self, playing: bool = False) -> None:
        self._playing = playing
        self.stopped = False

    def is_playing(self) -> bool:
        return self._playing

    def stop(self) -> None:
        self.stopped = True
        self._playing = False


def _room(*, playing: bool = False):
    room = object.__new__(voice.VoiceRoom)
    room.names = ["Kotoba"]
    room.speakers = {}
    room.vc = _VC(playing)
    room._turn_ended_at = 0.0
    room._turn = None
    room._closing = False
    room.started = []

    async def _start(user_id, text):
        room.started.append((user_id, text))

    room._start_turn = _start
    return room


def _speaker(room, uid: int):
    spk = room.speakers[uid] = voice.Speaker(uid)
    return spk


def _heard(room, spk, text: str):
    asyncio.run(room._committed(spk, text))


def test_her_name_wakes_her():
    room = _room()
    ana = _speaker(room, 1)
    _heard(room, ana, "Kotoba, hola que tal")
    assert room.started == [(1, "hola que tal")]


def test_the_next_sentence_without_the_name_does_not():
    """The window is gone on purpose. One rule, no state: say the name."""
    room = _room()
    ana = _speaker(room, 1)
    _heard(room, ana, "Kotoba, hola")
    room._turn_ended_at = time.monotonic()
    _heard(room, ana, "y que estas haciendo ahora")
    assert room.started == [(1, "hola")]


def test_not_even_while_she_is_still_speaking():
    room = _room(playing=True)
    ana = _speaker(room, 1)
    _heard(room, ana, "Kotoba, cuentame un chiste")
    _heard(room, ana, "no, mejor otro")
    assert room.started == [(1, "cuentame un chiste")]


def test_the_room_talking_around_her_is_not_addressed_to_her():
    room = _room()
    luis = _speaker(room, 2)
    _heard(room, luis, "el otro dia vi una cosa rarisima")
    assert room.started == []


def test_a_second_person_saying_the_name_gets_her():
    room = _room()
    ana, luis = _speaker(room, 1), _speaker(room, 2)
    _heard(room, ana, "Kotoba, hola")
    _heard(room, luis, "Kotoba, y a mi que")
    assert room.started == [(1, "hola"), (2, "y a mi que")]


def test_a_word_repeated_is_not_her_name_however_close_it_sounds():
    """A comma buys one extra edit against her name, because in a real room it comes back as "Toma",
    "Boba", "Tova". "Toto, Toto." is three edits away and woke her three times in a row while the
    people in the channel were asking her to be quiet. The edit is for a vocative, so something has
    to follow it worth answering."""
    assert voice.said_her_name("Toto, Toto.", ["Kotoba"]) is None
    assert voice.said_her_name("Toto, toto, toto", ["Kotoba"]) is None


def test_the_mangled_names_that_bought_that_tolerance_still_reach_her():
    for heard, rest in (("Toma, que hora es", "que hora es"),
                        ("Tova, ayudame", "ayudame"),
                        ("Boba, que opinas", "que opinas"),
                        ("Kotoba, hola", "hola")):
        assert voice.said_her_name(heard, ["Kotoba"]) == rest, heard


def test_her_mouth_is_closed_by_the_player_and_not_by_her_turn():
    """The lesson this test bought: her mouth OUTLIVES her turn. Generation ends while the audio is
    still draining, and by then both handles are already None — so a stop that only cancels the turn
    kills nothing and they hear her to the end. The player is the floor.

    What triggers it is now her name, not a word list, so the sentence carrying it is an ordinary
    turn: she is handed "cállate" and answers it however she likes."""
    room = _room(playing=True)
    room._turn = None                       # generation done, mouth still going
    room._speech = None                     # ... and both handles already cleared
    room._turn_lock = asyncio.Lock()
    ana = _speaker(room, 1)
    # The REAL _stop_turn, not a stub: replacing it proved only that it was called, and for weeks
    # it could be called and do nothing at all.
    asyncio.run(room._stop_turn())
    assert room.vc.stopped, "the player kept playing, so they heard her talk to the end"
    _heard(room, ana, "Kotoba, cállate")
    assert room.started == [(1, "cállate")], "no vocabulary — the words are hers to answer"
