"""A bare image attachment (no caption) must NOT carry a hardcoded English prompt — that made her give a
generic English description and break the ongoing scene/language. The frontend sends a `__image_only__`
sentinel; load_context drops it (image-only user turn) and injects a developer note telling her to react
IN CONTEXT and in the SAME language. A real typed caption is kept untouched."""
from __future__ import annotations

import asyncio

import kotoba.core.context as ctx
from kotoba.core import attachments
from kotoba.models.schemas import ChatRequest


class _DB:
    async def fetch_recent_turns(self, *a, **k): return []
    async def fetch_soul_config(self): return {"name": "Kotoba", "language": "auto"}
    async def fetch_user_profile_as_markdown(self): return ""


def _img_part():
    return {"type": "input_image", "image_url": "data:image/png;base64,QUJD"}


def _load(messages, session_id):
    req = ChatRequest(messages=messages)
    return asyncio.run(ctx.load_context(req, _DB(), session_id, consume_attachments=True))


def test_bare_image_sentinel_dropped_and_context_note_added(monkeypatch):
    monkeypatch.setattr(attachments, "take", lambda sid: [_img_part()])
    messages = [
        {"role": "user", "content": "vamos a hacer un roleplay"},
        {"role": "assistant", "content": "¡dale!"},
        {"role": "user", "content": "__image_only__"},
    ]
    items = _load(messages, "s1")
    # the sentinel text never reaches the model
    assert all("__image_only__" not in str(m.get("content")) for m in items)
    # a developer note tells her to stay in context + language (not a generic English description)
    joined = " ".join(m["content"] for m in items if m["role"] == "developer").lower()
    assert "language" in joined and ("image" in joined or "shared" in joined)
    # the image part still reaches the model on the last user turn
    last_user = [m for m in items if m["role"] == "user"][-1]
    assert any(p.get("type") == "input_image" for p in last_user["content"])


def test_real_caption_is_preserved(monkeypatch):
    monkeypatch.setattr(attachments, "take", lambda sid: [_img_part()])
    messages = [{"role": "user", "content": "¿qué dice este meme?"}]
    items = _load(messages, "s2")
    last_user = [m for m in items if m["role"] == "user"][-1]
    texts = [p.get("text") for p in last_user["content"] if isinstance(p, dict) and p.get("type") == "input_text"]
    assert "¿qué dice este meme?" in texts  # a genuine caption is kept
