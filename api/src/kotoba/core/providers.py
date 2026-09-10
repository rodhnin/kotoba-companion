"""LLM provider descriptors: which provider Kotoba talks to, its base_url, key fallback, and reasoning.

The loop is coupled to the Responses-API SHAPE, not to OpenAI the vendor, so xAI is a near-drop-in:
swap base_url, key and model. Chat-Completions-only providers are reachable through an
openai_compatible gateway, not built here. The default is openai, empty base_url, OPENAI_API_KEY.

`models` is what first run OFFERS, cheapest first; `serves_model` still accepts anything typed. A
model must answer on the Responses API, call tools and stream — `grok-4.20-multi-agent` is left out
because our call SUCCEEDS with every tool of ours dropped — and it must reason, or the canned
English narration leaks into a Spanish turn. Coding-specialised ids live in `code_models`."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("kotoba.providers")


@dataclass(frozen=True)
class ModelChoice:
    id: str
    note: str            # what it is FOR, in one clause she can say out loud
    output_cost: float   # $ per 1M output tokens
    context: int = 0     # context window in tokens, 0 when the provider does not publish one


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    default_base_url: str            # "" → use the OpenAI SDK default endpoint
    key_env: str
    key_prefix_hint: str = ""        # soft validation only (warn, never hard-block)
    key_url: str = ""
    credit_url: str = ""
    model_match: str = ""
    default_model: str = ""
    # What earns a place on either list — and why a switch needs the pair above — is in the module
    # docstring. Both lists are a recommendation, never a ceiling.
    models: tuple["ModelChoice", ...] = ()
    code_models: tuple["ModelChoice", ...] = ()
    # Models matching this regex (within the provider) are reasoning models → may get reasoning kwargs.
    reasoning_model_match: str = ""
    # Effort values the provider's API accepts; we clamp/normalize to these. Empty = pass through.
    effort_values: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh")
    supports_encrypted_reasoning: bool = True  # may we send store=False + include=[reasoning.encrypted_content]?


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        default_base_url="",
        key_env="OPENAI_API_KEY",
        key_prefix_hint="sk-",
        key_url="https://platform.openai.com/api-keys",
        credit_url="https://platform.openai.com/settings/organization/billing",
        model_match=r"^(gpt-|o1|o3|o4|chatgpt-|text-)",
        default_model="gpt-5.6-luna",
        models=(
            ModelChoice("gpt-5.6-luna", "the least they charge me, and it still holds a million words of us",
                        1.20, 1_050_000),
            ModelChoice("gpt-5.4-nano", "just as cheap, with less room to remember",
                        1.25, 400_000),
            ModelChoice("gpt-5.4-mini", "the middle of their old generation — steady, and dearer than it looks",
                        4.50, 400_000),
            ModelChoice("gpt-5.6-terra", "their newest middle: sharper than the small ones, same long memory",
                        12.00, 1_050_000),
            ModelChoice("gpt-5.4", "slower and pricier, and it thinks harder on the tangled ones",
                        15.00, 1_050_000),
            ModelChoice("gpt-5.6-sol", "the best thinking they sell, for the ones that really are hard",
                        20.00, 1_050_000),
            ModelChoice("gpt-5.5", "built for long professional work, and it charges like it",
                        30.00, 1_050_000),
        ),
        code_models=(
            ModelChoice("gpt-5.3-codex", "tuned for agentic coding", 14.00, 400_000),
        ),
        reasoning_model_match=r"^(o1|o3|o4)|^gpt-5|gpt-5",
        # 'minimal' intentionally excluded — gpt-5.4-mini rejects it (400); normalize_effort clamps it to 'low'.
        # `max` is luna's top tier and only OpenAI serves it; the clamp below folds it to xhigh for xAI.
        effort_values=("low", "medium", "high", "xhigh", "max"),
        supports_encrypted_reasoning=True,
    ),
    "xai": ProviderSpec(
        id="xai",
        label="xAI (Grok)",
        default_base_url="https://api.x.ai/v1",
        key_env="XAI_API_KEY",
        key_prefix_hint="xai-",
        key_url="https://console.x.ai/team/default/api-keys",
        credit_url="https://console.x.ai",
        model_match=r"^grok-",
        # 4.3, not the 4.6 xAI recommends: 4.3 costs 2.4x less per output token and holds twice the
        # context (1M vs 500k). A default is what a stranger pays without choosing, so it is the cheap one.
        default_model="grok-4.3",
        models=(
            ModelChoice("grok-4.3", "cheap, and it remembers the longest conversations",
                        2.50, 1_000_000),
            ModelChoice("grok-4.5", "their coding-minded one — steadier on long, fiddly jobs",
                        6.00, 500_000),
            ModelChoice("grok-4.6", "xAI's newest — sharper, and it costs more to say things",
                        6.00, 500_000),
        ),
        code_models=(
            ModelChoice("grok-build-0.1", "their coding model — `grok-code-fast-1` now points here",
                        2.00, 256_000),
        ),
        # grok-4* and the grok-build coding line serve /v1/responses with encrypted reasoning.
        reasoning_model_match=r"grok-4(?!\.20-0309-reasoning)|grok-build",
        # xhigh is real on grok-4.6; where a model does not have it xAI treats it as `high` rather than
        # refusing, so allowing it costs a 4.6 user nothing and gains them the top setting.
        effort_values=("low", "medium", "high", "xhigh"),
        supports_encrypted_reasoning=True,
    ),
}

DEFAULT_PROVIDER = "openai"


def cost_hint(spec: ProviderSpec, choice: ModelChoice) -> str:
    """How much more this choice costs than the provider's cheapest, as a ratio of OUTPUT price.

    Output is the half that moves: she answers far more than she is asked, and it is where the two Grok
    models differ 2.4x while their input differs 1.6x. The cheapest choice gets no hint at all — a badge
    on every row is a badge that says nothing, and neither does one reading `1x`: two models within a
    few percent of each other are the same price to anybody choosing between them."""
    if not spec.models:
        return ""
    floor = min(m.output_cost for m in spec.models)
    if not floor:
        return ""
    ratio = choice.output_cost / floor
    if ratio < 1.05:
        return ""
    return f"{ratio:.1f}x".replace(".0x", "x")


def catalogue() -> dict:
    """Every provider's offered models, JSON-ready, with the ratio already worked out.

    One shape for both surfaces that render it — first run's model step and the Settings panel — so a
    model offered in one is the same model, with the same clause and the same ratio, in the other."""
    return {
        spec.id: {
            "label": spec.label,
            "default": spec.default_model,
            "models": [_row(spec, m) for m in spec.models],
            "code_models": [_row(spec, m) for m in spec.code_models],
        }
        for spec in PROVIDERS.values()
    }


def _row(spec: ProviderSpec, choice: ModelChoice) -> dict:
    return {"id": choice.id, "note": choice.note, "hint": cost_hint(spec, choice),
            "output_cost": choice.output_cost,
            "context": choice.context}


def active_provider_id() -> str:
    """The configured provider id (runtime override > env > default). Unknown → default 'openai'."""
    from kotoba.core import app_settings

    pid = app_settings.runtime_value("provider", "KOTOBA_LLM_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    return pid if pid in PROVIDERS else DEFAULT_PROVIDER


def get_spec(provider_id: str | None = None) -> ProviderSpec:
    return PROVIDERS.get((provider_id or active_provider_id()), PROVIDERS[DEFAULT_PROVIDER])


def active_base_url() -> str:
    """Effective base_url: runtime override > env > the provider's default. An EMPTY effective value falls
    back to the provider default (not '') — otherwise a persisted empty override would route e.g. the xAI
    key to the OpenAI SDK endpoint. For OpenAI the default is '' too (SDK default), so this is a no-op there."""
    from kotoba.core import app_settings

    spec = get_spec()
    v = app_settings.runtime_value("base_url", "KOTOBA_LLM_BASE_URL", spec.default_base_url).strip()
    return v or spec.default_base_url


def serves_model(model: str, provider_id: str | None = None) -> bool:
    """Whether this provider is the one that answers for MODEL. Unknown-shaped names are given the
    benefit of the doubt: an openai_compatible gateway can be pointed at anything, and a false
    "your provider does not have that model" would be worse than the 404 it saves."""
    spec = get_spec(provider_id)
    if not spec.model_match:
        return True
    name = (model or "").strip().lower()
    if not name:
        return True
    other = any(re.search(s.model_match, name) for s in PROVIDERS.values() if s.model_match)
    return bool(re.search(spec.model_match, name)) or not other


def model_supports_reasoning(model: str, provider_id: str | None = None) -> bool:
    """True if MODEL (within the active/given provider) accepts reasoning/encrypted-content kwargs."""
    spec = get_spec(provider_id)
    if not spec.reasoning_model_match:
        return False
    return bool(re.search(spec.reasoning_model_match, (model or "").strip().lower()))


_EFFORT_ORDER = ["minimal", "low", "medium", "high", "xhigh", "max"]


def normalize_effort(effort: str, provider_id: str | None = None) -> str:
    """The effort this provider will actually accept. Empty/off are the caller's to handle.

    A value ON the ladder is CLAMPED: the highest the provider offers that is not above what was asked
    ('max' → 'xhigh' for Grok), or its lowest when the ask sits below everything offered ('minimal' →
    'low', which is why gpt-5.4-mini's 400 never reaches anybody).

    A value NOT on the ladder has no position, and used to go to the provider VERBATIM — this is the last
    gate before the wire, since `runtime_value` returns the env half raw, so `EFFORT=junk` arrived as
    `{"effort": "junk"}`. It now falls to the provider's LOWEST offered effort; guessing high would spend
    a stranger's money on a typo. A provider declaring NO effort_values passes everything through."""
    spec = get_spec(provider_id)
    e = (effort or "").strip().lower()
    if not spec.effort_values or e in spec.effort_values:
        return e
    allowed = [v for v in spec.effort_values if v in _EFFORT_ORDER]
    if not allowed:
        return e
    if e in _EFFORT_ORDER:
        below = [v for v in allowed if _EFFORT_ORDER.index(v) <= _EFFORT_ORDER.index(e)]
        if below:
            return max(below, key=_EFFORT_ORDER.index)
    else:
        log.warning("reasoning effort %r is not one %s offers — using %r", effort, spec.label,
                    min(allowed, key=_EFFORT_ORDER.index))
    return min(allowed, key=_EFFORT_ORDER.index)
