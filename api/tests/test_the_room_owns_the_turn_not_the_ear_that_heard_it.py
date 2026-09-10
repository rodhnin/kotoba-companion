"""Who owns a spoken turn decides whether anybody can stop it.

Run inside the listen loop of the person she is answering, the turn had three measured consequences:
the filler that keeps that person's transcriber alive closed it three seconds in, a second waker
waited invisibly behind a lock with the player showing green, and an abandoned reply went on being
synthesised into a queue nobody could hear — paid for and never played.

The room owns it now, so a second name replaces the first and her mouth closes before anything is
awaited.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from kotoba.discord import voice as voice_mod
from kotoba.discord.voice import Speaker, VoiceRoom


class FakeVc:
    def __init__(self) -> None:
        self.playing = True
        self.stops = 0

    def is_playing(self) -> bool:
        return self.playing

    def stop(self) -> None:
        self.stops += 1
        self.playing = False


class FakeSpeech:
    """Only the two things the room is allowed to touch."""

    def __init__(self) -> None:
        self.killed_at: float | None = None

    def kill(self) -> None:
        self.killed_at = time.monotonic()


def room_for(on_turn) -> VoiceRoom:
    room = VoiceRoom.__new__(VoiceRoom)
    room.client = None
    room.channel = None
    room.names = ["Kotoba"]
    room.language = ""
    room.on_turn = on_turn
    room.on_empty = None
    room.vc = FakeVc()
    room.speakers = {}
    room.speaking_turn = 0
    room._closing = False
    room._turn = None
    room._speech = None
    room._turn_lock = asyncio.Lock()
    room._turn_ended_at = 0.0
    room._answered = None
    room._leaving_after_turn = False
    room._bg = set()
    room._empty_since = 0.0
    return room


def test_a_second_wake_cancels_the_first_and_waits_for_its_teardown():
    order: list[str] = []

    async def on_turn(room, user_id, text, turn_no, speech):
        order.append(f"start {turn_no}")
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(0)          # teardown that outlives the cancel, like the loop's
            order.append(f"torn down {turn_no}")
            raise

    async def scenario():
        room = room_for(on_turn)
        await room._start_turn(11, "hola")
        await asyncio.sleep(0)
        await room._start_turn(22, "oye")
        await asyncio.sleep(0)
        await room._stop_turn()

    asyncio.run(scenario())
    assert order[:3] == ["start 1", "torn down 1", "start 2"], order


def test_she_goes_quiet_before_the_turn_is_torn_down():
    """The cancellation lands inside the loop, whose teardown takes seconds. Closing her mouth after
    it means the person who interrupted goes on being talked over for exactly that long."""
    marks: dict[str, float] = {}

    async def on_turn(room, user_id, text, turn_no, speech):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)
            marks["torn down"] = time.monotonic()
            raise

    async def scenario():
        room = room_for(on_turn)
        await room._start_turn(11, "hola")
        await asyncio.sleep(0)
        speech = room._speech = FakeSpeech()
        await room._stop_turn()
        return speech

    speech = asyncio.run(scenario())
    assert speech.killed_at is not None
    assert speech.killed_at < marks["torn down"]


def test_a_cut_turn_takes_the_model_turn_it_was_waiting_on_with_it():
    from kotoba.discord.client import KotobaClient

    surface = KotobaClient.__new__(KotobaClient)
    surface._progress = {}
    fate: dict[str, bool] = {}

    async def model_turn():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            fate["cancelled"] = True
            raise

    async def scenario():
        inner = asyncio.create_task(model_turn())
        outer = asyncio.create_task(surface._until_stalled(inner, 10))
        await asyncio.sleep(0.01)
        outer.cancel()
        try:
            await outer
        except asyncio.CancelledError:
            pass
        return outer.cancelled(), inner.cancelled(), fate.get("cancelled", False)

    outer_cancelled, inner_cancelled, saw_cancel = asyncio.run(scenario())
    assert outer_cancelled, "the barge-in was turned into an ordinary ending"
    assert inner_cancelled and saw_cancel, "the model turn outlived the mouth it fed"


def test_a_turn_that_dies_does_not_take_the_room_with_it():
    async def on_turn(room, user_id, text, turn_no, speech):
        raise RuntimeError("the loop fell over")

    async def scenario():
        room = room_for(on_turn)
        await room._start_turn(11, "hola")
        await asyncio.sleep(0.01)
        return room

    room = asyncio.run(scenario())
    assert not room._turn_live()
    assert room._turn_ended_at > 0


def test_a_persons_ears_stay_open_for_as_long_as_they_are_in_the_room(monkeypatch):
    """Closed between sentences they reopened within five seconds nearly half the time, and each
    handshake ate the front of whatever was said next — which is where the name goes."""
    monkeypatch.setattr(voice_mod, "PUMP_SECONDS", 0.01)
    monkeypatch.setattr(voice_mod, "KEEPALIVE", 0.03)
    closed: list[int] = []
    sent: list[bytes] = []

    async def on_turn(room, user_id, text, turn_no, speech):
        await asyncio.sleep(0.1)

    async def scenario():
        room = room_for(on_turn)
        room.channel = type("Ch", (), {"members": [type("M", (), {"id": 7})()]})()
        speaker = Speaker(7)
        speaker.stt = object()
        speaker.last_audio = 0.0

        async def fake_send(sp, frame):
            sent.append(frame)
            sp.last_sent = time.monotonic()

        async def fake_close(sp):
            closed.append(sp.user_id)
            sp.stt = None

        room._send = fake_send
        room._close_ears = fake_close
        await room._start_turn(7, "hola")
        pump = asyncio.create_task(room._pump(speaker))
        await asyncio.sleep(0.15)
        assert not closed, "her ears closed while she was still in the room"
        assert sent, "the keepalive stopped, and an idle transcriber is dropped at fifteen seconds"
        await room._stop_turn()
        pump.cancel()
        try:
            await pump
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


def test_the_ears_close_when_the_person_walks_out(monkeypatch):
    monkeypatch.setattr(voice_mod, "PUMP_SECONDS", 0.01)
    closed: list[int] = []

    async def scenario():
        room = room_for(None)
        room.channel = type("Ch", (), {"members": []})()
        speaker = Speaker(7)
        speaker.stt = object()

        async def fake_close(sp):
            closed.append(sp.user_id)

        room._close_ears = fake_close
        await asyncio.wait_for(room._pump(speaker), timeout=2)

    asyncio.run(scenario())
    assert closed == [7]


@pytest.mark.parametrize("spoke, partial, expected", [
    (4.0, "qué hora es en Tokio", voice_mod.COMMIT_WAIT),
    (0.6, "kotoba", voice_mod.COMMIT_WAIT_UNSURE),
    (4.0, "", voice_mod.COMMIT_WAIT_UNSURE),
    (4.0, "dile que mañana y", voice_mod.COMMIT_WAIT_UNSURE),
])
def test_a_pause_inside_a_sentence_is_given_longer_than_the_end_of_one(spoke, partial, expected):
    """A second everywhere splits about one utterance in forty, and the split ones are exactly the
    hesitant openings her name lives in."""
    room = room_for(None)
    speaker = Speaker(7)
    speaker.spoke_seconds = spoke
    speaker.last_partial = partial
    assert room._commit_wait(speaker) == expected
