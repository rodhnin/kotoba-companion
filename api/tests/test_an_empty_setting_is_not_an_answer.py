"""An empty environment value means "unset", never "off".

`paths.home_dir` already learned this — an empty KOTOBA_HOME resolved to the working directory — but
`runtime_value` only skipped the env half when it was None. Two settings read the empty string as a
real answer: `KOTOBA_REASONING_EFFORT=` stopped her reasoning and brought canned narration back, and
`KOTOBA_EXPRESSIVE=` took the audio tags with it. Commenting a line out by blanking it disabled a
feature with nothing on any screen to say so. `off` is still how you turn reasoning off on purpose.
"""
from __future__ import annotations

import pytest

from kotoba.core import app_settings


@pytest.fixture(autouse=True)
def _no_file_override(monkeypatch, tmp_path):
    monkeypatch.setenv("KOTOBA_HOME", str(tmp_path))
    monkeypatch.setattr(app_settings, "_runtime", lambda: {})


def test_a_blank_effort_is_the_one_key_that_means_it(monkeypatch):
    """`reasoning_effort` gives "" a meaning of its own — a second spelling of `off`, pinned in
    test_reasoning_passthrough. It is the exception this rule is written around, not a victim of it."""
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "")
    assert app_settings.runtime_value("reasoning_effort", "KOTOBA_REASONING_EFFORT",
                                      app_settings.DEFAULT_REASONING_EFFORT) == ""


def test_a_blank_model_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("KOTOBA_MODEL", "   ")
    got = app_settings.runtime_value("model", "KOTOBA_MODEL", app_settings.DEFAULT_MODEL)
    assert got == app_settings.DEFAULT_MODEL, f"a blank value left her without a model: {got!r}"


def test_off_still_turns_reasoning_off(monkeypatch):
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "off")
    assert app_settings.runtime_value("reasoning_effort", "KOTOBA_REASONING_EFFORT",
                                      app_settings.DEFAULT_REASONING_EFFORT) == "off"


def test_a_blank_expressive_keeps_the_audio_tags(monkeypatch):
    monkeypatch.setenv("KOTOBA_EXPRESSIVE", "")
    assert app_settings.runtime_all()["expressive"] is True, "a blank value dropped the audio tags"


def test_a_real_answer_still_wins(monkeypatch):
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "high")
    monkeypatch.setenv("KOTOBA_EXPRESSIVE", "false")
    assert app_settings.runtime_value("reasoning_effort", "KOTOBA_REASONING_EFFORT",
                                      app_settings.DEFAULT_REASONING_EFFORT) == "high"
    assert app_settings.runtime_all()["expressive"] is False
