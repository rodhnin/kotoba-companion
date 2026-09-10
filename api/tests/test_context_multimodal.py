"""Multimodal passthrough — an image content part from ElevenLabs survives into the model input as a
Responses API `input_image`, instead of being flattened to a string and lost (so gpt-4o-mini can see it).
Plus text_of() renders content for DB logging without dumping raw base64."""
from __future__ import annotations

from kotoba.core.context import _history_from_request, _normalize_content, text_of


def test_plain_string_unchanged():
    assert _normalize_content("hola") == "hola"


def test_openai_chat_image_url_becomes_input_image():
    content = [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
    ]
    out = _normalize_content(content)
    assert isinstance(out, list)
    assert {"type": "input_text", "text": "what is this?"} in out
    assert {"type": "input_image", "image_url": "https://x/y.png"} in out


def test_flat_input_image_and_data_url():
    assert {"type": "input_image", "image_url": "data:image/png;base64,QUJD"} in _normalize_content(
        [{"type": "input_image", "image_url": "data:image/png;base64,QUJD"}]
    )
    # alternative shapes ElevenLabs might use
    assert {"type": "input_image", "image_url": "data:image/png;base64,ZZ"} in _normalize_content(
        [{"type": "image", "data": "data:image/png;base64,ZZ"}]
    )


def test_no_image_collapses_to_plain_text():
    out = _normalize_content([{"type": "text", "text": "a"}, {"type": "input_text", "text": "b"}])
    assert out == "a b"  # back-compat: plain string when there's no image


def test_history_preserves_image_part():
    msgs = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": [
            {"type": "text", "text": "read this"},
            {"type": "image_url", "image_url": {"url": "https://x/img.jpg"}},
        ]},
    ]
    hist = _history_from_request(msgs)
    assert hist[0] == {"role": "user", "content": "earlier"}
    last = hist[-1]
    assert last["role"] == "user" and isinstance(last["content"], list)
    assert any(p.get("type") == "input_image" for p in last["content"])


def test_text_of_never_dumps_base64():
    content = [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 5000}},
    ]
    rendered = text_of(content)
    assert "look" in rendered and "[image]" in rendered
    assert "AAAA" not in rendered and len(rendered) < 50  # no base64 blob in the DB/FTS
