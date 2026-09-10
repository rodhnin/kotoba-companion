"""Audio-tag → face contract. The model emits ElevenLabs [audio tags] in expressive mode; each allowed
tag must (1) survive the AudioTagFilter and (2) map to a face emotion via emotion_from_text, so the
SPOKEN emotion and the avatar's FACE always agree (one source: TAG_TO_EMOTION).

(History: an earlier approach injected tags into the canned tool narration via stream.voiced(); that was
dropped in favor of the model owning its own tags, so voiced()/EMOTION_TO_TAG were removed. This file now
guards only the live tag→face mapping.)
"""
from __future__ import annotations

from kotoba.core.stream import ALLOWED_AUDIO_TAGS, TAG_TO_EMOTION, emotion_from_text


def test_every_allowed_feeling_tag_maps_to_a_face():
    """Every allowed tag that carries a feeling drives a face via emotion_from_text, so voice and avatar
    agree. (Pure-delivery tags like [pause]/[whispers] map to 'neutral', which is also a valid face.)"""
    for tag in ALLOWED_AUDIO_TAGS:
        # emotion_from_text only fires on tags present in TAG_TO_EMOTION; every allowed tag must be there.
        assert tag in TAG_TO_EMOTION, f"allowed tag {tag!r} has no face mapping in TAG_TO_EMOTION"
        assert emotion_from_text(f"[{tag}] something") is not None, f"tag {tag!r} yields no face"


def test_emotion_from_text_returns_none_without_a_tag():
    assert emotion_from_text("just plain words, no brackets") is None
    assert emotion_from_text("") is None


def test_emotion_from_text_reads_the_first_tag():
    # warmly → affectionate (the first tag wins, even if a later tag differs).
    assert emotion_from_text("[warmly] hi there [sad] later") == TAG_TO_EMOTION["warmly"]
