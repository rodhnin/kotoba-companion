"""Persisted app settings the user controls from the Settings panel (~/.kotoba/settings.yaml).

`disabled_toolsets` is which tool families are off; `runtime` holds user overrides that shadow the
matching KOTOBA_* env var, so the panel changes behaviour live without editing .env. They apply on the
next request because every call site reads through `runtime_value(...)` — override > env > default.

`_RUNTIME_SPEC` is also the ALLOWLIST: set_runtime refuses any key outside it, so the panel cannot
write arbitrary entries into settings.yaml. Its validators run on READ as well as write — a hand-edited
`sandbox: rocket` silently disabled execution while the panel displayed "local". The registry import is
function-local: at module scope it boots the tool registry mid-body and leaves this half-built."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

import yaml

from kotoba.core import atomic_file, transport
from kotoba.paths import home_dir

log = logging.getLogger("kotoba.settings")


def _one_of(*allowed: str) -> Callable[[Any], str]:
    def v(x: Any) -> str:
        s = str(x).strip().lower()
        if s not in allowed:
            raise ValueError(f"must be one of {allowed}")
        return s
    return v


def _bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    return str(x).strip().lower() in ("1", "true", "yes", "on")


def _str(x: Any) -> str:
    # Reject before str(): JSON `null` stringifies to "None", which then ships as a MODEL NAME.
    if not isinstance(x, (str, int, float)) or isinstance(x, bool):
        raise ValueError("must be text")
    return str(x).strip()


def _str_req(x: Any) -> str:
    s = _str(x)
    if not s:
        raise ValueError("cannot be empty")
    return s


def _finite(x: Any) -> float:
    """float(x) accepted inf and nan: an infinite work_timeout is a task that can never be reclaimed, and
    nan loses every comparison, so the deadline check silently never fires. int(inf) raises, which turned
    a bad value into a 500 instead of a 400."""
    n = float(x)
    if n != n or n in (float("inf"), float("-inf")):
        raise ValueError("must be a finite number")
    return n


def _int_min(lo: int) -> Callable[[Any], int]:
    def v(x: Any) -> int:
        n = int(_finite(x))
        if n < lo:
            raise ValueError(f"must be ≥ {lo}")
        return n
    return v


def _num_min(lo: float) -> Callable[[Any], float]:
    def v(x: Any) -> float:
        n = _finite(x)
        if n < lo:
            raise ValueError(f"must be ≥ {lo}")
        return n
    return v


def _base_url(x: Any) -> str:
    """Empty (SDK default) or an http(s) URL. Soft validation — reject obvious junk, allow any host so
    gateways / local endpoints work."""
    s = str(x).strip()
    if s and not s.startswith(("http://", "https://")):
        raise ValueError("base_url must start with http:// or https:// (or be empty)")
    return s


# A REASONING model on purpose, and the single source of the default (core.llm reads it from here).
# Luna over gpt-5.4-mini: 3.75x less per output token on all three axes, 2.6x the context, and a
# live probe confirmed streaming, tool calls and encrypted reasoning under store=False.
DEFAULT_MODEL = "gpt-5.6-luna"
# 'low', not unset. Unset sent no `reasoning` block at all, which cost MORE (the provider applies its own
# default, a step up) and dropped `store=False` with it, so a fresh install left its reasoning retained at
# the provider. It also read as "not a reasoning model", which switched the canned English narration back
# on in every language. `normalize_effort` lifts it to the provider's lowest when a model has no 'low'.
DEFAULT_REASONING_EFFORT = "low"

_RUNTIME_SPEC: dict[str, tuple[str, Any, Callable[[Any], Any]]] = {
    "provider": ("KOTOBA_LLM_PROVIDER", "openai", _one_of("openai", "xai")),
    "base_url": ("KOTOBA_LLM_BASE_URL", "", _base_url),
    "model": ("KOTOBA_MODEL", DEFAULT_MODEL, _str_req),  # the companion model — an empty one has no fallback
    "work_model": ("KOTOBA_WORK_MODEL", "", _str),
    # Per-role models: empty = inherit via the fallback chain in core.llm.model_name().
    "code_model": ("KOTOBA_CODE_MODEL", "", _str),
    "research_model": ("KOTOBA_RESEARCH_MODEL", "", _str),
    "utility_model": ("KOTOBA_UTILITY_MODEL", "", _str),
    "reasoning_effort": ("KOTOBA_REASONING_EFFORT", DEFAULT_REASONING_EFFORT, _one_of("off", "", "minimal", "low", "medium", "high", "xhigh", "max")),
    "expressive": ("KOTOBA_EXPRESSIVE", True, _bool),
    "elevenlabs_agent_id": ("NEXT_PUBLIC_ELEVENLABS_AGENT_ID", "", _str),
    # "local" = our /api/voice WS, works on a fresh clone with just an EL key; "agent" = EL's cloud,
    # which must reach our /v1, so it needs a public URL and a dashboard-configured agent.
    "voice_mode": ("KOTOBA_VOICE_MODE", "local", _one_of("agent", "local")),
    # "expressive" = eleven_v3 REST, performs [audio tags], ~1s more latency; "fast" = flash WS, strips tags.
    "tts_engine": ("KOTOBA_TTS_ENGINE", "expressive", _one_of("expressive", "fast")),
    "sandbox": ("KOTOBA_SANDBOX", "local", _one_of("local", "docker", "none")),
    "work_timeout": ("KOTOBA_WORK_TIMEOUT", transport.WORK_TIMEOUT_SECONDS, _num_min(30)),
    "work_max_iter": ("KOTOBA_WORK_MAX_ITER", 40, _int_min(1)),
    "work_max_tool_calls": ("KOTOBA_WORK_MAX_TOOL_CALLS", 40, _int_min(1)),
    "work_fail_limit": ("KOTOBA_WORK_FAIL_LIMIT", 6, _int_min(1)),
    # `trust` and `browser_cdp` are deliberately absent: a live browser_cdp change desyncs an
    # already-launched browser, so both stay read-only until they have a live-safe read path.
}


def settings_path() -> Path:
    return Path(
        os.getenv("KOTOBA_SETTINGS", str(home_dir() / "settings.yaml"))
    ).expanduser()


class SettingsUnreadable(RuntimeError):
    """The file is there and cannot be parsed. Every caller has to hear that, because the two silent
    answers are both wrong: serving defaults turns the sandbox back to `local` and re-enables the
    tool families somebody switched OFF, and writing over it then makes that the saved truth."""


def _load(strict: bool = False) -> dict:
    p = settings_path()
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("%s cannot be parsed (%s) — using defaults and refusing to overwrite it", p, exc)
        if strict:
            raise SettingsUnreadable(str(exc)) from exc
        return {}
    # A non-MAPPING top level sails through `or {}` and then AttributeErrors out of every caller.
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    atomic_file.write_text(settings_path(), yaml.safe_dump(data, sort_keys=True, default_flow_style=False))


def _exclusive():
    """The SHARED primitive, not a private copy. The copy was not re-entrant and derived its lock name
    with `with_suffix(".yaml.lock")`, so a KOTOBA_SETTINGS not ending in `.yaml` locked a different file
    than anything using core.atomic_file — i.e. no mutual exclusion at all."""
    return atomic_file.exclusive(settings_path())


def saved_disabled_toolsets() -> set[str]:
    """Toolset families the user has switched off, straight from the file.

    Read by core.plugins BEFORE it executes anything, which is the only moment the choice can still
    stop a plugin's module body from running. It cannot ask the registry at that point: discover()
    fires at import of kotoba.tools and apply_on_startup() only lands in engine.start(), so the
    in-memory set is still empty. The file is the one answer available that early."""
    return {str(ts) for ts in (_load().get("disabled_toolsets") or []) if str(ts).strip()}


def apply_on_startup() -> None:
    """Re-apply saved toolset toggles to the registry when the app boots."""
    from kotoba.tools import registry

    for ts in saved_disabled_toolsets():
        registry.set_toolset_enabled(ts, False)


def set_toolset_enabled(toolset: str, enabled: bool) -> None:
    """Persist the choice, THEN apply it to the registry — in that order.

    The file is the durable answer and the registry the volatile mirror of it, and applying first
    was a live hazard once the registry started acting on a plugin toggle: re-enabling called
    plugins.load_plugin while settings.yaml still said disabled, and the loader (which unions both
    sources, because at boot only the file has an answer) refused the very load the user had just
    asked for. Failing the other way round leaves a choice recorded and unapplied, which the next
    start corrects by itself."""
    from kotoba.tools import registry

    with _exclusive():  # the WHOLE read-modify-write, or a concurrent toggle is lost
        data = _load()
        disabled = set(data.get("disabled_toolsets", []) or [])
        if enabled:
            disabled.discard(toolset)
        else:
            disabled.add(toolset)
        data["disabled_toolsets"] = sorted(disabled)
        _save(data)
    registry.set_toolset_enabled(toolset, enabled)


def _runtime() -> dict:
    r = _load().get("runtime") or {}
    return r if isinstance(r, dict) else {}


_CONSUMER_CLAMPS = frozenset({"reasoning_effort"})


def runtime_value(key: str, env_var: str, default: Any) -> str:
    """Effective value for a runtime-settable key, as a STRING: a user override in settings.yaml wins,
    else the env var, else `default`. Keys outside _RUNTIME_SPEC pass through untouched.

    The validator runs on BOTH halves. Guarding the file alone reproduced the bug it exists for: with
    `KOTOBA_SANDBOX=rocket`, Settings and doctor rendered `local` while this returned `rocket` to the
    backend picker — no backend, execution silently off, every surface reporting a working sandbox.

    `_CONSUMER_CLAMPS` names keys whose consumer answers a bad value BETTER than "unset" does, and gets
    the env half raw. `reasoning_effort` is the one: normalize_effort folds anything off the ladder
    down, while treating it as unset would flip is_reasoning_model off and bring canned English back."""
    spec = _RUNTIME_SPEC.get(key)
    ov = _runtime().get(key)
    if ov is not None:
        if spec is None:
            return str(ov)
        try:
            return str(spec[2](ov))
        except Exception:
            log.warning("settings.yaml has an invalid %r (%r) — falling back", key, ov)
    env = os.getenv(env_var)
    # Blank is UNSET, except where the key's own vocabulary gives "" a meaning: `reasoning_effort`
    # accepts it as a second spelling of `off`, pinned. Everywhere else a blanked line used to be read
    # as an answer, which is how `KOTOBA_EXPRESSIVE=` dropped the audio tags with nothing to say so.
    if env is None or (not env.strip() and key not in _CONSUMER_CLAMPS):
        return str(default)
    if spec is None or key in _CONSUMER_CLAMPS:
        return env
    try:
        return str(spec[2](env))
    except Exception:
        log.warning("%s is not a usable %r (%r) — using the default", env_var, key, env)
        return str(default)


def runtime_all() -> dict:
    """Current effective values for every settable key (override > env > default) — for build_settings so
    the panel can render each control already populated. Native types where it helps the UI (bool/number)."""
    out: dict[str, Any] = {}
    ov = _runtime()
    for key, (env_var, default, validate) in _RUNTIME_SPEC.items():
        if key in ov:
            raw: Any = ov[key]
        elif os.getenv(env_var) is not None and (
                os.getenv(env_var, "").strip() or key in _CONSUMER_CLAMPS):
            raw = os.getenv(env_var)
        else:
            raw = default
        try:
            out[key] = validate(raw)
        except Exception:
            out[key] = default
    return out


def audio_tags_enabled() -> bool:
    """SINGLE source for whether [audio tags] are in play: the prompt asks for them (soul.prompt) and the
    stream filter keeps them (core.stream) only when this is True — both delegate here so they can never
    desync. `expressive` OFF always wins. In LOCAL voice mode the fast engine (flash) deletes every tag,
    so asking for them would burn prompt + reply tokens for a flat voice; in AGENT mode the performer is
    the user's EL dashboard agent, whose model we cannot see, so `expressive` alone decides."""
    values = runtime_all()
    if not values["expressive"]:
        return False
    if values["voice_mode"] != "local":
        return True
    return values["tts_engine"] != "fast"


def set_runtime(key: str, value: Any) -> Any:
    """Validate + persist one runtime override. Returns the normalized stored value. Raises ValueError for
    an unknown key or an invalid value (caller maps to HTTP 400). Applies live: call sites read on next use."""
    if key not in _RUNTIME_SPEC:
        raise ValueError(f"unknown setting: {key}")
    _env, _default, validate = _RUNTIME_SPEC[key]
    normalized = validate(value)
    with _exclusive():
        # One tab in the file used to end here as `{}` plus this one key — every other setting gone.
        data = _load(strict=True)
        runtime = dict(data.get("runtime") or {})
        runtime[key] = normalized
        data["runtime"] = runtime
        _save(data)
    return normalized
