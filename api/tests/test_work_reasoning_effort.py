"""WORK mode runs in the background (no voice latency to protect), so it reasons HARDER than the snappy
companion turn. This is what lets it actually think about a browser page (read the snapshot, pick the right
ref, screenshot when stuck, recover) instead of repeating a step blindly. Companion stays low for voice."""
from __future__ import annotations

import kotoba.core.llm as llm


def test_work_uses_higher_effort_than_companion(monkeypatch):
    monkeypatch.setenv("KOTOBA_MODEL", "gpt-5.4-mini")  # reasoning model (else kwargs are gated off)
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "low")
    monkeypatch.delenv("KOTOBA_WORK_REASONING_EFFORT", raising=False)
    # neutralize any settings-file override so we read env/default
    monkeypatch.setattr("kotoba.core.app_settings.runtime_value",
                        lambda key, env_var, default: __import__("os").getenv(env_var, default))
    companion = llm.model_call_kwargs("companion")
    work = llm.model_call_kwargs("work")
    assert companion["reasoning"]["effort"] == "low"
    assert work["reasoning"]["effort"] == "medium"  # background → think harder than companion (TPM-aware)
    # work reasons harder than companion
    assert work["reasoning"]["effort"] != companion["reasoning"]["effort"]


def test_non_reasoning_model_sends_nothing_in_either_mode(monkeypatch):
    monkeypatch.setattr("kotoba.core.app_settings.runtime_value",
                        lambda key, env_var, default: "" if env_var == "KOTOBA_REASONING_EFFORT" else default)
    assert llm.model_call_kwargs("companion") == {}
    assert llm.model_call_kwargs("work") == {}


def test_work_effort_override(monkeypatch):
    monkeypatch.setattr("kotoba.core.app_settings.runtime_value",
                        lambda key, env_var, default: {
                            "KOTOBA_MODEL": "gpt-5.4-mini",  # reasoning model
                            "KOTOBA_REASONING_EFFORT": "low",
                            "KOTOBA_WORK_REASONING_EFFORT": "medium",
                        }.get(env_var, default))
    assert llm.model_call_kwargs("work")["reasoning"]["effort"] == "medium"
