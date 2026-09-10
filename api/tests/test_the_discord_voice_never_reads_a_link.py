"""Discord's voice is the fourth consumer of the spoken chain, and it was the only one without it.

`server.py` and `core/voice/session.py` both feed a reply through leak → fence → url → tag → phrase
before it reaches the TTS. `discord/speak.py` called `send_text` directly, so in a voice channel she
read citation URLs, code fences and tool-call leaks out loud — measured live on a justice.gov link
with a tracking parameter.
"""
from __future__ import annotations

import asyncio

from kotoba.discord import speak


class _Tts:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.ended = False

    async def connect(self) -> None: ...
    async def send_text(self, text: str) -> None: self.said.append(text)
    async def end(self) -> None: self.ended = True
    async def close(self) -> None: ...
    def audio_chunks(self): ...


def _spoken(text: str) -> str:
    """Everything the playback would hand to the TTS for one reply."""
    tts = _Tts()
    obj = speak.Speech.__new__(speak.Speech)
    obj._dead = False
    obj._tts = tts
    obj._reader = None
    if hasattr(obj, "_new_spoken_chain"):
        obj._spoken = obj._new_spoken_chain()

    async def go():
        await obj.feed(text)
        held = obj._flush_spoken() if hasattr(obj, "_flush_spoken") else ""
        if held:
            await tts.send_text(held)

    asyncio.run(go())
    return "".join(tts.said)


def test_a_citation_url_is_never_spoken():
    said = _spoken("prueban que fuera bueno con ellos "
                   "((https://www.justice.gov/epstein?utm_source=openai)) y nada mas.")
    assert "http" not in said, f"she read a link out loud: {said!r}"
    assert "justice.gov" not in said, f"she read a host out loud: {said!r}"
    assert "utm_source" not in said, f"she read a tracking parameter out loud: {said!r}"


def test_the_words_around_it_survive():
    said = _spoken("mira esto ((https://example.test/x)) y ya.")
    assert "mira esto" in said and "y ya" in said, f"the sentence was gutted: {said!r}"


def test_a_code_fence_is_not_read_aloud():
    said = _spoken("aqui tienes:\n```python\nprint('mas codigo')\n```\nlisto.")
    assert "print(" not in said, f"she read code out loud: {said!r}"
