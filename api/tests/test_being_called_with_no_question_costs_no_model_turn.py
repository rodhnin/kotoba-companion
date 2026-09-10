"""Saying her name and nothing else was one wake in six, and every one of them bought a whole model
turn and a synthesis to produce "dime".

Six of the eight measured ones ended with the person saying her name again four to seven seconds
later, because from where they stood she had simply not answered. A noise in her own voice and a
short window to carry on is what a person would do, and it costs nothing.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from kotoba.discord import speak as speak_mod
from kotoba.discord import voice as voice_mod
from kotoba.discord.voice import Speaker, VoiceRoom


@pytest.fixture(autouse=True)
def _no_clips_leak():
    yield
    speak_mod._CLIPS.clear()


class FakeVc:
    def __init__(self) -> None:
        self.played = 0

    def is_playing(self) -> bool:
        return False

    def play(self, source) -> None:
        self.played += 1

    def stop(self) -> None:
        return None


def room_with(turns: list, clips: bool) -> VoiceRoom:
    room = VoiceRoom.__new__(VoiceRoom)
    room.names = ["Kotoba"]
    room.language = "es"
    room.channel = None
    room.vc = FakeVc()
    room.speaking_turn = 0
    room.speakers = {}
    room._closing = False
    room._turn = None
    room._speech = None
    room._turn_lock = asyncio.Lock()
    room._turn_ended_at = 0.0
    room._answered = None
    room._leaving_after_turn = False
    room._bg = set()

    async def on_turn(*args):
        turns.append(args[2])

    room.on_turn = on_turn
    speak_mod._CLIPS[:] = [[b"\x00" * 3840]] if clips else []
    return room


def test_her_name_alone_answers_with_a_noise_and_no_turn(monkeypatch):
    turns: list[str] = []
    room = room_with(turns, clips=True)
    monkeypatch.setattr(speak_mod.Speech, "_play", lambda self: None)

    async def scenario():
        speaker = Speaker(7)
        await room._committed(speaker, "Kotoba")
        assert turns == [], "she spent a model turn saying hello"
        assert speaker.attention_until > time.monotonic()
        # Whatever they say next is the question, and it does not need the name again.
        await room._committed(speaker, "qué hora es")
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert turns == ["qué hora es"]


def test_the_window_closes_and_the_name_is_needed_again(monkeypatch):
    turns: list[str] = []
    room = room_with(turns, clips=True)
    monkeypatch.setattr(speak_mod.Speech, "_play", lambda self: None)
    monkeypatch.setattr(voice_mod, "FOLLOW_UP", -1.0)

    async def scenario():
        speaker = Speaker(7)
        await room._committed(speaker, "Kotoba")
        await room._committed(speaker, "pásame la sal")
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert turns == []


def test_with_no_clip_ready_she_still_answers():
    """Silence is worse than a clumsy answer: the fallback is the turn this replaces."""
    turns: list[str] = []
    room = room_with(turns, clips=False)

    async def scenario():
        await room._committed(Speaker(7), "Kotoba")
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert turns == ["?"]
