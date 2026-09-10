"""Voice latency guard: ElevenLabs resends the FULL conversation every turn; on a long call that bloats
the prompt and the reasoning model's first-token latency grows until ElevenLabs drops the WebSocket.
_history_from_request caps the history we feed the model to the most recent N turns."""
from __future__ import annotations

from kotoba.core.context import _history_from_request, is_trigger_sentinel


def test_is_trigger_sentinel():
    # Pure proactive-speech triggers the frontend injects to make her announce — NOT real user content.
    assert is_trigger_sentinel("__work_done__")
    assert is_trigger_sentinel("  __reminder__ ")
    # attachment sentinels are NOT triggers (they pair with a real file → handled elsewhere)
    assert not is_trigger_sentinel("__image_only__")
    assert not is_trigger_sentinel("__file_only__")
    assert not is_trigger_sentinel("hello")
    assert not is_trigger_sentinel("")


def test_history_drops_trigger_sentinel_keeps_attachment_sentinel():
    # __work_done__ must NOT reach the model as a user turn (it'd pollute context + get echoed); the work
    # note drives the announcement. __image_only__ stays (the attachment handler keys off it).
    msgs = [
        {"role": "user", "content": "real question"},
        {"role": "assistant", "content": "an answer"},
        {"role": "user", "content": "__work_done__"},
    ]
    out = _history_from_request(msgs)
    assert all(m["content"] != "__work_done__" for m in out)
    assert [m["content"] for m in out] == ["real question", "an answer"]

    keep = _history_from_request([{"role": "user", "content": "__image_only__"}])
    assert keep == [{"role": "user", "content": "__image_only__"}]


def test_history_capped_to_recent_turns():
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(60)]
    out = _history_from_request(msgs)
    assert len(out) <= 24                      # bounded — does not grow with the whole call
    assert out[-1]["content"] == "m59"         # keeps the MOST RECENT turns (drops the oldest)


def test_history_short_conversation_unchanged():
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}]
    out = _history_from_request(msgs)
    assert [m["content"] for m in out] == ["hi", "hey"]
