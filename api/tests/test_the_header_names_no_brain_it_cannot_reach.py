"""The MODEL row names a brain the next turn can actually reach, or says what is missing instead.

It is one line, read before anything else, and may only claim what this process already knows. An
absent or placeholder key names nothing rather than a model nobody can talk to; a present, plausible
key names its model even if the provider would refuse it, since no round trip happens here — a wrong
key is indistinguishable from a good one without one, and `doctor` is where that question is asked.

The state this file exists for: `provider` and `model` are independent settings, so switching provider
can leave a stale model behind — a pair that 404s every turn while the row still shows it as
ready-looking as any other boot. Knowing better costs no network at all."""
from __future__ import annotations

from kotoba.cli.facts import _model
from kotoba.core import llm, providers

# Dead keys of the right shape: present and plausible, and refused by anything they are sent to.
PLAUSIBLE_OPENAI = "sk-" + "a" * 40
PLAUSIBLE_XAI = "xai-" + "a" * 40


def header(monkeypatch, **env) -> str:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return _model(llm, providers)


def test_an_absent_key_names_no_model(monkeypatch):
    assert header(monkeypatch, OPENAI_API_KEY="") == "OpenAI · no key yet"


def test_the_placeholder_out_of_the_example_file_is_not_a_key(monkeypatch):
    assert header(monkeypatch, OPENAI_API_KEY="sk-...") == "OpenAI · no key yet"


def test_a_plausible_key_names_its_model_without_asking_the_provider(monkeypatch):
    line = header(monkeypatch, OPENAI_API_KEY=PLAUSIBLE_OPENAI)

    assert line == f"{llm.model_name()} · OpenAI"


def test_a_model_the_active_provider_does_not_serve_is_not_named_as_ready(monkeypatch):
    """`/set provider xai` with the model left where it was — the pair `first_run.pin_model` exists to
    prevent, arriving through the door that does not go past first run."""
    line = header(monkeypatch, KOTOBA_LLM_PROVIDER="xai", XAI_API_KEY=PLAUSIBLE_XAI,
                  KOTOBA_MODEL="gpt-5.6-luna")

    assert "gpt-5.6-luna" not in line
    assert line == "xAI (Grok) · wrong model"


def test_a_model_the_active_provider_does_serve_is_named(monkeypatch):
    line = header(monkeypatch, KOTOBA_LLM_PROVIDER="xai", XAI_API_KEY=PLAUSIBLE_XAI,
                  KOTOBA_MODEL="grok-4.3")

    assert line == "grok-4.3 · xAI (Grok)"
