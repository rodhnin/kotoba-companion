"""Her reply, said out loud in a Discord voice channel.

The same two engines the rest of Kotoba uses, at the same rate: her voice comes back as 24 kHz mono
and Discord wants 48 kHz stereo in 20 ms frames, which is all `audio.ToDiscord` does.

Text is fed in as it streams. The expressive engine splits its own sentences, so a reply that takes
six seconds to write starts being heard after the first one rather than after the last.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random

from kotoba.discord import audio as audio_mod
from kotoba.discord.voice import Playback

log = logging.getLogger("kotoba.discord")

# Rendered once per process in her own voice and kept in memory. Sounds, not words: she is in rooms
# whose language nobody declared, and a spoken "let me see" in the wrong one is worse than silence.
# Several, so the same one does not come back every time, which is what makes a canned line sound canned.
# Each measured under 1.3 s and none of them spelled out letter by letter.
ACKS = ["Mmm...", "Hmm...", "Mm."]
_CLIPS: list[list[bytes]] = []
_WARMING = False


async def warm_acks() -> None:
    """Her first sound, ready before anybody speaks.

    From the end of a question to her first word is about four and a half seconds, of which under
    three are hers to shorten — the rest is the model writing. A short noise in her own voice at
    three tenths of a second turns that silence into somebody thinking."""
    global _WARMING
    if _CLIPS or _WARMING:
        return
    _WARMING = True
    try:
        _CLIPS.extend([await _render(line) for line in ACKS])
    except Exception:
        log.debug("could not prepare her acknowledgements", exc_info=True)
    finally:
        _WARMING = False


async def _render(line: str) -> list[bytes]:
    from kotoba.core.voice import config as voice_config
    from kotoba.core.voice.tts import create_tts_client

    tts = create_tts_client(voice_config.default_voice_id(), output_format="pcm_24000")
    await tts.connect()
    try:
        await tts.send_text(line)
        await tts.end()
        bridge = audio_mod.ToDiscord()
        frames: list[bytes] = []
        async for chunk in tts.audio_chunks():
            frames.extend(bridge.feed(chunk))
        rest = bridge.drain()
        if rest:
            frames.append(rest)
        return frames
    finally:
        await tts.close()


def an_ack() -> list[bytes]:
    return random.choice(_CLIPS) if _CLIPS else []


def ensure_opus() -> None:
    """Windows ships the DLL inside the wheel and discord.py loads it itself; asking ctypes for a
    Linux .so name there raises instead. Only name one where nothing else will."""
    import discord.opus as opus

    if opus.is_loaded() or os.name == "nt":
        return
    from ctypes.util import find_library

    opus.load_opus(find_library("opus") or "libopus.so.0")


class Speech:
    """One spoken turn. Cancelled, it stops the player as well as the queue — clearing one and not
    the other leaves the library's thread alive on a source nobody is filling."""

    def __init__(self, vc, *, generation: int = 0) -> None:
        self.vc = vc
        self.generation = generation
        self.playback = Playback()
        self.bridge = audio_mod.ToDiscord()
        self._tts = None
        self._reader: asyncio.Task | None = None
        self.spoke = False
        self._dead = False
        self._spoken = self._new_spoken_chain()

    @staticmethod
    def _new_spoken_chain():
        """The chain a reply passes before any of it is SPOKEN, in the order `flush_spoken` defines.
        This surface fed the TTS directly, so a voice channel was the one place she read citation
        URLs, code fences and tool-call leaks out loud. The written register filters differently on
        purpose: what is safe to print is not what is safe to say."""
        from kotoba.core import stream as sse

        return (sse.ToolCallLeakFilter(), sse.CodeFenceFilter(), sse.UrlFilter(),
                sse.AudioTagFilter(), sse.ForbiddenPhraseFilter())

    def _say(self, text: str) -> str:
        leak_f, code_f, url_f, tag_f, phrase_f = self._spoken
        return phrase_f.feed(tag_f.feed(url_f.feed(code_f.feed(leak_f.feed(text)))))

    def _flush_spoken(self) -> str:
        from kotoba.core import stream as sse

        return sse.flush_spoken(*self._spoken, final=True)

    async def start(self, *, acknowledge: bool = True) -> None:
        """Only the player, plus a noise that says she heard. Her VOICE is opened by the first
        words, not here.

        Opened up front, its receive timeout starts running before there is anything to synthesise:
        a turn that took longer than that to write its first sentence came back with the stream
        already given up on, and she said nothing at all."""
        self._play()
        if acknowledge:
            for frame in an_ack():
                self.playback.push(frame)

    async def _open(self) -> None:
        from kotoba.core.voice import config as voice_config
        from kotoba.core.voice.tts import create_tts_client

        if self._dead:
            return
        tts = create_tts_client(voice_config.default_voice_id(), output_format="pcm_24000")
        await tts.connect()
        if self._dead:                      # abandoned during the handshake, which takes long enough
            await tts.close()
            return
        self._tts = tts
        self._reader = asyncio.create_task(self._read())

    def _play(self) -> None:
        ensure_opus()
        if self.vc.is_playing():
            self.vc.stop()
        self.vc.play(_source_class()(self.playback))
        log.info("voice: player started")

    async def feed(self, text: str) -> None:
        """Abandoned means abandoned. This line reopened the voice of a turn nobody could hear any
        more, and synthesised the whole rest of the reply into a queue that had already been thrown
        away — paid for, start to finish, and never played."""
        if not text or self._dead:
            return
        if self._tts is None:
            await self._open()
        if self._tts is None:
            return
        spoken = self._say(text)
        if not spoken:
            return
        try:
            await self._tts.send_text(spoken)
        except Exception:
            log.warning("voice: tts refused a line", exc_info=True)

    async def _read(self) -> None:
        try:
            got = 0
            async for chunk in self._tts.audio_chunks():
                if self._dead:
                    return
                got += len(chunk)
                for frame in self.bridge.feed(chunk, self.generation):
                    self.spoke = True
                    self.playback.push(frame)
            log.info("voice: tts delivered %s bytes, spoke=%s", got, self.spoke)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("voice: tts stream ended badly", exc_info=True)

    async def finish(self) -> None:
        if self._dead:
            return
        if self._tts is None:
            self.playback.finish()          # nothing was ever said; let the player end cleanly
            return
        if self._tts is not None:
            held = self._flush_spoken()
            if held:
                try:
                    await self._tts.send_text(held)
                except Exception:
                    log.warning("voice: tts refused the tail", exc_info=True)
            try:
                await self._tts.end()
            except Exception:
                pass
        if self._reader is not None:
            try:
                await asyncio.wait_for(self._reader, timeout=60)
            except Exception:
                self._reader.cancel()
        rest = self.bridge.drain()
        if rest:
            self.playback.push(rest)
        self.playback.finish()
        await self._close()

    def kill(self) -> None:
        """Synchronous on purpose: the caller has to be able to shut her up BEFORE it awaits anything.
        Tearing the turn down first leaves the frames already queued playing for the seconds that
        takes, so the person who interrupted goes on being talked over."""
        self._dead = True
        if self._reader is not None:
            self._reader.cancel()
        try:
            if self.vc is not None and self.vc.is_playing():
                self.vc.stop()
        except Exception:
            pass
        self.playback.abandon()

    async def abandon(self) -> None:
        """Barge-in and shutdown take the same path: stop the player, then empty the queue."""
        self.kill()
        await self._close()

    async def _close(self) -> None:
        tts, self._tts = self._tts, None
        if tts is not None:
            try:
                await tts.close()
            except Exception:
                pass


_SOURCE = None


def _source_class():
    """Built on first use, so this module imports on an install without the extra.

    `read()` runs on the library's PLAYER THREAD, which is why the only thing crossing that boundary
    is the queue inside Playback — and why an empty queue answers with silence rather than with the
    end of the stream."""
    global _SOURCE
    if _SOURCE is not None:
        return _SOURCE
    import discord

    class KotobaSource(discord.AudioSource):
        def __init__(self, playback: Playback) -> None:
            self._playback = playback

        def read(self) -> bytes:
            return self._playback.read()

        def is_opus(self) -> bool:
            return False

    _SOURCE = KotobaSource
    return _SOURCE
