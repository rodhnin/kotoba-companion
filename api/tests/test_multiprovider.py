"""Talking to more than one model provider: descriptors, keys, client construction, effort clamping.

Covers the provider descriptor table, the keystore's envelope encryption for in-app API keys, a
`get_client` that rebuilds when the active provider changes, and per-provider reasoning detection and
effort normalisation. The standing constraint across all of it: with nothing configured, behaviour must
stay byte-identical to the single-provider OpenAI path it grew out of.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("KOTOBA_KEYSTORE_KEY_FILE", str(tmp_path / "ks_key"))
    import kotoba.core.llm as llm
    llm._client = None
    llm._client_key = None
    llm._provider_keys.clear()
    yield


# ── keystore ──────────────────────────────────────────────────────────────────────────────────────────
def test_keystore_roundtrip_and_tamper():
    """A stored key survives the round trip, and nothing else decrypts.

    Ciphertext is `v1:`-prefixed and never contains the secret. Anything that is not a genuine envelope
    — a tampered blob, or an un-prefixed value left over from before the keystore existed — decrypts to
    None rather than being handed back as if it were plaintext. The fingerprint is stable and does not
    leak the key it fingerprints."""
    from kotoba.core import keystore
    blob = keystore.encrypt("xai-secret-123")
    assert blob.startswith("v1:") and "xai-secret-123" not in blob
    assert keystore.decrypt(blob) == "xai-secret-123"
    assert keystore.decrypt("v1:garbage") is None
    assert keystore.decrypt("plain-legacy") is None
    assert keystore.fingerprint("xai-secret-123") == keystore.fingerprint("xai-secret-123")
    assert "xai-secret-123" not in keystore.fingerprint("xai-secret-123")


# ── provider descriptors ─────────────────────────────────────────────────────────────────────────────
def test_default_provider_is_openai_sdk_default():
    """Out of the box nothing is overridden: OpenAI, and an empty base_url so the SDK picks its own."""
    from kotoba.core import providers
    assert providers.active_provider_id() == "openai"
    assert providers.active_base_url() == ""

def test_reasoning_detection_per_provider():
    from kotoba.core import providers
    assert providers.model_supports_reasoning("gpt-5.4-mini", "openai") is True
    assert providers.model_supports_reasoning("o3-mini", "openai") is True
    assert providers.model_supports_reasoning("gpt-4o-mini", "openai") is False
    assert providers.model_supports_reasoning("grok-4.3", "xai") is True
    assert providers.model_supports_reasoning("grok-2", "xai") is False

def test_effort_clamp_per_provider():
    """`xhigh` used to be clamped to `high` for xAI on the grounds that grok-4.3 tops out there. Half of
    that is right and the half doing the work was not: xAI's reasoning guide says `xhigh` is real on
    grok-4.6, and that a model without it treats the request AS `high` rather than refusing it. So the
    clamp bought nothing and cost a grok-4.6 user their top setting. `effort_values` is per PROVIDER,
    and a per-model ceiling is not a thing it can express.

    The `minimal` exclusion is a different matter and stays: no provider in the table lists it, and on
    OpenAI sending it is a real 400 on every turn, so a requested `minimal` clamps up to `low`."""
    from kotoba.core import providers
    assert providers.normalize_effort("xhigh", "xai") == "xhigh"
    assert providers.normalize_effort("xhigh", "openai") == "xhigh"
    assert providers.normalize_effort("medium", "xai") == "medium"
    assert providers.normalize_effort("minimal", "xai") == "low"


@pytest.mark.parametrize("junk", ["junk", "maximum", "highest", "MAX-effort", "hard"])
def test_an_effort_the_provider_never_heard_of_is_not_forwarded_to_it(junk):
    """The docstring promised a fallback and the code returned the string VERBATIM. Measured:
    `normalize_effort('maximum')` came back `'maximum'` and reached `responses.create` as
    `{"effort": "maximum"}`. Reachable from the environment — the runtime setting is allowlisted, but
    `runtime_value` hands the env half back raw, so this function is the last gate before the wire."""
    from kotoba.core import providers

    for pid, spec in providers.PROVIDERS.items():
        got = providers.normalize_effort(junk, pid)
        assert got in spec.effort_values, f"{pid} was sent {got!r}"


def test_an_unrecognised_effort_falls_to_the_cheapest_the_provider_offers():
    """Not the highest. A typo must not silently buy a stranger the most expensive setting there is —
    and 'highest-but-not-over' has no meaning for a value with no place on the ladder."""
    from kotoba.core import providers

    assert providers.normalize_effort("junk", "openai") == "low"
    assert providers.normalize_effort("junk", "xai") == "low"


def test_a_provider_that_declares_no_efforts_still_passes_everything_through():
    """An openai_compatible gateway can be pointed at anything, so there is no list to clamp against.
    The clamp is only meaningful where the provider vouched for one."""
    from dataclasses import replace

    from kotoba.core import providers

    open_ended = replace(providers.PROVIDERS["openai"], id="gateway", effort_values=())
    monkey = dict(providers.PROVIDERS, gateway=open_ended)
    original = providers.PROVIDERS
    try:
        providers.PROVIDERS = monkey
        assert providers.normalize_effort("whatever-it-wants", "gateway") == "whatever-it-wants"
    finally:
        providers.PROVIDERS = original


def test_the_environment_cannot_put_a_junk_effort_on_the_wire(monkeypatch):
    """The whole reachable path, not just the helper: env → runtime_value → model_call_kwargs → SDK."""
    from kotoba.core import app_settings, llm

    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "junk")
    app_settings.set_runtime("provider", "openai")
    app_settings.set_runtime("model", "gpt-5.6-luna")
    from kotoba.core import providers

    effort = llm.model_call_kwargs("companion").get("reasoning", {}).get("effort")
    assert effort in providers.get_spec("openai").effort_values, f"sent {effort!r}"


def test_openai_effort_excludes_minimal_and_normalizes(monkeypatch):
    """gpt-5.4-mini rejects effort `minimal` with a 400 on every turn.

    So OpenAI no longer offers it at all, and a requested `minimal` clamps up to `low` rather than being
    forwarded verbatim."""
    from kotoba.core import providers
    assert "minimal" not in providers.get_spec("openai").effort_values
    assert providers.normalize_effort("minimal", "openai") == "low"


def test_empty_base_url_override_falls_back_to_provider_default(monkeypatch):
    """A persisted empty base_url must not route the xAI key at the OpenAI endpoint.

    Empty means "no override", so the provider's own default answers instead — which for OpenAI is
    itself empty, i.e. whatever the SDK ships with."""
    from kotoba.core import app_settings, providers
    app_settings.set_runtime("provider", "xai")
    app_settings.set_runtime("base_url", "")
    assert providers.active_base_url() == "https://api.x.ai/v1"
    app_settings.set_runtime("provider", "openai")
    assert providers.active_base_url() == ""


# ── provider-aware client ──────────────────────────────────────────────────────────────────────────────
def test_get_client_switches_provider_and_rebuilds(monkeypatch):
    """Switching provider hands back a NEW client, not the cached one aimed at the old endpoint."""
    from kotoba.core import app_settings, llm, providers
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    c1 = llm.get_client()
    assert c1 is not None and providers.active_provider_id() == "openai"
    app_settings.set_runtime("provider", "xai")
    llm.set_provider_key("xai", "xai-key")
    c2 = llm.get_client()
    assert c2 is not c1
    assert "x.ai" in str(c2.base_url)

def test_get_client_none_without_key(monkeypatch):
    """No key anywhere for the active provider means no client — the offline contract, unchanged.

    Returning None is what every caller already handles; inventing a client that will 401 is not."""
    from kotoba.core import app_settings, llm
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app_settings.set_runtime("provider", "xai")
    assert llm.get_client() is None

def test_in_app_key_beats_env(monkeypatch):
    """A key entered in the app wins over the environment: the visible setting is the one that acts."""
    from kotoba.core import llm, providers
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    llm.set_provider_key("openai", "sk-from-app")
    assert llm._resolve_key(providers.get_spec("openai")) == "sk-from-app"


# ── runtime spec validation ─────────────────────────────────────────────────────────────────────────────
def test_per_role_model_fallback_chain():
    """Each role can name its own model, and an unset one inherits rather than erroring.

    With only the base `model` configured every role resolves to it. Once overrides exist the chain is:
    `code` takes `code_model`, `research` falls back to the work model, and `utility` falls back to the
    companion model — so configuring one role never silently re-points another."""
    from kotoba.core import app_settings, llm
    app_settings.set_runtime("model", "gpt-5.4-mini")
    for role in ("companion", "work", "code", "research", "utility"):
        assert llm.model_name(role) == "gpt-5.4-mini"
    app_settings.set_runtime("work_model", "grok-4.3")
    app_settings.set_runtime("code_model", "gpt-5.4-codex")
    assert llm.model_name("companion") == "gpt-5.4-mini"
    assert llm.model_name("work") == "grok-4.3"
    assert llm.model_name("code") == "gpt-5.4-codex"
    assert llm.model_name("research") == "grok-4.3"
    assert llm.model_name("utility") == "gpt-5.4-mini"

def test_unknown_role_falls_back_to_companion():
    """A role nobody defined resolves to the companion model instead of raising."""
    from kotoba.core import app_settings, llm
    app_settings.set_runtime("model", "gpt-5.4-mini")
    assert llm.model_name("bogus-role") == "gpt-5.4-mini"


def test_provider_and_base_url_validation():
    """Only providers the table declares are settable, and base_url must be http(s) or empty.

    A provider with no descriptor here has no key resolution and no effort ladder, so accepting its name
    would produce a client that cannot work. An empty base_url means "the SDK default"."""
    from kotoba.core.app_settings import set_runtime
    assert set_runtime("provider", "xai") == "xai"
    with pytest.raises(ValueError):
        set_runtime("provider", "acme")
    assert set_runtime("base_url", "https://api.x.ai/v1") == "https://api.x.ai/v1"
    assert set_runtime("base_url", "") == ""
    with pytest.raises(ValueError):
        set_runtime("base_url", "ftp://nope")
