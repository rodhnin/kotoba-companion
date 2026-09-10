"""Whether this machine has ever been configured, and the setting a brain choice drags with it.

Both answers are asked by `kotoba setup` and by the web's first-run screen, so they live here.
`select_model()` is asked BEFORE the key on purpose: the check runs against whatever model is
configured, so a key with no access to it fails at the key step and not three screens later.

`needed()` must stay cheap: an env var is a perfectly good configuration. `verify_key()` is the round
trip that must happen BEFORE a key is stored — storing first left a dead key in the keystore, read by
`needed()` as configured. `pin_model()` exists because `model` is global, not per provider: choosing
xAI on a fresh install left it on an OpenAI model, so the round trip 404'd and a good key read as bad."""
from __future__ import annotations

import logging
import os

from kotoba.core import app_settings, llm, providers
from kotoba.db.database import Database

log = logging.getLogger("kotoba.first_run")

# Enough to prove the key answers. NOT free on a reasoning model: reasoning tokens come out of this
# same budget, so the probe comes back `incomplete` with nothing visible and the thinking is still
# billed. Raising it would only buy a visible word — the 400 this check exists to catch (a model that
# rejects the encrypted-reasoning kwargs) arrives before any generation.
_PROBE_TOKENS = 16


async def needed(db: Database) -> bool:
    """True when the ACTIVE provider has no usable key — neither saved nor in the environment.

    The question is "can she talk right now?", not "does any key exist anywhere?". Those differ on a case
    `.env.example` makes ordinary, since it ships a line for both companies: with `XAI_API_KEY` set, no
    OpenAI key and `provider` on its default, this answered False while `llm.get_client()` answered None —
    configured by its own account, unable to complete a turn, with neither front door offering a fix.
    Asking about the ACTIVE provider is `llm._resolve_key`'s own question, so the two cannot disagree.

    A template placeholder (`sk-...` from .env.example) is not a key, and neither is a blank row:
    counting one skipped first run and sent the person into a 401 on every turn."""
    spec = providers.get_spec()
    if not llm.looks_placeholder(os.getenv(spec.key_env, "")):
        return False
    return llm.looks_placeholder(await db.get_key(f"llm:{spec.id}:api_key") or "")


async def verify_key(provider_id: str, key: str) -> tuple[bool, str]:
    """Does this key answer? A real round trip, on a client built for it alone, storing nothing.

    Verify-then-store needs a way to ask about a CANDIDATE, and the llm-test endpoint cannot be it: it
    validates whatever is already active. So the probe builds its own client from the key it is handed and
    touches neither the keystore nor the provider setting — nothing to restore, nothing left behind. The
    MODEL matters too: one belonging to the other company 404s and reads to a person as a bad key.

    Reasoning kwargs go only to the ACTIVE provider: computed against its ladder, they turn a perfectly
    good xAI key into a 400 while OpenAI is active. An unreachable network is a REFUSAL here, unlike the
    optional voice key. Returns (store it, a sentence for the caller — never containing the key)."""
    # A pasted key arrives with whitespace around it often enough that the 401 message says so. That
    # message is never reached: whitespace cannot go in an HTTP header, so the client fails before the
    # request leaves and the failure looks exactly like an unreachable network — which sent somebody to
    # inspect a firewall over a trailing space. No API key has an edge of whitespace to lose.
    key = (key or "").strip()
    spec = providers.get_spec(provider_id)
    active = providers.active_provider_id() == spec.id
    model = llm.model_name("companion")
    if not providers.serves_model(model, spec.id):
        model = spec.default_model or model
    base_url = providers.active_base_url() if active else spec.default_base_url
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=key, **({"base_url": base_url} if base_url else {}))
    try:
        await client.responses.create(model=model, input="ping", max_output_tokens=_PROBE_TOKENS,
                                      **(llm.model_call_kwargs() if active else {}))
        return True, f"{spec.label} · {model} responded."
    except Exception as e:
        # Redact BEFORE the cut: the cut can land inside the key, and _redact matches it whole.
        detail = f"{type(e).__name__}: {_redact(str(e), key)[:160]}"
        log.warning("llm key check failed for %s: %s", spec.id, detail)
        return False, detail
    finally:
        try:
            await client.close()
        except Exception:
            pass


def _redact(text: str, key: str) -> str:
    """A provider echoing the request back is how a key reaches a log or a screen. Cheap insurance on
    the one path whose whole job is handling a key somebody just typed.

    It matches the key WHOLE, which is why the caller must redact before it truncates. Cutting first and
    redacting the stump leaves a usable prefix that no longer matches anything — the same ordering
    `/api/settings/llm-test` pins."""
    return text.replace(key, "…") if key and key in text else text


def pin_model(provider_id: str) -> str:
    """Point `model` at something PROVIDER_ID serves, and return the model now configured."""
    spec = providers.get_spec(provider_id)
    if not providers.serves_model(llm.model_name("companion"), spec.id) and spec.default_model:
        app_settings.set_runtime("model", spec.default_model)
    return llm.model_name("companion")


def model_owner(model: str, provider_id: str):
    """The OTHER provider that serves MODEL, when this one does not. `None` when nobody else claims it —
    an unknown-shaped id gets the benefit of the doubt everywhere, so it has no owner to name."""
    return next((s for s in providers.PROVIDERS.values()
                 if s.id != provider_id and providers.serves_model(model, s.id)), None)


def select_model(provider_id: str, model: str) -> str:
    """Pin `model` to one PROVIDER_ID serves, and return what is now configured. Raises ValueError for an
    empty model or one that belongs to another provider."""
    spec = providers.get_spec(provider_id)
    name = (model or "").strip()
    if not name:
        raise ValueError("model required")
    if not providers.serves_model(name, spec.id):
        owner = model_owner(name, spec.id)
        raise ValueError(f"{spec.label} does not serve {name}"
                         + (f" — {owner.label} does" if owner is not None else ""))
    app_settings.set_runtime("model", name)
    return llm.model_name("companion")


def select_provider(provider_id: str) -> tuple[str, str]:
    """Choose the brain: pin the model first, then persist the provider — the same order `kotoba setup`
    uses, so a key is never validated against a model its provider does not serve. Returns
    (provider id, model). Raises ValueError for an unknown provider."""
    if provider_id not in providers.PROVIDERS:
        raise ValueError(f"unknown provider: {provider_id}")
    model = pin_model(provider_id)
    app_settings.set_runtime("provider", provider_id)
    return provider_id, model
