"""Lazily-created shared AsyncOpenAI client.

Returns None when no key is resolvable for the ACTIVE provider, so the server boots and the SSE pipeline
stays testable without one (the loop falls back to a friendly offline message). `kotoba setup` is the
route that wires a key on every install; the provider's env var still works on a clone.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("kotoba.llm")

# Keyed by (provider_id, base_url, key_fingerprint) so a LIVE provider/key switch rebuilds the client.
_client = None
_client_key: tuple | None = None

# Decrypted plaintext keys live in MEMORY only — never logged, never sent to the model (ephemeral_secrets discipline).
_provider_keys: dict[str, str] = {}

_TEMPLATE_WORDS = {
    "changeme", "change-me", "change_me", "placeholder", "todo", "none", "null",
    "your-key-here", "your_key_here", "your-api-key", "your_api_key", "xxx", "xxxx", "xxxxx",
}


def looks_placeholder(key: str) -> bool:
    """True for the values a template ships, never for a real key however unusual it looks.

    `.env.example` ships `OPENAI_API_KEY=sk-...` literally; copied unedited, that string used to reach
    the SDK as a configured key — doctor reported it green and every turn died on a 401 that only
    existed in the log. The tells are structural, so no real key can trip this: an ellipsis (`...`),
    angle brackets (`<paste it here>`), or one of the classic template words. Everything else —
    short, odd-looking, unprefixed — is treated as real, because a false 'your key is bad' is worse
    than the silence this closes."""
    k = (key or "").strip()
    if not k:
        return True
    if "..." in k or "…" in k or "<" in k or ">" in k:
        return True
    bare = k.lower()
    for prefix in ("sk-", "xai-", "el_"):
        bare = bare.removeprefix(prefix)
    return not bare or bare in _TEMPLATE_WORDS


def set_provider_key(provider_id: str, plaintext: str | None) -> None:
    """Cache (or clear) the in-app API key for a provider. Called by the lifespan loader + save endpoint.

    Trimmed, because what is stored has to be what was verified: the probe trims, so an untrimmed store
    would keep a key that answers in the wizard and fails on the first real turn."""
    plaintext = plaintext.strip() if plaintext else plaintext
    if plaintext:
        _provider_keys[provider_id] = plaintext
    else:
        _provider_keys.pop(provider_id, None)


def _resolve_key(spec) -> str:
    """Active API key for a provider: in-app saved key (decrypted, cached) > env fallback > ''.
    A placeholder counts as no key at all — the offline path it lands on says how to configure her,
    which beats a 401 on every turn. The saved key is filtered too: it USED to be exempt on the grounds
    that it had been round-tripped before storage, and that was only ever true of `kotoba setup` —
    `/api/settings/llm-key` saved first and validated after, so `sk-...` pasted into the web panel
    reached the SDK as a configured key and doctor called it green. Both doors verify before they store
    now; the filter stays, because the rows saved before they did are still on disk."""
    saved = _provider_keys.get(spec.id, "")
    if saved and not looks_placeholder(saved):
        return saved
    env = os.getenv(spec.key_env, "")
    return "" if looks_placeholder(env) else env


def get_client():
    """Provider-aware AsyncOpenAI client (works for OpenAI, xAI Grok, and any OpenAI-compatible base_url).

    Returns None when no API key is resolvable for the active provider — the loop falls back to a friendly
    offline message and the server still boots/tests without a key.
    """
    global _client, _client_key
    from kotoba.core import keystore, providers

    spec = providers.get_spec()
    base_url = providers.active_base_url()
    key = _resolve_key(spec)
    if not key:
        _client, _client_key = None, None
        return None
    cache_key = (spec.id, base_url, keystore.fingerprint(key))
    if _client is not None and _client_key == cache_key:
        return _client
    from openai import AsyncOpenAI

    # NO per-request read timeout, on ANY transport. The reason written here was EL-shaped ("it crashes
    # the call"); the real one is that it cannot tell a hung stream from a slow answer, and every caller
    # already bounds itself. A read timeout was tried here once and reverted for exactly that reason.
    kwargs: dict = {"api_key": key}
    if base_url:
        kwargs["base_url"] = base_url
    _client = AsyncOpenAI(**kwargs)
    _client_key = cache_key
    return _client


# Fallback chains — minimal config (only `model`) works for every role; read at request time (.env loads after import).
_ROLE_CHAIN: dict[str, list[tuple[str, str]]] = {
    "companion": [("model", "KOTOBA_MODEL")],
    "work": [("work_model", "KOTOBA_WORK_MODEL"), ("model", "KOTOBA_MODEL")],
    "code": [("code_model", "KOTOBA_CODE_MODEL"), ("work_model", "KOTOBA_WORK_MODEL"), ("model", "KOTOBA_MODEL")],
    "research": [("research_model", "KOTOBA_RESEARCH_MODEL"), ("work_model", "KOTOBA_WORK_MODEL"), ("model", "KOTOBA_MODEL")],
    "utility": [("utility_model", "KOTOBA_UTILITY_MODEL"), ("model", "KOTOBA_MODEL")],
}


def model_name(role: str = "companion") -> str:
    """The chat model for a ROLE, read at RUNTIME (not import time). Walks the role's fallback chain
    (e.g. code → work → companion) returning the first non-empty override/env; the final companion link
    defaults to app_settings.DEFAULT_MODEL (gpt-5.6-luna). `role` accepts the legacy values "companion"/"work"
    unchanged, plus the new "code"/"research"/"utility"."""
    from kotoba.core import app_settings

    chain = _ROLE_CHAIN.get(role, _ROLE_CHAIN["companion"])
    for i, (key, env) in enumerate(chain):
        default = app_settings.DEFAULT_MODEL if i == len(chain) - 1 else ""
        val = app_settings.runtime_value(key, env, default).strip()
        if val:
            return val
    return app_settings.DEFAULT_MODEL


def _model_supports_reasoning(name: str) -> bool:
    """True if MODEL `name` accepts reasoning/encrypted-content kwargs, per the ACTIVE provider's rule.
    OpenAI: o1/o3/o4-* and gpt-5*. xAI: grok-4* and grok-build*. Everything else is non-reasoning and 400s on
    `reasoning`/`include`. Gating on the model NAME (not just reasoning_effort) stops a broken combo: model
    and reasoning_effort are INDEPENDENT runtime settings, so picking gpt-4o-mini while effort stays 'low'
    must NOT send encrypted reasoning ('Encrypted content is not supported with this model')."""
    from kotoba.core import providers
    return providers.model_supports_reasoning(name)


def model_call_kwargs(mode: str = "companion", role: str | None = None) -> dict:
    """Extra kwargs for responses.create, gated by whether the model is a reasoning model.

    Reasoning models (gpt-5.x) take reasoning={"effort": low|medium|high|xhigh|max}. Higher means more
    thinking BEFORE any words stream, i.e. more silence, so it is MODE-AWARE: a companion turn takes the
    plain reasoning_effort ('low'), while WORK uses KOTOBA_WORK_REASONING_EFFORT (default 'medium') —
    which is what lets it read a browser snapshot and recover from an error instead of repeating a step
    blindly. "off" treats the model as non-reasoning.

    Stateless + encrypted reasoning: store=False plus include=["reasoning.encrypted_content"], re-fed
    each iteration. Non-reasoning models reject `reasoning`/`include`, so they get none of this."""
    from kotoba.core import app_settings
    # Gate on the model ACTUALLY answering (the role's model when delegating) — model and reasoning_effort
    # are independent runtime settings, and a non-reasoning model 400s on reasoning/include kwargs.
    gate_model = model_name(role) if role else model_name(mode)
    if not _model_supports_reasoning(gate_model):
        return {}
    base = app_settings.runtime_value("reasoning_effort", "KOTOBA_REASONING_EFFORT",
                                       app_settings.DEFAULT_REASONING_EFFORT).strip().lower()
    if not base or base == "off":
        return {}
    if mode == "work":
        # 'medium' default: ~half the reasoning tokens of 'high' — high effort + browser snapshots pegged the org TPM ceiling.
        effort = app_settings.runtime_value(
            "work_reasoning_effort", "KOTOBA_WORK_REASONING_EFFORT", "medium"
        ).strip().lower()
        if not effort or effort == "off":
            effort = base
    else:
        effort = base
    from kotoba.core import providers

    effort = providers.normalize_effort(effort)
    if not effort or effort == "off":
        return {}
    kw: dict = {"reasoning": {"effort": effort}}
    return _reasoning_store_kwargs(kw)


async def utility_extract(prompt: str, *, max_output_tokens: int = 512) -> str:
    """One-shot text extraction for the utility-role sidecars (emotion, memory), on the RESPONSES API.

    The default gpt-5.x rejects chat.completions `max_tokens` with a 400, which the callers' bare
    `except` swallowed — emotion and memory were silently DEAD out of the box. The budget here is
    generous enough that reasoning tokens do not starve the visible answer. Returns '' on any error.

    Gated on the UTILITY role's model, because that is the model answering. Built on the companion's it
    was wrong the moment `utility_model` pointed elsewhere — and what a person sets that to is a CHEAP
    model, which then received `reasoning` + `store=False` + encrypted-content include, a documented
    400: a permanent `neutral` face and a memory that never gained a fact, through the other door."""
    client = get_client()
    if client is None or not (prompt or "").strip():
        return ""
    try:
        r = await client.responses.create(
            model=model_name("utility"),
            input=prompt,
            max_output_tokens=max_output_tokens,
            **model_call_kwargs("companion", role="utility"),
        )
        return (getattr(r, "output_text", "") or "").strip()
    except Exception:
        log.debug("utility_extract failed", exc_info=True)
        return ""


def _reasoning_store_kwargs(kw: dict) -> dict:
    from kotoba.core import providers

    # Only stateless-encrypted-reasoning providers (OpenAI/xAI Responses) get store=False + include=[...].
    if not providers.get_spec().supports_encrypted_reasoning:
        return kw
    if os.getenv("KOTOBA_LLM_STORE", "").strip().lower() in ("1", "true", "yes"):
        kw["store"] = True  # opt-in to OpenAI-stored state (option A)
    else:
        kw["store"] = False  # stateless (option B, default): carry the reasoning ourselves as an encrypted blob we re-feed
        kw["include"] = ["reasoning.encrypted_content"]
    return kw


def is_reasoning_model(role: str | None = None) -> bool:
    """True when configured for a reasoning model (reasoning_effort set and not 'off').

    Pass `role` to gate on THAT role's model: narration suppression must follow the model actually
    answering, so a delegate on a non-reasoning code model still gets its canned narration.

    A reasoning model narrates its tool use naturally, in the user's own language, so the hardcoded
    English ANNOUNCE/HEARTBEAT/COMPLETE lines become both duplicated and wrong-language. The silence
    that suppression leaves is safe, but not because ElevenLabs fills it: that filler belongs to an EL
    agent CONVERSATION, and in local voice mode there is no agent. In `agent` mode the ~7s cut is real
    and answered by the /v1 generator's buffer word; in `local` mode nothing cuts the turn at all."""
    from kotoba.core import app_settings
    if not _model_supports_reasoning(model_name(role or "companion")):
        return False  # a non-reasoning model is never "reasoning", whatever reasoning_effort says
    effort = app_settings.runtime_value("reasoning_effort", "KOTOBA_REASONING_EFFORT",
                                       app_settings.DEFAULT_REASONING_EFFORT).strip().lower()
    return bool(effort) and effort != "off"
