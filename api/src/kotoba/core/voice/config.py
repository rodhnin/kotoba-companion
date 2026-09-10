"""Shared configuration for the outbound ElevenLabs voice layer (STT + TTS).

All traffic is OUTBOUND (local backend -> EL), which is what removes the public-URL/tunnel
requirement of the old Conversational-AI agent.

`filter_background_audio` is EL's own ambient-noise switch and is ON by default: an idle room used
to commit its own noise, and every commit bought a full agentic turn plus a memory extraction. EL
rejects it paired with `include_timestamps`, so stt_url drops the filter when a caller asks for word
timings. `language_code` stays OFF because the soul promises `language: auto`; unset, EL has been
seen labelling ambient noise as Chinese, so a configured language or KOTOBA_STT_LANGUAGE pins it."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from urllib.parse import quote, urlencode

log = logging.getLogger("kotoba.voice.config")

WS_BASE = "wss://api.elevenlabs.io"
HTTP_BASE = "https://api.elevenlabs.io"

STT_MODEL_ID = "scribe_v2_realtime"
TTS_MODEL_ID = "eleven_flash_v2_5"
# eleven_v3 performs [audio tags] but EL returns 403 for it on the stream-input WebSocket (a platform
# restriction, verified live) — it is only reachable over REST, hence the expressive engine.
EXPRESSIVE_TTS_MODEL_ID = "eleven_v3"

SAMPLE_RATE = 16000
AUDIO_FORMAT = "pcm_16000"

# commit_strategy="vad" makes the SERVER commit a transcript after end-of-turn silence — automatic
# turn-taking, no manual commit needed. Values/defaults verified against EL docs + a live probe.
COMMIT_STRATEGY = "vad"
VAD_SILENCE_THRESHOLD_SECS = 1.5
VAD_THRESHOLD = 0.4
MIN_SPEECH_DURATION_MS = 100
MIN_SILENCE_DURATION_MS = 100

FILTER_BACKGROUND_AUDIO = True
_LANGUAGE_CODE_RE = re.compile(r"^[A-Za-z]{2,3}$")

DEFAULT_VOICE_SETTINGS = {"stability": 0.5, "similarity_boost": 0.8}

# Fallback only — the active voice comes from soul config (soul/default.md frontmatter voice_id).
FALLBACK_VOICE_ID = "JTlYtJrcTzPC71hMLOxo"


class VoiceError(Exception):
    """Base for the voice layer — any EL-side failure a caller must handle."""


class VoiceAuthError(VoiceError):
    """No API key configured, or ElevenLabs rejected it."""


class VoiceQuotaError(VoiceAuthError):
    """The ElevenLabs quota is exhausted — same HTTP 401 as a rejected key, distinguished by
    detail.status == "quota_exceeded" (EL error docs), and just as unrecoverable this session.
    Subclassed so every existing VoiceAuthError path (fatal, never retried) applies unchanged."""


class VoiceStreamClosed(VoiceError):
    """The WebSocket dropped or was used after close."""


# ElevenLabs error codes that mean the CREDENTIAL was refused, so the voice is not coming back this
# session. `invalid_api_key` and `authentication_required` are what the TTS socket was observed
# sending; the rest are the 401 statuses EL's error reference lists. An UNLISTED code deliberately
# stays transient: a wrong latch costs the whole call, a missed one costs one round trip.
AUTH_ERROR_CODES = frozenset({
    "invalid_api_key", "missing_api_key", "invalid_authorization_header",
    "authentication_required", "unauthorized", "sign_in_required",
})
QUOTA_ERROR_CODE = "quota_exceeded"


def key_rejection(code: str, detail: str = "") -> VoiceAuthError | None:
    """The fatal error an ElevenLabs error CODE means, or None when it is an ordinary stream fault.

    Both realtime sockets answer a refused key in-band rather than at the handshake — STT with
    `{"message_type":"auth_error",…}`, TTS with `{"error":"invalid_api_key","code":1008}` right
    before closing 1008 (verified live). It draws the same line the REST engine draws on HTTP 401
    (tts_rest._synth): quota_exceeded is its own class, every other refusal is VoiceAuthError."""
    name = (code or "").strip().lower()
    text = (detail or "").strip()
    if QUOTA_ERROR_CODE in name or QUOTA_ERROR_CODE in text.lower():
        return VoiceQuotaError(f"ElevenLabs quota exhausted: {(text or name)[:200]}")
    if name in AUTH_ERROR_CODES:
        return VoiceAuthError(f"ElevenLabs rejected the API key: {(text or name)[:200]}")
    return None


def handshake_rejection(exc: Exception) -> VoiceAuthError | None:
    """Same verdict for a handshake the server refused outright, or None to leave it transient.

    Defensive rather than observed: EL accepts the upgrade and refuses in-band, but a proxy in front
    of it can answer 401 itself. websockets >= 14 raises `InvalidStatus` (the legacy
    `InvalidStatusCode` belongs to the old asyncio implementation and `websockets.connect` never
    raises it), carrying the status on `.response` and EL's JSON detail on `.response.body`, which
    `str(exc)` drops. Only 401 latches: websockets' own `process_exception` already treats 5xx as
    retryable, and a 403 on this socket is eleven_v3 being refused the endpoint, not a bad key."""
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) != 401:
        return None
    detail = bytes(getattr(response, "body", b"") or b"").decode("utf-8", errors="replace")
    return key_rejection("unauthorized", detail)


def own_cancellation_swallowed() -> bool:
    """True when a broad teardown `except` just caught the CURRENT task's own CancelledError rather
    than the awaited task's — the same `me.cancelling()` discipline turns.supersede uses. Teardown
    callers note it, finish closing, and re-raise at the end, so a barge-in landing mid-recovery can
    neither leave a zombie turn nor abandon a half-closed client."""
    me = asyncio.current_task()
    return me is not None and bool(me.cancelling())


# In-memory key override (future Settings panel), same discipline as the provider keys:
# memory only — never logged, never returned to a caller.
_api_key: str = ""


def set_api_key(plaintext: str | None) -> None:
    """The key first run saved, live in this process. Called by `core.engine._preload_voice_key` at
    startup and by the wizard the moment it accepts one, so a key stored in the app is a key that
    works — memory only, never logged, never handed back to a caller."""
    global _api_key
    _api_key = plaintext or ""


def resolve_api_key() -> str:
    """Active ElevenLabs key: in-app saved key > env fallback > ''.

    A placeholder in the environment counts as NO key. `.env.example` ships `ELEVENLABS_API_KEY=el_...`,
    and copied unedited that string used to be handed to ElevenLabs as if it were real — she went silent
    with the 401 buried in a log, which is the same trap the LLM key had. Only the env path is filtered,
    and the in-app path needs no filter of its own: the only thing that writes it is the setup wizard,
    which refuses a placeholder before it ever reaches the keystore.
    `core.llm.looks_placeholder` is the single rule for both keys, and it already knows the `el_` prefix."""
    if _api_key:
        return _api_key
    from kotoba.core.llm import looks_placeholder

    env = os.getenv("ELEVENLABS_API_KEY", "") or ""
    return "" if looks_placeholder(env) else env


def auth_headers() -> dict[str, str]:
    """The header, or a VoiceAuthError that says which of the two problems it is — an absent key and an
    unedited template need different fixes, and 'no key configured' sends someone hunting for a key they
    can see sitting in their own .env."""
    key = resolve_api_key()
    if not key:
        from kotoba.core.llm import looks_placeholder

        raw = os.getenv("ELEVENLABS_API_KEY", "") or ""
        if raw and looks_placeholder(raw):
            raise VoiceAuthError(
                "ELEVENLABS_API_KEY still holds the placeholder copied from .env.example — run "
                "`kotoba setup` to put your real ElevenLabs key in, or edit the variable"
            )
        raise VoiceAuthError("No ElevenLabs API key configured — run `kotoba setup`")
    return {"xi-api-key": key}


def default_voice_id(soul: dict | None = None) -> str:
    """Voice for TTS: soul-config voice_id > KOTOBA_VOICE_ID env > built-in fallback.
    Callers with DB access pass `await db.fetch_soul_config()` as `soul`."""
    if soul and (soul.get("voice_id") or "").strip():
        return str(soul["voice_id"]).strip()
    return os.getenv("KOTOBA_VOICE_ID", "").strip() or FALLBACK_VOICE_ID


def stt_language() -> str:
    """Language to pin realtime transcription to (ISO 639-1/639-3), or "" for EL auto-detection.
    Junk is refused rather than sent: an unparseable code would be silently ignored by the server,
    leaving the caller believing the session was pinned."""
    raw = os.getenv("KOTOBA_STT_LANGUAGE", "").strip()
    if raw and not _LANGUAGE_CODE_RE.match(raw):
        log.warning("ignoring KOTOBA_STT_LANGUAGE=%r — expected an ISO 639-1/639-3 code", raw)
        return ""
    return raw.lower()


def stt_language_for(soul_language: str | None) -> str:
    """What to pin realtime transcription to: the env override first, then HER OWN configured language,
    then "" for ElevenLabs auto-detection.

    Settings → Personality has always shown a Language control, and it only ever chose how she REPLIES.
    Transcription kept auto-detecting, so picking "Spanish" did nothing about EL hearing a short Spanish
    sentence as Portuguese — and the transcript is the whole of what she gets, so she answered in
    Portuguese. A visible control that cannot fix the thing it is named after is worse than no control.
    `auto` still means auto on both sides, and KOTOBA_STT_LANGUAGE still overrides both."""
    pinned = stt_language()
    if pinned:
        return pinned
    raw = (soul_language or "").strip().lower()
    if not raw or raw == "auto":
        return ""
    if not _LANGUAGE_CODE_RE.match(raw):
        log.warning("soul language %r is not an ISO 639-1/639-3 code — transcription stays on auto", raw)
        return ""
    return raw


def stt_filter_background_audio() -> bool:
    raw = os.getenv("KOTOBA_STT_FILTER_BACKGROUND", "").strip().lower()
    if not raw:
        return FILTER_BACKGROUND_AUDIO
    return raw in ("1", "true", "yes", "on")


def tts_engine() -> str:
    """Active TTS engine for the local voice path: "expressive" (eleven_v3 REST, tags performed —
    default) or "fast" (flash WS, tags stripped). Live-settable from the panel."""
    from kotoba.core import app_settings

    value = app_settings.runtime_value("tts_engine", "KOTOBA_TTS_ENGINE", "expressive").strip().lower()
    return value if value in ("expressive", "fast") else "expressive"


def expressive_voice_settings() -> dict:
    """Optional voice_settings for the expressive engine; omitted (EL server defaults) unless set —
    v3 only accepts certain values (e.g. stability 0.0/0.5/1.0), so nothing is guessed here."""
    out: dict = {}
    for key, env in (
        ("stability", "KOTOBA_TTS_STABILITY"),
        ("similarity_boost", "KOTOBA_TTS_SIMILARITY"),
        ("style", "KOTOBA_TTS_STYLE"),
    ):
        raw = os.getenv(env, "").strip()
        if raw:
            try:
                out[key] = float(raw)
            except ValueError:
                pass
    return out


def tts_rest_url(voice_id: str, *, output_format: str | None = None) -> str:
    params: dict = {}
    if output_format:
        params["output_format"] = output_format
    qs = f"?{urlencode(params)}" if params else ""
    return f"{HTTP_BASE}/v1/text-to-speech/{quote(voice_id, safe='')}/stream{qs}"


def ws_close_code(exc: Exception) -> int:
    """Close code of a ConnectionClosed without the deprecated .code property (1006 = no close frame)."""
    rcvd = getattr(exc, "rcvd", None)
    return rcvd.code if rcvd else 1006


def stt_url(
    *,
    model_id: str = STT_MODEL_ID,
    audio_format: str = AUDIO_FORMAT,
    commit_strategy: str = COMMIT_STRATEGY,
    language_code: str | None = None,
    include_timestamps: bool = False,
    include_language_detection: bool = False,
    filter_background_audio: bool = False,
    vad_silence_threshold_secs: float = VAD_SILENCE_THRESHOLD_SECS,
    vad_threshold: float = VAD_THRESHOLD,
    min_speech_duration_ms: int = MIN_SPEECH_DURATION_MS,
    min_silence_duration_ms: int = MIN_SILENCE_DURATION_MS,
    keyterms: list[str] | None = None,
) -> str:
    params: dict = {"model_id": model_id, "audio_format": audio_format, "commit_strategy": commit_strategy}
    if commit_strategy == "vad":
        params["vad_silence_threshold_secs"] = vad_silence_threshold_secs
        params["vad_threshold"] = vad_threshold
        params["min_speech_duration_ms"] = min_speech_duration_ms
        params["min_silence_duration_ms"] = min_silence_duration_ms
    if language_code:
        params["language_code"] = language_code
    # A name the transcriber has no dictionary for comes back spelled how it sounded. Naming it up
    # front is the difference between being called and being missed. It is not free: the service
    # charges more per minute for keyterm prompting. Capped at what realtime accepts, 50 x 20.
    extra: list[tuple[str, str]] = []
    for term in (keyterms or [])[:50]:
        clean = str(term).strip()[:20]
        if clean:
            extra.append(("keyterms", clean))
    if include_timestamps:
        params["include_timestamps"] = "true"
    if include_language_detection:
        params["include_language_detection"] = "true"
    if filter_background_audio and not include_timestamps:
        params["filter_background_audio"] = "true"
    elif filter_background_audio:
        log.warning("filter_background_audio dropped — ElevenLabs refuses it alongside include_timestamps")
    # A list of pairs, never the dict: keyterms repeats one key, which a dict cannot express.
    return f"{WS_BASE}/v1/speech-to-text/realtime?{urlencode(list(params.items()) + extra)}"


def tts_url(
    voice_id: str,
    *,
    model_id: str = TTS_MODEL_ID,
    output_format: str | None = None,
    inactivity_timeout: int | None = None,
) -> str:
    params: dict = {"model_id": model_id}
    if output_format:
        params["output_format"] = output_format
    if inactivity_timeout:
        # EL closes the socket after 20s without input text; raiseable to 180. We send 60
        # (voice.session.TTS_INACTIVITY_TIMEOUT) — neither vendor number is a constant here.
        params["inactivity_timeout"] = inactivity_timeout
    return f"{WS_BASE}/v1/text-to-speech/{quote(voice_id, safe='')}/stream-input?{urlencode(params)}"
