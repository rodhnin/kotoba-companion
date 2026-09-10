"""ask_user must NOT block the voice turn. It opens the input card and returns guidance immediately; the
typed value comes back as a normal user turn (frontend sendUserMessage), so the turn never hangs waiting
(a long wait inside the ElevenLabs voice turn kills the WebSocket — the "form crashed the call" bug)."""
from __future__ import annotations


import asyncio

import kotoba.core.interaction as interaction
import kotoba.tools.builtin.ask_user as ask_user
from kotoba.tools import ToolContext





def _patch_emits(monkeypatch):
    emitted = []

    async def fake_emit_task(session_id, kind, **data):
        emitted.append((kind, data))

    async def fake_emit_emotion(session_id, emotion):
        pass

    monkeypatch.setattr(interaction, "emit_task", fake_emit_task)
    monkeypatch.setattr(interaction, "emit_emotion", fake_emit_emotion)
    return emitted


def test_ask_user_opens_card_and_returns_without_blocking(monkeypatch):
    emitted = _patch_emits(monkeypatch)
    ctx = ToolContext(db=None, session_id="s1", mode="companion")
    out = asyncio.run(ask_user.execute({"prompt": "Paste the repo link", "kind": "link"}, ctx))
    # returned immediately with guidance (did NOT wait for a typed value)
    assert out and ("type" in out.lower() or "text box" in out.lower() or "screen" in out.lower())
    # showed the input card
    assert any(k == "need_input" and d.get("mode") == "input" for k, d in emitted)
    # crucially: no Future left waiting → the turn is not blocked
    assert interaction.has_pending("s1") is False


def test_ask_user_key_with_name_mentions_secure_and_never_blocks(monkeypatch):
    emitted = _patch_emits(monkeypatch)
    ctx = ToolContext(db=None, session_id="s2", mode="companion")
    out = asyncio.run(ask_user.execute({"prompt": "Your API key", "kind": "key", "name": "my_openai"}, ctx))
    assert out and "secure" in out.lower()
    assert interaction.has_pending("s2") is False
    # card was opened (non-blocking)
    assert any(k == "need_input" and d.get("mode") == "input" for k, d in emitted)


def test_ask_user_key_without_name_does_not_open_card(monkeypatch):
    emitted = _patch_emits(monkeypatch)
    ctx = ToolContext(db=None, session_id="s3", mode="companion")
    out = asyncio.run(ask_user.execute({"prompt": "Your API key", "kind": "key"}, ctx))
    # returns an error telling the model to supply a name — not the "secure" guidance
    assert out and "name" in out.lower()
    # no card was opened — opening it would silently drop the typed value
    assert not any(k == "need_input" for k, d in emitted)
    assert interaction.has_pending("s3") is False
