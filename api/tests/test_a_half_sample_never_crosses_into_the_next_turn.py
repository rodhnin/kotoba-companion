"""Her voice arrives as bytes, not as samples, and a stream splits one in half wherever it likes.

Carried across, the half joins the next chunk and everything behind it is shifted by one byte — every
sample built from the wrong pair, which comes out as static rather than as anything recognisable.
Dropped, the same thing happens once. So the carry is kept.

But it is kept only within one turn. Interrupted, the request that was in flight leaves half a sample
belonging to a reply nobody will hear, and gluing it to the front of the NEXT one shifts that one
instead — an interruption poisoning the answer that replaced it.
"""
from __future__ import annotations

from kotoba.discord.audio import ChunkAligner


def test_a_split_sample_is_rejoined_not_dropped():
    align = ChunkAligner()
    first = align.feed(b"\x01\x02\x03")
    second = align.feed(b"\x04\x05\x06")
    assert first == b"\x01\x02"
    assert second == b"\x03\x04\x05\x06", "the odd byte comes back at the head of the next chunk"
    assert first + second == b"\x01\x02\x03\x04\x05\x06", "and nothing is lost or reordered"


def test_every_chunk_handed_on_is_a_whole_number_of_samples():
    align = ChunkAligner()
    for chunk in (b"\x00" * 3, b"\x00" * 1, b"\x00" * 7, b"\x00" * 2, b"\x00" * 5):
        assert len(align.feed(chunk)) % 2 == 0


def test_an_abandoned_request_leaves_nothing_behind():
    """Half a sample of a turn that is over belongs to nothing."""
    align = ChunkAligner()
    align.feed(b"\x01\x02\x03", 1)                 # one byte held back
    kept = align.feed(b"\x04\x05\x06\x07", 2)      # a new turn: the carry is not its
    assert kept == b"\x04\x05\x06\x07"
    assert len(kept) % 2 == 0


def test_the_carry_survives_inside_one_turn_but_not_across_two():
    align = ChunkAligner()
    align.feed(b"\xaa" * 5, 7)
    assert align.feed(b"\xbb" * 2, 7)[:1] == b"\xaa"
    align.feed(b"\xcc" * 5, 8)
    assert align.feed(b"\xdd" * 2, 8)[:1] == b"\xcc"
