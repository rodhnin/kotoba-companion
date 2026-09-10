"""Pydantic request/response models for the Kotoba backend."""
from __future__ import annotations

import re

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


_WELL_FORMED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ChatRequest(BaseModel):
    """OpenAI-compatible body ElevenLabs POSTs to /v1/chat/completions.

    `extra="allow"` so we capture whatever ElevenLabs nests via `customLlmExtraBody` (the exact
    location is SDK/version-dependent). `resolve_session_id()` digs it out wherever it lands.
    """

    model_config = ConfigDict(extra="allow")

    model: str = "kotoba"
    messages: list[dict] = Field(default_factory=list)
    stream: bool = True
    session_id: Optional[str] = None

    def resolve_session_id(self) -> Optional[str]:
        """Find the frontend session id wherever ElevenLabs put it.

        A shape we did not mint is not honoured: it is echoed into the log, and a newline inside one
        forges a line there. Refusing the whole request would drop a live call for a field that only
        routes events, so an unusable id reads as absent and the caller is given a fresh one."""
        if self.session_id and _WELL_FORMED.match(self.session_id):
            return self.session_id
        extra = self.model_extra or {}
        for key in ("session_id", "sessionId"):
            v = extra.get(key)
            if v and _WELL_FORMED.match(str(v)):
                return str(v)
        # ElevenLabs nests the custom-LLM extra body under `elevenlabs_extra_body` — read off real
        # voice-call bodies, not their docs. The other three are SDK-version fallbacks.
        for container in (
            "elevenlabs_extra_body",
            "extra_body",
            "custom_llm_extra_body",
            "customLlmExtraBody",
        ):
            c = extra.get(container)
            if isinstance(c, dict):
                v = c.get("session_id") or c.get("sessionId")
                if v and _WELL_FORMED.match(str(v)):
                    return str(v)
        return None

    def extra_keys(self) -> list[str]:
        """Top-level keys ElevenLabs sent beyond the standard ones (for debug logging)."""
        return sorted((self.model_extra or {}).keys())
