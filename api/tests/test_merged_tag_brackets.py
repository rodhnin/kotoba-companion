"""A bracket the model glues two tags into is stage direction, not prose.

Found live: a glued tag pair printed verbatim on screen, because the filter asked whether the whole
bracket content was one known tag, so two words that are each a tag read as unknown prose. The rule
now is per-word: a bracket is a tag only when every word it holds is one, longer known tags matching
first; connectors deliberately stay prose, since matching near-misses to the vocabulary once rewrote
an unrelated URL. Three things pinned together: the whole shape space across every streaming chunk
boundary; ordinary prose (links, footnotes, unclosed brackets) must still print; and the spoken
surface stays byte-identical to the old behavior, since a voice drops any bracket it cannot perform.
Only the written register carries the one-tag-per-bracket rule; spoken registers carry none of it."""
from __future__ import annotations

import random
import re

import pytest

from kotoba.core import app_settings
from kotoba.core.stream import ALLOWED_AUDIO_TAGS, AudioTagFilter, emotion_from_text
from kotoba.soul import prompt

SHAPES = [
    ("[sad warmly] Jordan, y antes de nada", " Jordan, y antes de nada", ["sad", "warmly"]),
    ("[sad, warmly] hola", " hola", ["sad", "warmly"]),
    ("[sad,warmly] hola", " hola", ["sad", "warmly"]),
    ("[SAD WARMLY] hola", " hola", ["sad", "warmly"]),
    ("[warmly sad] hola", " hola", ["warmly", "sad"]),
    ("[sad and warmly] hola", "[sad and warmly] hola", []),
    ("[sad][warmly] hola", " hola", ["sad", "warmly"]),
    ("[laughs warmly sad] combo", " combo", ["laughs", "warmly", "sad"]),
    ("[giggles laughs softly] combo", " combo", ["giggles", "laughs softly"]),
    ("[laughs softly] anda ya", " anda ya", ["laughs softly"]),
    ("[drawn out] bueno", " bueno", ["drawn out"]),
    ("[warmly] de nada", " de nada", ["warmly"]),
    ("[thinking] mmm", " mmm", ["thinking"]),
    ("[sad warmly]", "", ["sad", "warmly"]),
    ("[sad warmly](https://x.example) es un enlace", "[sad warmly](https://x.example) es un enlace", []),
    ("[Forbes](https://x.example/a) dice", "[Forbes](https://x.example/a) dice", []),
    ("la nota [1] y [2]", "la nota [1] y [2]", []),
    ("arr[0] y m[i][j]", "arr[0] y m[i][j]", []),
    ("x[happy] es un indice", "x[happy] es un indice", []),
    ("un [tag roto", "un [tag roto", []),
    ("un [sad warmly que no cierra", "un [sad warmly que no cierra", []),
    ("[ ]", "[ ]", []),
    ("[yay] hola", "[yay] hola", []),
]


def _chunkings(text: str, rng: random.Random | None = None):
    yield [text]
    yield [text[: len(text) // 2], text[len(text) // 2:]]
    yield list(text)
    yield [text[i:i + 3] for i in range(0, len(text), 3)]
    if rng is not None:
        for _ in range(4):
            cuts = sorted(rng.sample(range(len(text) + 1), min(len(text), rng.randint(0, 8))))
            points = [0, *cuts, len(text)]
            yield [text[a:b] for a, b in zip(points, points[1:]) if a != b]


def _screen(chunks) -> tuple[str, list[str]]:
    seen: list[str] = []
    tags = AudioTagFilter(keep_valid=False, text_surface=True, on_tag=seen.append)
    return "".join(tags.feed(c) for c in chunks) + tags.flush(), seen


def test_every_shape_lands_where_it_belongs_however_the_stream_slices_it():
    rng = random.Random(20260812)
    for said, shown, tagged in SHAPES:
        for chunks in _chunkings(said, rng):
            assert _screen(chunks) == (shown, tagged), (said, chunks)


def test_a_merged_pair_still_names_her_face_first_word_first():
    assert emotion_from_text("[sad warmly] Jordan") == "sad"
    assert emotion_from_text("[warmly sad] Jordan") == "affectionate"
    assert emotion_from_text("[SAD, WARMLY] Jordan") == "sad"
    assert emotion_from_text("[sad and warmly] Jordan") is None
    assert emotion_from_text("[sad warmly](https://x.example) link") is None


_SPOKEN_BRACKET_RE = re.compile(r"\[([^\[\]]{0,40})\]")


def _spoken_oracle(text: str, keep_valid: bool) -> str:
    def gone(m: re.Match) -> str:
        kept = keep_valid and m.group(1).strip().lower() in ALLOWED_AUDIO_TAGS
        return m.group(0) if kept else ""

    return _SPOKEN_BRACKET_RE.sub(gone, text)


def test_the_voice_still_drops_every_bracket_it_cannot_perform_and_only_those():
    """The spoken surface must not move: a merged pair is not an allowed tag, so it is dropped in
    silence, and the nineteen allowed tags survive exactly when keep_valid says so."""
    rng = random.Random(20260812)
    battery = [said for said, _, _ in SHAPES] + [f"[{t}] hola" for t in sorted(ALLOWED_AUDIO_TAGS)]
    for said in battery:
        for keep_valid in (False, True):
            expected = _spoken_oracle(said, keep_valid)
            for chunks in _chunkings(said, rng):
                voice = AudioTagFilter(keep_valid=keep_valid)
                out = "".join(voice.feed(c) for c in chunks) + voice.flush()
                assert out == expected, (said, keep_valid, chunks)


FROZEN_NOW = ("2026-01-02 03:04 UTC", "2026-01-02 04:04 (CET UTC+01:00)")
SOUL = {"name": "Kotoba", "language": "auto", "personality": "Warm.",
        "address_style": "By name.", "emotional_rules": "Soften.", "quirks": "Hmph."}


@pytest.fixture
def _pinned(monkeypatch, tmp_path):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    for var in ("KOTOBA_EXPRESSIVE", "KOTOBA_VOICE_MODE", "KOTOBA_TTS_ENGINE", "KOTOBA_TZ"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(prompt, "_now_strings", lambda: FROZEN_NOW)


def _build(engine: str, **kwargs) -> str:
    app_settings.set_runtime("expressive", True)
    app_settings.set_runtime("voice_mode", "local")
    app_settings.set_runtime("tts_engine", engine)
    return prompt.build_system_prompt(SOUL, "- Name: Jordan", [], **kwargs)


def test_the_written_register_is_taught_one_tag_per_bracket(_pinned):
    """The rule the merge proved missing: `_TEXT_RULES` asked her to keep the [audio tags] while the
    only tag grammar in the prompt — don't stack, one tag → one stretch of words — left with the
    discarded voice rules, and the sound rules literally showed her "[sad] or [warmly]". Expressive
    only: with the tag channel off the written register no longer asks for tags at all, so there is
    no grammar to teach."""
    written = _build("expressive", register="text")
    assert "never `[sad warmly]` or `[sad and warmly]`" in written
    assert "ONE\ntag alone in its own brackets" in written


def test_the_written_register_without_tags_carries_no_tag_grammar(_pinned):
    written = _build("fast", register="text")
    assert "tag alone in its own brackets" not in written
    assert "Keep the [audio tags]" not in written


@pytest.mark.parametrize("engine", ["expressive", "fast"])
def test_the_spoken_registers_never_see_the_written_rule(_pinned, engine):
    for spoken in (_build(engine), _build(engine, register="voice")):
        assert "[sad warmly]" not in spoken
        assert "tag alone in its own brackets" not in spoken
