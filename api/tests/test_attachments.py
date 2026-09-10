"""Backend-routed attachments — image/PDF the user uploads to OUR endpoint get injected into the next
user turn as Responses API parts (input_image / input_file), since ElevenLabs uploadFile can't reach a
custom LLM. One-shot per session."""
from __future__ import annotations

import asyncio

from kotoba.core import attachments


def test_add_take_is_one_shot():
    attachments._pending.clear()
    attachments.add("s1", {"type": "input_image", "image_url": "data:image/png;base64,AAA"})
    assert attachments.has("s1")
    got = attachments.take("s1")
    assert got == [{"type": "input_image", "image_url": "data:image/png;base64,AAA"}]
    assert not attachments.has("s1")          # consumed
    assert attachments.take("s1") == []        # nothing left


def test_cap_per_session():
    attachments._pending.clear()
    for i in range(10):
        attachments.add("s2", {"type": "input_image", "image_url": f"data:image/png;base64,{i}"})
    assert len(attachments.take("s2")) == attachments.MAX_PER_SESSION


class _FakeDB:
    async def fetch_recent_turns(self, *a, **k): return []
    async def fetch_soul_config(self): return {"name": "Kotoba", "personality": "warm"}
    async def fetch_user_profile_as_markdown(self): return ""


def test_load_context_injects_image_into_last_user_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    from kotoba.models.schemas import ChatRequest
    from kotoba.core.context import load_context

    attachments._pending.clear()
    attachments.add("sx", {"type": "input_image", "image_url": "data:image/png;base64,IMG"})

    req = ChatRequest(messages=[
        {"role": "user", "content": "what is this?"},
    ], session_id="sx")
    items = asyncio.run(load_context(req, _FakeDB(), "sx"))

    last = items[-1]
    assert last["role"] == "user" and isinstance(last["content"], list)
    types = [p.get("type") for p in last["content"]]
    assert "input_text" in types and "input_image" in types
    assert any(p.get("image_url") == "data:image/png;base64,IMG" for p in last["content"])
    assert not attachments.has("sx")  # consumed by the turn


def test_load_context_silence_turn_does_not_consume_attachment(monkeypatch, tmp_path):
    """A non-typed (silence) turn must NOT consume the pending attachment — otherwise an ElevenLabs
    silence turn steals it before the user's real message turn arrives (the PDF-not-received bug)."""
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    from kotoba.models.schemas import ChatRequest
    from kotoba.core.context import load_context

    attachments._pending.clear()
    attachments.add("sy", {"type": "input_file", "filename": "a.pdf", "file_data": "data:application/pdf;base64,PDF"})
    req = ChatRequest(messages=[{"role": "user", "content": "..."}], session_id="sy")

    items = asyncio.run(load_context(req, _FakeDB(), "sy", consume_attachments=False))
    assert isinstance(items[-1]["content"], str)        # not turned into parts
    assert attachments.has("sy")                         # STILL pending — preserved for the real turn

    # the real typed turn then consumes it
    items2 = asyncio.run(load_context(req, _FakeDB(), "sy", consume_attachments=True))
    assert any(p.get("type") == "input_file" for p in items2[-1]["content"])
    assert not attachments.has("sy")
