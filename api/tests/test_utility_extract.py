"""A regression: the utility-role sidecars (emotion, memory) must call the RESPONSES API, not
chat.completions with `max_tokens` — the default gpt-5.x rejects that with a 400, which the bare `except`
swallowed, leaving emotion+memory silently DEAD out-of-the-box on OpenAI."""
from __future__ import annotations

import asyncio



class _RespResult:
    def __init__(self, text):
        self.output_text = text


class _FakeClient:
    """Records how it was called. Only exposes responses.create (NOT chat.completions), so any code path
    that still used chat.completions.max_tokens would AttributeError → the test catches the regression."""
    def __init__(self, text):
        self._text = text
        self.calls = []

    class _Responses:
        def __init__(self, outer):
            self._o = outer

        async def create(self, **kwargs):
            self._o.calls.append(kwargs)
            return _RespResult(self._o._text)

    @property
    def responses(self):
        return _FakeClient._Responses(self)


def _patch_client(monkeypatch, text):
    from kotoba.core import llm
    fc = _FakeClient(text)
    monkeypatch.setattr(llm, "get_client", lambda: fc)
    return fc


def test_utility_extract_uses_responses_api_with_max_output_tokens(monkeypatch):
    from kotoba.core import llm
    fc = _patch_client(monkeypatch, "  happy  ")
    out = asyncio.run(llm.utility_extract("prompt", max_output_tokens=42))
    assert out == "happy"
    assert fc.calls and "max_output_tokens" in fc.calls[0]      # Responses API param, NOT chat max_tokens
    assert "max_tokens" not in fc.calls[0]                       # never the chat.completions param
    assert fc.calls[0]["max_output_tokens"] == 42


def test_extract_emotion_returns_valid_emotion(monkeypatch):
    from kotoba.core import emotions
    _patch_client(monkeypatch, "excited")
    assert asyncio.run(emotions.extract_emotion("yay great news!")) == "excited"


def test_extract_emotion_takes_last_word_and_falls_back(monkeypatch):
    from kotoba.core import emotions
    _patch_client(monkeypatch, "The emotion is: affectionate")   # reasoning prose around the answer
    assert asyncio.run(emotions.extract_emotion("aww")) == "affectionate"
    _patch_client(monkeypatch, "banana")                          # not a valid emotion → neutral
    assert asyncio.run(emotions.extract_emotion("x")) == "neutral"


def test_extract_emotion_empty_text_is_neutral(monkeypatch):
    from kotoba.core import emotions
    _patch_client(monkeypatch, "happy")
    assert asyncio.run(emotions.extract_emotion("   ")) == "neutral"


def test_memory_extract_parses_json_from_responses(monkeypatch):
    from kotoba.core import memory

    _patch_client(monkeypatch, '{"user_name": "Jordan", "companion_name": null, "facts": []}')

    saved = {}

    class _DB:
        async def upsert_user_profile(self, key, value):
            saved[key] = value
        async def update_soul_config(self, **kw):
            saved.update(kw)

    asyncio.run(memory.extract_and_save_memory("me llamo Jordan", _DB()))
    assert saved.get("name") == "Jordan"


def test_utility_extract_gates_reasoning_on_the_utility_model(monkeypatch, tmp_path):
    """The kwargs must be gated on the model that ANSWERS, not on the companion's.

    `utility_extract` sends `model=model_name("utility")` but built its kwargs with
    `model_call_kwargs("companion")`. Point `utility_model` at a cheap non-reasoning model — the whole
    reason the per-role setting exists — and the call goes out as gpt-4o-mini carrying `reasoning` +
    `store=False` + `include=["reasoning.encrypted_content"]`, the exact combination
    `llm._model_supports_reasoning` documents as a 400.

    `utility_extract` swallows the error, so emotion falls back to `neutral` forever and nothing is ever
    written to memory. `model_call_kwargs` already takes `role` for precisely this."""
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "s.yaml"))
    monkeypatch.setenv("KOTOBA_KEYSTORE_KEY_FILE", str(tmp_path / "ks"))
    from kotoba.core import app_settings, llm

    app_settings.set_runtime("provider", "openai")
    app_settings.set_runtime("reasoning_effort", "low")
    app_settings.set_runtime("model", "gpt-5.6-luna")        # companion: reasoning
    app_settings.set_runtime("utility_model", "gpt-4o-mini")  # the sidecars: NON-reasoning

    fc = _patch_client(monkeypatch, "happy")
    asyncio.run(llm.utility_extract("prompt"))

    sent = fc.calls[0]
    assert sent["model"] == "gpt-4o-mini"
    assert "reasoning" not in sent
    assert "store" not in sent and "include" not in sent
