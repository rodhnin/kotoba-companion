"""Between Discord's 48 kHz stereo and the rates her ears and mouth already use.

Every filter here CARRIES STATE between calls. A convolution restarted per 20 ms packet inserts a
discontinuity fifty times a second — a 50 Hz buzz that a transcriber hears and a person does not
place. That is the one thing to preserve if this is ever rewritten.
"""
from __future__ import annotations

import numpy as np

STT_RATE_HZ = 16_000
TTS_RATE = 24_000          # what her voice comes back as
DISCORD_RATE = 48_000
DISCORD_FRAME = 3840       # 20 ms, stereo, s16
STT_FRAME_BYTES = 8_000    # 250 ms of 16 kHz mono s16 — the cadence the browser sends

DECIM_TAPS = 49
INTERP_TAPS = 33


def lowpass(ntaps: int, cutoff_norm: float, gain: float = 1.0) -> np.ndarray:
    """Windowed sinc. `cutoff_norm` is cycles per sample of the rate the filter runs at."""
    n = np.arange(ntaps) - (ntaps - 1) / 2
    h = 2 * cutoff_norm * np.sinc(2 * cutoff_norm * n)
    h *= np.hamming(ntaps)
    return (h / h.sum() * gain).astype(np.float32)


def to_mono(stereo: bytes) -> np.ndarray:
    """int32 before the sum, or two loud channels wrap around and the loudest moment inverts."""
    frames = np.frombuffer(stereo, "<i2").reshape(-1, 2).astype(np.int32)
    return ((frames[:, 0] + frames[:, 1]) // 2).astype(np.int16)


class Decimator3:
    """48 kHz to 16 kHz. The ratio is exactly 3, but plain `x[::3]` folds everything above 8 kHz
    back into the band, and sibilants live up there."""

    def __init__(self) -> None:
        self._taps = lowpass(DECIM_TAPS, 7_200 / DISCORD_RATE)
        self._tail = np.zeros(DECIM_TAPS - 1, dtype=np.float32)
        self._phase = 0

    def feed(self, mono: np.ndarray) -> np.ndarray:
        buf = np.concatenate((self._tail, mono.astype(np.float32)))
        if len(buf) < DECIM_TAPS:
            self._tail = buf
            return np.zeros(0, dtype=np.int16)
        filtered = np.convolve(buf, self._taps, mode="valid")
        picked = filtered[self._phase::3]
        self._phase = (self._phase - len(filtered)) % 3
        self._tail = buf[-(DECIM_TAPS - 1):]
        return np.clip(picked, -32768, 32767).astype(np.int16)


class Interpolator2:
    """24 kHz to 48 kHz. Zero-stuff and filter — linear interpolation leaves images that Opus then
    encodes faithfully as grit."""

    def __init__(self) -> None:
        self._taps = lowpass(INTERP_TAPS, 11_000 / DISCORD_RATE, gain=2.0)
        self._tail = np.zeros(INTERP_TAPS - 1, dtype=np.float32)

    def feed(self, mono: np.ndarray) -> np.ndarray:
        stuffed = np.zeros(len(mono) * 2, dtype=np.float32)
        stuffed[::2] = mono
        buf = np.concatenate((self._tail, stuffed))
        if len(buf) < INTERP_TAPS:
            self._tail = buf
            return np.zeros(0, dtype=np.int16)
        out = np.convolve(buf, self._taps, mode="valid")
        self._tail = buf[-(INTERP_TAPS - 1):]
        return np.clip(out, -32768, 32767).astype(np.int16)


class Framer:
    """Hand on exactly `size` bytes at a time, hold the remainder."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        out = []
        while len(self._buf) >= self.size:
            out.append(bytes(self._buf[:self.size]))
            del self._buf[:self.size]
        return out

    def drain(self, pad: bool = False) -> bytes:
        rest, self._buf = bytes(self._buf), bytearray()
        if pad and rest:
            rest += b"\x00" * (self.size - len(rest))
        return rest


class ChunkAligner:
    """s16 is two bytes and a stream can split one. The browser has this same class for the same
    reason; without it an abandoned request byte-shifts everything behind it into static.

    A new generation drops the carry: half a sample of a turn that is over belongs to nothing."""

    def __init__(self) -> None:
        self._carry = b""
        self._gen = 0

    def feed(self, data: bytes, generation: int = 0) -> bytes:
        if generation != self._gen:
            self._gen, self._carry = generation, b""
        buf = self._carry + data
        keep = len(buf) - (len(buf) % 2)
        self._carry = buf[keep:]
        return buf[:keep]


class ToStt:
    """One speaker's Discord audio, in the shape her ears already accept."""

    def __init__(self) -> None:
        self._decim = Decimator3()
        self._framer = Framer(STT_FRAME_BYTES)

    def feed(self, pcm48_stereo: bytes) -> list[bytes]:
        return self._framer.feed(self._decim.feed(to_mono(pcm48_stereo)).tobytes())

    def drain(self) -> bytes:
        return self._framer.drain(pad=True)


class ToDiscord:
    """Her voice, in the shape Discord's player takes: 20 ms of 48 kHz stereo."""

    def __init__(self) -> None:
        self._align = ChunkAligner()
        self._interp = Interpolator2()
        self._framer = Framer(DISCORD_FRAME)

    def feed(self, pcm24_mono: bytes, generation: int = 0) -> list[bytes]:
        aligned = self._align.feed(pcm24_mono, generation)
        if not aligned:
            return []
        up = self._interp.feed(np.frombuffer(aligned, "<i2"))
        stereo = np.repeat(up[:, None], 2, axis=1).astype("<i2").tobytes()
        return self._framer.feed(stereo)

    def drain(self) -> bytes:
        return self._framer.drain(pad=True)
