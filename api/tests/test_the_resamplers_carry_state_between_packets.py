"""Discord speaks in 20 ms packets, and a filter restarted on each one buzzes at 50 Hz.

A convolution that begins from silence fifty times a second inserts a discontinuity fifty times a
second. It is inaudible as "a click" and very audible as a tone — and a transcriber hears it long
before a person places it. So the only property worth pinning hard is that feeding a signal in pieces
gives the same samples as feeding it whole.

The rates are the two the rest of Kotoba already uses: 16 kHz into her ears, 24 kHz out of her mouth,
48 kHz on both sides of Discord.
"""
from __future__ import annotations

import numpy as np

from kotoba.discord import audio


def _tone(n: int, hz: float, rate: int) -> np.ndarray:
    t = np.arange(n) / rate
    return (np.sin(2 * np.pi * hz * t) * 12000).astype(np.int16)


def test_decimating_in_packets_equals_decimating_whole():
    signal = _tone(48_000, 440, audio.DISCORD_RATE)
    whole = audio.Decimator3().feed(signal)
    piece = audio.Decimator3()
    parts = [piece.feed(signal[i:i + 960]) for i in range(0, len(signal), 960)]
    assert np.array_equal(whole, np.concatenate(parts))


def test_interpolating_in_packets_equals_interpolating_whole():
    signal = _tone(24_000, 300, audio.TTS_RATE)
    whole = audio.Interpolator2().feed(signal)
    piece = audio.Interpolator2()
    parts = [piece.feed(signal[i:i + 480]) for i in range(0, len(signal), 480)]
    assert np.array_equal(whole, np.concatenate(parts))


def test_a_tone_survives_the_trip_down_to_her_ears():
    """Not a buzz, not silence: the pitch has to still be there at the other end."""
    signal = _tone(48_000, 440, audio.DISCORD_RATE)
    out = audio.Decimator3().feed(signal).astype(np.float64)
    spectrum = np.abs(np.fft.rfft(out[2000:]))
    peak = np.fft.rfftfreq(len(out[2000:]), 1 / audio.STT_RATE_HZ)[spectrum.argmax()]
    assert abs(peak - 440) < 15


def test_everything_above_the_new_ceiling_is_filtered_not_folded():
    """Plain x[::3] folds 10 kHz down to 6 kHz, and it sounds like a whistle that was never said."""
    loud = _tone(48_000, 10_000, audio.DISCORD_RATE)
    out = audio.Decimator3().feed(loud).astype(np.float64)
    assert np.abs(out[2000:]).max() < 1500


def test_two_loud_channels_do_not_wrap_when_mixed():
    """int16 + int16 overflows, and the loudest moment of a shout inverts."""
    loud = np.full(200, 30000, dtype=np.int16)
    stereo = np.repeat(loud[:, None], 2, axis=1).astype("<i2").tobytes()
    assert audio.to_mono(stereo).max() == 30000


def test_the_stt_frame_is_the_exact_cadence_the_browser_sends():
    """EL's own VAD was tuned against 250 ms frames; a different size changes when it commits."""
    to_stt = audio.ToStt()
    got = b"".join(to_stt.feed(np.zeros(960 * 2, dtype=np.int16).tobytes()) or [b""])
    for _ in range(40):
        for frame in to_stt.feed(np.zeros(960 * 2, dtype=np.int16).tobytes()):
            assert len(frame) == audio.STT_FRAME_BYTES
            got = frame
    assert got == b"" or len(got) == audio.STT_FRAME_BYTES


def test_discord_gets_exactly_twenty_milliseconds_at_a_time():
    out = audio.ToDiscord()
    frames = out.feed(np.zeros(24_000, dtype=np.int16).tobytes())
    assert frames and all(len(f) == audio.DISCORD_FRAME for f in frames)


def test_an_odd_byte_never_shifts_the_next_chunk():
    """`tts_rest` pads an abandoned request with one byte; a consumer without this produces static."""
    align = audio.ChunkAligner()
    first = align.feed(b"\x01\x02\x03")
    second = align.feed(b"\x04")
    assert first == b"\x01\x02"
    assert second == b"\x03\x04"


def test_a_new_turn_drops_the_half_sample_of_the_old_one():
    align = audio.ChunkAligner()
    align.feed(b"\x01\x02\x03", generation=1)
    assert align.feed(b"\x09\x08", generation=2) == b"\x09\x08"
