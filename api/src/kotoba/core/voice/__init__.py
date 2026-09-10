"""Outbound ElevenLabs voice layer: the LOCAL backend calls EL (STT + TTS) — no inbound
traffic, no public URL, no tunnel. The agentic loop stays ours; only audio crosses the wire."""
from kotoba.core.voice.config import (
    VoiceAuthError,
    VoiceError,
    VoiceStreamClosed,
    default_voice_id,
    resolve_api_key,
    set_api_key,
)
from kotoba.core.voice.stt import SttClient, SttCommitted, SttError, SttEvent, SttPartial, SttSessionStarted
from kotoba.core.voice.tts import TtsClient, synthesize

__all__ = [
    "VoiceAuthError", "VoiceError", "VoiceStreamClosed",
    "default_voice_id", "resolve_api_key", "set_api_key",
    "SttClient", "SttCommitted", "SttError", "SttEvent", "SttPartial", "SttSessionStarted",
    "TtsClient", "synthesize",
]
