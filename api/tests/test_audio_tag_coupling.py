"""Audio tags have ONE gate: core.app_settings.audio_tags_enabled().

Two knobs used to be independent: `expressive` (what the PROMPT asks for) and `tts_engine` (who renders
it). expressive=ON + tts_engine=fast paid for the long prompt block AND tag tokens in every reply, then
flash deleted every tag — a flat voice, paid for twice. The gate now couples them, and both consumers
(soul.prompt._expressive, core.stream.expressive_mode) delegate to it so they cannot desync.
"""
from __future__ import annotations

import pytest

from kotoba.core import app_settings, stream
from kotoba.soul import prompt


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    for var in ("KOTOBA_EXPRESSIVE", "KOTOBA_VOICE_MODE", "KOTOBA_TTS_ENGINE"):
        monkeypatch.delenv(var, raising=False)


def _configure(expressive=None, voice_mode=None, tts_engine=None):
    """Set only what the case names — anything left None keeps the shipped default."""
    for key, value in (("expressive", expressive), ("voice_mode", voice_mode), ("tts_engine", tts_engine)):
        if value is not None:
            app_settings.set_runtime(key, value)


# ---- the rule table -----------------------------------------------------------------------------

CASES = [
    # expressive, voice_mode, tts_engine, tags_on
    (False, "local", "expressive", False),   # explicit OFF wins everywhere
    (False, "local", "fast", False),
    (False, "agent", None, False),
    (True, "local", "fast", False),          # flash would delete them anyway
    (True, "local", "expressive", True),     # THE DEFAULT — v3 performs them
    (True, "agent", None, True),             # agent mode: `expressive` alone decides
]


@pytest.mark.parametrize("expressive,voice_mode,tts_engine,tags_on", CASES)
def test_gate_matches_the_rule_table(expressive, voice_mode, tts_engine, tags_on):
    _configure(expressive, voice_mode, tts_engine)
    assert app_settings.audio_tags_enabled() is tags_on


@pytest.mark.parametrize("expressive,voice_mode,tts_engine,tags_on", CASES)
def test_both_consumers_follow_the_gate(expressive, voice_mode, tts_engine, tags_on):
    _configure(expressive, voice_mode, tts_engine)
    assert prompt._expressive() is tags_on
    assert stream.expressive_mode() is tags_on


@pytest.mark.parametrize("tts_engine", ["expressive", "fast"])
def test_agent_mode_is_untouched_by_the_engine(tts_engine):
    """tts_engine only drives the LOCAL path; in agent mode the EL dashboard agent renders, so it must
    not move the gate."""
    _configure(True, "agent", tts_engine)
    assert app_settings.audio_tags_enabled() is True


def test_the_gate_can_only_remove_tags_never_add_them():
    for voice_mode in ("local", "agent"):
        for tts_engine in ("expressive", "fast"):
            _configure(False, voice_mode, tts_engine)
            assert app_settings.audio_tags_enabled() is False


# ---- what the prompt actually contains ----------------------------------------------------------

SOUL = {"name": "Kotoba", "personality": "warm", "language": "es"}
EXPRESSIVE_MARKER = prompt._VOICE_RULES_EXPRESSIVE.splitlines()[0]
PLAIN_MARKER = prompt._VOICE_RULES_PLAIN.splitlines()[0]


def _build() -> str:
    return prompt.build_system_prompt(SOUL, "(no profile)", [], session_id="s1")


def test_default_config_still_ships_the_expressive_block():
    """REGRESSION: the shipped default (voice_mode=local, tts_engine=expressive, expressive=ON) must
    behave exactly as before the coupling — this is the configuration running in production."""
    built = _build()
    assert app_settings.audio_tags_enabled() is True
    assert EXPRESSIVE_MARKER in built and PLAIN_MARKER not in built
    assert prompt._SOUND_RULES_EXPRESSIVE in built
    assert stream.expressive_mode() is True
    assert stream.AudioTagFilter().feed("[happily] Hola") == "[happily] Hola"


def test_fast_engine_drops_the_expressive_block_from_the_prompt():
    _configure(True, "local", "fast")
    built = _build()
    assert EXPRESSIVE_MARKER not in built and PLAIN_MARKER in built
    assert prompt._SOUND_RULES_EXPRESSIVE not in built
    assert stream.AudioTagFilter().feed("[happily] Hola") == " Hola"


def test_fast_engine_makes_the_prompt_shorter():
    _configure(True, "local", "expressive")
    with_tags = len(_build())
    _configure(True, "local", "fast")
    assert len(_build()) < with_tags
