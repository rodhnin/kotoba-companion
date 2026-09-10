"""Her kaomoji: the tier every terminal draws, and the one that has to hold still.

The face is repainted inside a live region that measures it once, so a frame wider than the width it
declared pushes whatever sits beside it a cell to the right and back again — for one blink, at twelve
frames a second. The widths here are not decoration: `render()` pads to `Face.width`, and padding only
works while nothing it can draw is wider than that.
"""
from __future__ import annotations

import time

from rich.cells import cell_len

from kotoba.cli.render import art
from kotoba.cli.render.kaomoji import EMOTIONS, FACES_ASCII, FACES_UNICODE, FAMILY, PLATE_SLOT, Face
from kotoba.core.emotions import VALID_EMOTIONS


def phases(face: Face, emotion: str) -> list[str]:
    """Every frame this mood can be drawn as: mouth shut, half open, wide, each with and without the
    blink she changes mood behind."""
    return [face._draw(emotion, openness, face._table, blink=blink)
            for openness in (0.0, 0.5, 0.95) for blink in (False, True)]


def test_every_emotion_the_engine_can_send_has_a_face_on_both_tables():
    assert set(EMOTIONS) == set(VALID_EMOTIONS) == set(FACES_ASCII) == set(FACES_UNICODE)
    assert set(FAMILY) == set(EMOTIONS)
    assert set(FAMILY.values()) <= set(PLATE_SLOT)


def test_no_frame_of_any_mood_is_wider_than_the_width_the_plate_reserved():
    for unicode_ok in (True, False):
        face = Face(unicode=unicode_ok)
        for emotion in EMOTIONS:
            for drawn in phases(face, emotion):
                assert cell_len(drawn) <= face.width, f"{emotion} {drawn!r} overflows {face.width}"
                padded = drawn + " " * max(0, face.width - cell_len(drawn))
                assert cell_len(padded) == face.width


def test_the_ascii_table_is_seven_bits_including_the_glyph_she_blinks_with():
    face = Face(unicode=False)
    for emotion in EMOTIONS:
        for drawn in phases(face, emotion) + [face.still()]:
            assert [ch for ch in drawn if ord(ch) > 127] == []


def test_a_mood_she_has_never_heard_of_changes_nothing_and_draws_nothing_blank():
    """`done` is a real key in the soul's EXPRESSIONS and deliberately never emitted; a hallucinated
    one arrives the same way. Either must leave the face she is wearing exactly where it was."""
    face = Face()
    face.set("happy", instant=True)
    for stranger in ("done", "ecstatic", "", "neutral;rm -rf"):
        face.set(stranger)
        assert face.emotion == "happy" and face.settled
        assert face.still() == "( ^ω^ )"


def test_an_unknown_mood_falls_back_to_a_face_that_exists_on_disk():
    for stranger in ("done", "ecstatic", "../../etc/passwd"):
        assert art.path(stranger).name == "neutral.png"
        assert art.path(stranger, outlined=True).name in ("neutral.png", "neutral-outlined.png")


def test_a_face_nobody_is_talking_to_repaints_to_the_same_bytes_forever():
    """The idle budget is zero bytes a second. Her mouth decays from the last token she was fed, so a
    face left alone has to stop moving on its own — a region diffing a frame that keeps changing
    repaints for as long as she is on the screen."""
    face = Face()
    face.set("thinking", instant=True)
    frames = set()
    started = time.monotonic()
    while time.monotonic() - started < 0.35:
        frames.add(face.render())
        time.sleep(0.01)
    assert len(frames) == 1

    face.feed(10)
    assert len({face.render() for _ in range(3)}) >= 1        # a token opens it
    face.rest()
    assert len({face.render() for _ in range(30)}) == 1       # and it closes for good


def test_a_partial_set_missing_an_outlined_face_keeps_the_emotion_and_drops_the_ring(monkeypatch, tmp_path):
    """A bring-your-own face set is allowed to be partial, and one used to fall from a missing
    `happy-outlined.png` straight to plain `neutral.png` — a legible face wearing the WRONG emotion.
    The emotion is the message and the ring is legibility support, so the ladder drops the ring first."""
    for name in ("happy.png", "neutral.png", "neutral-outlined.png"):
        (tmp_path / name).touch()
    monkeypatch.setenv("KOTOBA_CLI_FACES", str(tmp_path))
    assert art.path("happy", outlined=True).name == "happy.png"
    assert art.path("happy").name == "happy.png"


def test_only_when_the_emotion_is_gone_entirely_does_the_ring_preference_resume(monkeypatch, tmp_path):
    for name in ("neutral.png", "neutral-outlined.png"):
        (tmp_path / name).touch()
    monkeypatch.setenv("KOTOBA_CLI_FACES", str(tmp_path))
    assert art.path("sad", outlined=True).name == "neutral-outlined.png"
    assert art.path("sad").name == "neutral.png"


def test_the_full_set_still_serves_the_outlined_twin_first(monkeypatch, tmp_path):
    for name in ("happy.png", "happy-outlined.png", "neutral.png", "neutral-outlined.png"):
        (tmp_path / name).touch()
    monkeypatch.setenv("KOTOBA_CLI_FACES", str(tmp_path))
    assert art.path("happy", outlined=True).name == "happy-outlined.png"
