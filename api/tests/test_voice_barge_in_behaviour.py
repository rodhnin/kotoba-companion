"""Barge-in still behaves, now that the interrupt carries a turn number.

The turn filter is only safe because of where the client can fire from: `bargeIn()` is reachable ONLY
while the mic gate is CLOSED, and the gate closes on `audio_start` — so the scheduler's `currentTurn` is
always the turn the backend is speaking. The public `interrupt()` on the hook has no caller in the app.
These tests drive the real WebSocket, so they check behaviour rather than the shape of the source.
"""
from __future__ import annotations

import asyncio
import json
import time


from test_voice_ws import (  # reuse the fake EL clients + fake LLM harness
    FakeStt,
    client,
    recv_until,
    set_loop,
    texts,
    voice_env,
)


def _wait_for(ws, kind: str, limit: int = 200) -> dict:
    """Bounded: an event that never comes is a defect, and an endless receive() reports it by holding
    the whole run open rather than failing this line."""
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes") is None:
            ev = json.loads(msg["text"])
            if ev["type"] == kind:
                return ev
    raise AssertionError(f"no {kind!r} frame in {limit} messages")


def test_an_interrupt_naming_the_live_turn_still_stops_it(client, monkeypatch, voice_env):
    """The ordinary case, and the one the VAD produces."""
    state = {"calls": 0, "cancelled": False}

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        state["calls"] += 1
        if state["calls"] == 1:
            await queue.put("Let me think... ")
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
        await queue.put("After interrupt.")
        return "After interrupt."

    set_loop(monkeypatch, fake_loop)
    with client.websocket_connect("/api/voice/vws-turn-live") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "slow question"}))
        started = _wait_for(ws, "audio_start")
        ws.send_text(json.dumps({"type": "interrupt", "turn": started["turn"]}))
        ws.send_text(json.dumps({"type": "text", "text": "next"}))
        events, _ = recv_until(ws, "turn_end")

    assert state["cancelled"] is True
    assert any(e["type"] == "interrupted" for e in events)
    assert "After interrupt." in texts(events)


def test_an_interrupt_with_no_turn_still_works(client, monkeypatch, voice_env):
    """Back-compat: a client that predates the turn field must not lose barge-in."""
    state = {"calls": 0, "cancelled": False}

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        state["calls"] += 1
        if state["calls"] == 1:
            await queue.put("Thinking... ")
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
        await queue.put("Done.")
        return "Done."

    set_loop(monkeypatch, fake_loop)
    with client.websocket_connect("/api/voice/vws-turn-none") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "q"}))
        _wait_for(ws, "audio_start")
        ws.send_text(json.dumps({"type": "interrupt"}))   # no turn field at all
        ws.send_text(json.dumps({"type": "text", "text": "next"}))
        events, _ = recv_until(ws, "turn_end")

    assert state["cancelled"] is True
    assert any(e["type"] == "interrupted" for e in events)


def test_a_stale_interrupt_does_not_kill_the_turn_that_replaced_it(client, monkeypatch, voice_env):
    """The defect this fix exists for: an interrupt aimed at turn N arriving after N+1 has started used
    to kill N+1 — the brand-new turn — or leave it mute with its bytes discarded client-side."""
    state = {"calls": 0, "cancelled_turns": [], "completed_turns": []}

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        state["calls"] += 1
        n = state["calls"]
        await queue.put(f"Reply {n}. ")
        try:
            await asyncio.sleep(0.4 if n == 1 else 0.15)
        except asyncio.CancelledError:
            state["cancelled_turns"].append(n)
            raise
        state["completed_turns"].append(n)
        return f"Reply {n}."

    set_loop(monkeypatch, fake_loop)
    with client.websocket_connect("/api/voice/vws-turn-stale") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "first"}))
        first = _wait_for(ws, "audio_start")
        # A NEW turn supersedes the first (turn 1 is cancelled — that part is correct), then a straggler
        # interrupt aimed at the OLD turn arrives.
        ws.send_text(json.dumps({"type": "text", "text": "second"}))
        ws.send_text(json.dumps({"type": "interrupt", "turn": first["turn"]}))
        # Assert on SERVER state, not on the wire: if the stale interrupt wrongly kills turn 2, that turn
        # emits no turn_end and a receive would block forever — a hanging test proves nothing.
        time.sleep(0.6)

    assert 1 in state["cancelled_turns"], "the new turn must still supersede the old one"
    assert 2 not in state["cancelled_turns"], "the stale interrupt killed the turn that replaced it"
    assert 2 in state["completed_turns"], "turn 2 must run to completion"


def test_a_partial_during_playback_still_never_interrupts(client, monkeypatch, voice_env):
    """The standing invariant: a partial landing mid-playback is a straggler of pre-gate audio, never a
    barge-in. Re-checked here because the interrupt path moved."""
    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        await queue.put("Long answer coming... ")
        await asyncio.sleep(0.4)
        await queue.put("and here it ends.")
        return "Long answer coming... and here it ends."

    set_loop(monkeypatch, fake_loop)
    FakeStt.script = ["tell me everything"]
    with client.websocket_connect("/api/voice/vws-turn-partial") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "commit"}))
        _wait_for(ws, "audio_start")
        ws.send_bytes(b"__PARTIAL__")
        events, _ = recv_until(ws, "turn_end")

    assert any(e["type"] == "partial" for e in events)
    assert not any(e["type"] == "interrupted" for e in events)
    assert "and here it ends." in texts(events)


def test_an_ordinary_turn_still_ends_with_matched_audio_events(client, monkeypatch, voice_env):
    """audio_start/audio_end must stay paired — the mic gate opens on audio_end, and an unmatched
    start left the mic shut for the rest of the call."""
    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        await queue.put("All good here.")
        return "All good here."

    set_loop(monkeypatch, fake_loop)
    with client.websocket_connect("/api/voice/vws-turn-pairs") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "hi"}))
        events, _ = recv_until(ws, "turn_end")

    starts = [e for e in events if e["type"] == "audio_start"]
    ends = [e for e in events if e["type"] == "audio_end"]
    assert starts and len(starts) == len(ends), f"{len(starts)} audio_start vs {len(ends)} audio_end"
