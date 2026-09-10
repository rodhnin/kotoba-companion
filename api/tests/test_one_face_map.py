"""The browser and the terminal read her face off ONE source.

`emotion_from_text` — what the web and the voice path drive the avatar with — matched `[...]` against
the nineteen AUDIO tags only. The terminal resolves the same bracket through `AudioTagFilter` +
`TAG_TO_FACE`, which also knows the fourteen emotion words the text register actually names for her. So
`[thinking]` was a face in the terminal and a fall-through to a second LLM call in the browser.

Both surfaces now ask the same filter the same question. The spoken stream is a different question and
is untouched: a voice drops every bracket it cannot perform, tags included.
"""
from __future__ import annotations

from kotoba.core.emotions import VALID_EMOTIONS
from kotoba.core.stream import (
    ALLOWED_AUDIO_TAGS,
    TAG_TO_EMOTION,
    TAG_TO_FACE,
    AudioTagFilter,
    emotion_from_text,
)


def _terminal_face(said: str):
    seen: list[str] = []
    tags = AudioTagFilter(keep_valid=False, text_surface=True, on_tag=seen.append)
    tags.feed(said)
    tags.flush()
    return TAG_TO_FACE.get(seen[0]) if seen else None


SAID = [
    "[thinking] Mmm... a ver.",
    "[determined] Ya lo estoy delegando.",
    "[confused] No sé qué quieres decir.",
    "[warmly] De nada.",
    "[laughs softly] Anda ya.",
    "[sad warmly] Jordan, y antes de nada...",
    "[sad, warmly] Jordan.",
    "[SAD WARMLY] Jordan.",
    "[warmly sad] Jordan.",
    "[sad and warmly] Jordan.",
    "[sad][warmly] Jordan.",
    "[sad warmly](https://a.b) es un enlace",
    "[happy](https://a.b) es un enlace",
    "x[happy] es un indice",
    "m[i][j] y la nota [2]",
    "un [tag roto",
    "sin ningun corchete",
    "",
]


def test_a_bracket_of_glued_tags_is_a_face_not_a_second_llm_call():
    """The leak, pinned from this side too: `[sad warmly]` resolved as unknown prose, so the
    browser paid an extract_emotion call for a face she had already named twice over."""
    assert emotion_from_text("[sad warmly] Jordan") == "sad"
    assert emotion_from_text("[sad and warmly] Jordan") is None


def test_the_two_surfaces_choose_the_same_face_for_everything_she_writes():
    for said in SAID:
        assert emotion_from_text(said) == _terminal_face(said), said


def test_the_faces_she_writes_by_name_are_no_longer_a_second_llm_call():
    """The exact regression: these four are the ones the text register names for her, and every one of
    them used to reach the browser as "no tag" — an extract_emotion call per turn, guessing at a face
    she had already asked for."""
    for face in ("thinking", "determined", "confused", "sleepy"):
        assert emotion_from_text(f"[{face}] algo") == face


def test_every_audio_tag_still_maps_exactly_where_it_did():
    for tag in sorted(ALLOWED_AUDIO_TAGS):
        assert emotion_from_text(f"[{tag}] algo") == TAG_TO_EMOTION[tag], tag


def test_no_tag_is_still_no_face():
    assert emotion_from_text("nada de corchetes") is None
    assert emotion_from_text("") is None


def test_the_face_it_returns_is_always_one_the_avatar_has():
    for said in SAID:
        face = emotion_from_text(said)
        assert face is None or face in VALID_EMOTIONS, said
