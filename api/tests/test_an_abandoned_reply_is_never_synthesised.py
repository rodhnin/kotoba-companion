"""A turn nobody can hear any more must stop costing money.

`feed` opened a voice when it found none, which is right the first time and wrong every time after:
interrupted, the turn was cancelled but its feeder kept handing sentences over, so a whole reply was
synthesised sentence by sentence into a queue that had already been emptied. Billed in full, played
never.

The same flag covers the narrower race — abandoned while the connection handshake is still open.
"""
from __future__ import annotations

import asyncio

from kotoba.discord.speak import Speech


class FakeVc:
    def is_playing(self) -> bool:
        return False

    def stop(self) -> None:
        return None


def speech_for() -> Speech:
    made = Speech(FakeVc())
    made._play = lambda: None
    return made


def test_feeding_an_abandoned_turn_opens_no_voice():
    opened: list[int] = []

    async def scenario():
        speech = speech_for()
        speech._open = lambda: opened.append(1)      # never awaited, so a call would raise
        await speech.abandon()
        await speech.feed("una frase entera que nadie va a oír")
        await speech.finish()

    asyncio.run(scenario())
    assert opened == []


def test_a_turn_abandoned_mid_handshake_closes_what_it_opened():
    closed: list[str] = []

    class SlowTts:
        async def connect(self):
            await asyncio.sleep(0.05)

        async def close(self):
            closed.append("closed")

        async def send_text(self, text):
            closed.append("SPOKE")

    async def scenario():
        speech = speech_for()
        import kotoba.core.voice.tts as tts_mod
        import kotoba.core.voice.config as cfg
        original, cfg_original = tts_mod.create_tts_client, cfg.default_voice_id
        tts_mod.create_tts_client = lambda *a, **k: SlowTts()
        cfg.default_voice_id = lambda: "x"
        try:
            feeding = asyncio.create_task(speech.feed("hola"))
            await asyncio.sleep(0)
            await speech.abandon()
            await feeding
        finally:
            tts_mod.create_tts_client, cfg.default_voice_id = original, cfg_original

    asyncio.run(scenario())
    assert closed == ["closed"], closed
