"""The unhappy paths of the microphone yield and the soul-config reads on the voice hot paths.

Measured before the fix: a barge-in behind a held bracket with no live turn sent NOTHING that
reopens the gate, dropping already-spoken words for up to a full poll interval; and a card
opened while the yield stood inherited it, extending the suspension to cards nobody spoke over.

`fetch_soul_config()` grew calls on two hot paths where an exception was a catastrophe: on the
first-mic-frame path it unwound the whole call, and on the segment-reopen path it left the
turn's `audio_start` unanswered — a shut mic the client can only rescue by sustained speech.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

import kotoba.core.voice.session as vs
from kotoba.core import interaction


class FakeWS:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.frames: list[dict] = []
        self.stamps: list[tuple[float, str]] = []

    async def accept(self) -> None:
        pass

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, t: str) -> None:
        f = json.loads(t)
        self.frames.append(f)
        self.stamps.append((asyncio.get_running_loop().time(), f["type"]))

    async def send_bytes(self, b: bytes) -> None:
        pass

    def types(self) -> list[str]:
        return [f["type"] for f in self.frames]

    def brackets(self) -> list[dict]:
        return [f for f in self.frames if f["type"] in ("audio_start", "audio_end", "interrupted")]


class FakeDB:
    async def ensure_session(self, sid):
        pass

    async def insert_turn(self, *a, **k):
        pass

    async def fetch_soul_config(self):
        return None


class BlippyDB(FakeDB):
    """Healthy except that the soul-config read fails from call `fail_from` on — a locked sqlite
    file mid-call, the shape aiosqlite actually raises."""

    def __init__(self, fail_from: int = 1) -> None:
        self.calls = 0
        self.fail_from = fail_from

    async def fetch_soul_config(self):
        self.calls += 1
        if self.calls >= self.fail_from:
            raise sqlite3.OperationalError("database is locked")
        return None


class FakeStt:
    instances: list["FakeStt"] = []

    def __init__(self, **kw) -> None:
        self.audio: list[bytes] = []
        FakeStt.instances.append(self)

    async def connect(self) -> None:
        pass

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.append(pcm)

    async def events(self):
        await asyncio.Event().wait()
        yield

    async def close(self) -> None:
        pass


class SlowTts:
    """Streams until end() — a turn that is genuinely mid-speech when something cancels it."""

    def __init__(self, **kw) -> None:
        self.ended = False

    async def connect(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        pass

    async def end(self) -> None:
        self.ended = True

    async def audio_chunks(self):
        while not self.ended:
            await asyncio.sleep(0.005)
        yield b"\x01"

    async def close(self) -> None:
        self.ended = True


class InstantDeathTts:
    """connect() succeeds; the reader ends at once (no chunks) — the REST-engine death shape that
    sends `_feed_segment` down its reopen-a-segment recovery, where the second soul fetch lives."""

    def __init__(self, **kw) -> None:
        pass

    async def connect(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        pass

    async def end(self) -> None:
        pass

    async def audio_chunks(self):
        return
        yield

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _channel(monkeypatch):
    monkeypatch.setattr(interaction, "_reachable", lambda sid, what: True)
    monkeypatch.setattr(interaction, "emit_emotion", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(interaction, "emit_task", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(vs, "TtsClient", InstantDeathTts)
    monkeypatch.setattr(vs, "SttClient", FakeStt)
    FakeStt.instances = []

    async def fake_load_context(request, db, session_id, **kw):
        return [{"role": "user", "content": request.messages[-1]["content"]}]

    async def no_memory(user_text, db):
        return None

    monkeypatch.setattr(vs, "load_context", fake_load_context)
    monkeypatch.setattr(vs, "extract_and_save_memory", no_memory)
    yield monkeypatch


async def open_card(sid: str, label: str) -> asyncio.Task:
    task = asyncio.create_task(
        interaction.request_approval(sid, label, timeout=30.0, channel="text")
    )
    for _ in range(200):
        if label in interaction.pending_labels(sid):
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("the card never opened")


async def wait_for(pred, limit: float = 5.0) -> bool:
    end = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.005)
    return False


async def settle(*tasks) -> None:
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# ---- the yield: prompt, and scoped to the cards he actually spoke over -----------------------------


def test_the_yield_hands_the_mic_back_at_once_not_at_the_next_poll(_channel):
    """Behind a marked bracket the client's gate opens ONLY when the server closes it, so a release
    that rides the poll is up to a whole poll of the user's words dropped. The poll here is huge on
    purpose: only the wake can pass this test."""
    _channel.setattr(vs, "MIC_HOLD_POLL_SECS", 30.0)

    async def main():
        sid = "yield-at-once"
        card = await open_card(sid, "df -h")
        ws = FakeWS()
        s = vs.VoiceSession(ws, sid, db=None, soul_patterns={})
        runner = asyncio.create_task(s.run())
        assert await wait_for(lambda: s._mic_hold), "the hold never engaged"
        t0 = asyncio.get_running_loop().time()
        ws.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": 0})})
        assert await wait_for(
            lambda: any(t > t0 and typ in ("audio_end", "interrupted") for t, typ in ws.stamps),
            limit=2.0,
        ), f"nothing reopened the gate inside 2s (poll is 30s): {ws.types()!r}"
        assert s._mic_yielded and not s._mic_hold
        assert interaction.has_pending(sid), "the yield must not touch the card"
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)
        await settle(card)

    asyncio.run(main())


def test_a_card_opened_during_the_yield_holds_the_mic(_channel):
    """The ambient-noise protection is suspended for the cards he interrupted, never retired: a card
    that lands while the yield stands — a background job's approval — was never spoken over and gets
    its hold, even though pending-ness never dipped to zero in between."""
    _channel.setattr(vs, "MIC_HOLD_POLL_SECS", 0.05)

    async def main():
        sid = "yield-scoped"
        a = await open_card(sid, "card A")
        ws = FakeWS()
        s = vs.VoiceSession(ws, sid, db=None, soul_patterns={})
        runner = asyncio.create_task(s.run())
        assert await wait_for(lambda: s._mic_hold), "no hold for card A"
        ws.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": 0})})
        assert await wait_for(lambda: s._mic_yielded and not s._mic_hold), "the yield never happened"

        b = await open_card(sid, "card B")
        assert await wait_for(lambda: s._mic_hold, 2.0), (
            "card B never held the mic — the yield was inherited by a card nobody spoke over"
        )
        assert not s._mic_yielded, "the yield should have ended when an uncovered card appeared"
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)
        await settle(a, b)

    asyncio.run(main())


def test_the_yield_survives_its_own_cards_resolving_out_of_order(_channel):
    """The other direction of the snapshot: while only cards he DID speak over remain, the mic stays
    his — a subset of the snapshot must not read as \"new card\"."""
    _channel.setattr(vs, "MIC_HOLD_POLL_SECS", 0.05)

    async def main():
        sid = "yield-subset"
        a = await open_card(sid, "card A")
        b = await open_card(sid, "card B")
        ws = FakeWS()
        s = vs.VoiceSession(ws, sid, db=None, soul_patterns={})
        runner = asyncio.create_task(s.run())
        assert await wait_for(lambda: s._mic_hold), "no hold for the two cards"
        ws.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": 0})})
        assert await wait_for(lambda: s._mic_yielded and not s._mic_hold), "the yield never happened"

        assert interaction.resolve(sid, {"approved": False, "always": False})  # newest first: card B
        await asyncio.sleep(0.3)
        assert s._mic_yielded and not s._mic_hold, "losing ONE yielded card re-shut the mic on him"

        assert interaction.resolve(sid, {"approved": False, "always": False})  # card A
        assert await wait_for(lambda: not s._mic_yielded, 2.0), "the yield outlived every card in it"
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)
        await settle(a, b)

    asyncio.run(main())


def test_a_fresh_socket_holds_the_mic_for_a_card_the_yield_had_freed(_channel):
    """Hang up mid-yield and call again: the yield was the OLD socket's conversation, and the card is
    still waiting — the new socket holds at accept, before any turn exists (turn 0 is a valid hold:
    the client routes a marked bracket straight to the gate, never through the turn scheduler)."""
    _channel.setattr(vs, "MIC_HOLD_POLL_SECS", 0.05)

    async def main():
        sid = "yield-reconnect"
        card = await open_card(sid, "card A")
        ws1 = FakeWS()
        s1 = vs.VoiceSession(ws1, sid, db=None, soul_patterns={})
        r1 = asyncio.create_task(s1.run())
        assert await wait_for(lambda: s1._mic_hold), "no hold on the first socket"
        ws1.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": 0})})
        assert await wait_for(lambda: s1._mic_yielded), "the yield never happened"
        ws1.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(r1, 10)
        assert not s1._mic_hold, "the first socket left its hold behind"

        ws2 = FakeWS()
        s2 = vs.VoiceSession(ws2, sid, db=None, soul_patterns={})
        r2 = asyncio.create_task(s2.run())
        assert await wait_for(lambda: s2._mic_hold, 2.0), "the new socket never held for the old card"
        holds = [f for f in ws2.brackets() if f["type"] == "audio_start" and f.get("hold")]
        assert holds and holds[0]["turn"] == 0
        ws2.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(r2, 10)
        await settle(card)

    asyncio.run(main())


# ---- the soul-config reads on the two hot paths ----------------------------------------------------


def test_one_db_blip_on_the_first_mic_frame_does_not_drop_the_call(_channel):
    async def main():
        ws = FakeWS()
        s = vs.VoiceSession(ws, "stt-blip", db=BlippyDB(fail_from=1), soul_patterns={})
        runner = asyncio.create_task(s.run())
        await wait_for(lambda: "ready" in ws.types())
        ws.incoming.put_nowait({"bytes": b"\x00\x00\x00\x00"})
        assert await wait_for(lambda: bool(FakeStt.instances)), (
            "the STT session never opened — the locked read must degrade to defaults"
        )
        assert not runner.done(), f"one locked read dropped the whole call: {runner.exception()!r}"
        ws.incoming.put_nowait({"bytes": b"\x01\x01\x01\x01"})
        assert await wait_for(lambda: b"\x01\x01\x01\x01" in FakeStt.instances[0].audio), (
            "mic audio stopped flowing after the blip"
        )
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)

    asyncio.run(main())


def _speaking_loop(*lines: str):
    async def fake_loop(items, sid, db, queue, patterns, **kw):
        for line in lines:
            await queue.put(line)
            await asyncio.sleep(0.15)
        return "".join(lines)

    return fake_loop


def test_a_db_blip_on_the_segment_reopen_keeps_the_voice_and_closes_the_bracket(_channel):
    """The measured worst case: first segment opens fine, dies, and the reopen's soul fetch hits the
    locked file. That read must cost the PREFERENCE (default voice), not the bracket."""

    async def main():
        sid = "reopen-blip"
        ws = FakeWS()
        db = BlippyDB(fail_from=2)
        s = vs.VoiceSession(ws, sid, db=db, soul_patterns={})
        _channel.setattr(vs, "agentic_loop", _speaking_loop("Primera frase entera. ", "Segunda. "))
        runner = asyncio.create_task(s.run())
        await wait_for(lambda: "ready" in ws.types())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "di algo"})})
        assert await wait_for(lambda: "turn_end" in ws.types()), "the turn never ended"
        assert db.calls >= 2, "the reopen path never re-read the soul config"
        assert not s._audio_open, "an audio_start was left unanswered"
        assert ws.brackets() and ws.brackets()[-1]["type"] == "audio_end", (
            f"the bracket was left open on the client: {ws.brackets()!r}"
        )
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)

    asyncio.run(main())


def test_her_reply_does_not_end_the_yield(_channel):
    """A trigger turn (a reminder, finished work) arriving while the mic is yielded speaks through its
    own segment and hands the mic straight back: the segment's audio_end wakes the owner, which must
    NOT read the closed bracket as a reason to re-hold — the yielded card is still the yielded card."""
    _channel.setattr(vs, "MIC_HOLD_POLL_SECS", 0.05)
    _channel.setattr(vs, "agentic_loop", _speaking_loop("Te recuerdo lo del horno. "))

    async def main():
        sid = "yield-trigger"
        card = await open_card(sid, "card A")
        ws = FakeWS()
        s = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
        runner = asyncio.create_task(s.run())
        assert await wait_for(lambda: s._mic_hold), "no hold for card A"
        ws.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": 0})})
        assert await wait_for(lambda: s._mic_yielded and not s._mic_hold), "the yield never happened"

        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "__reminder__"})})
        assert await wait_for(lambda: "turn_end" in ws.types()), "the trigger turn never ended"
        await asyncio.sleep(0.3)
        assert s._mic_yielded and not s._mic_hold, (
            f"her own reply put him back behind the bracket: {ws.brackets()!r}"
        )
        holds = [f for f in ws.brackets() if f["type"] == "audio_start" and f.get("hold")]
        assert len(holds) == 1, f"a second hold appeared behind the yield: {ws.brackets()!r}"
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)
        await settle(card)

    asyncio.run(main())


def test_an_out_of_socket_supersede_still_closes_the_bracket(_channel):
    """POST /leave calls turns.supersede with the WebSocket still up — no `interrupted` frame is born,
    so the pump's cancel path owes the close itself. Before the fix this stranded the turn's
    audio_start: mic gated, nothing pending, the exact failure this file exists to avoid."""
    from kotoba.core import turns

    _channel.setattr(vs, "TtsClient", SlowTts)
    _channel.setattr(vs, "agentic_loop", _speaking_loop("Una frase que ella esta diciendo. ", ""))

    async def main():
        sid = "leave-mid-turn"
        ws = FakeWS()
        s = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
        runner = asyncio.create_task(s.run())
        await wait_for(lambda: "ready" in ws.types())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "di algo"})})
        assert await wait_for(lambda: "audio_start" in ws.types()), "she never started speaking"

        await turns.supersede(sid)
        assert await wait_for(lambda: not s._audio_open, 2.0), "the bracket was left open"
        assert ws.brackets()[-1]["type"] == "audio_end", (
            f"nothing reopened the client's gate: {ws.brackets()!r}"
        )
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)

    asyncio.run(main())


def test_a_barge_in_does_not_grow_a_spurious_audio_end(_channel):
    """The level guard the previous test leans on, asserted from the other side: an in-socket barge-in
    already reopens the gate with `interrupted`, so the cancel path must send nothing extra — the
    client would read a stray audio_end as closing a bracket that no longer exists."""
    _channel.setattr(vs, "TtsClient", SlowTts)
    _channel.setattr(vs, "agentic_loop", _speaking_loop("Una frase que ella esta diciendo. ", ""))

    async def main():
        sid = "barge-no-stray"
        ws = FakeWS()
        s = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
        runner = asyncio.create_task(s.run())
        await wait_for(lambda: "ready" in ws.types())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "di algo"})})
        assert await wait_for(lambda: "audio_start" in ws.types()), "she never started speaking"

        ws.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": 1})})
        assert await wait_for(lambda: "interrupted" in ws.types()), "the turn was not cut"
        await asyncio.sleep(0.3)
        after = [f["type"] for f in ws.brackets()[ws.brackets().index(
            next(f for f in ws.brackets() if f["type"] == "interrupted")):]]
        assert "audio_end" not in after, f"a stray audio_end followed the interrupt: {after!r}"
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)

    asyncio.run(main())


def test_a_crash_in_the_reply_pump_still_closes_the_bracket(_channel):
    """No `interrupted` follows a crash, so the pump owes the close itself — for ANY exception, not
    only the Voice family and its own cancellation."""

    async def main():
        sid = "pump-crash"
        ws = FakeWS()
        s = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
        _channel.setattr(vs, "agentic_loop", _speaking_loop("Una frase. ", "Otra. "))
        real = s._feed_segment
        seen = {"n": 0}

        async def feeding(seg, text, turn_no, tts_dead):
            seen["n"] += 1
            if seen["n"] >= 2:
                raise RuntimeError("a filter bug, not a Voice error")
            return await real(seg, text, turn_no, tts_dead)

        s._feed_segment = feeding
        runner = asyncio.create_task(s.run())
        await wait_for(lambda: "ready" in ws.types())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "di algo"})})
        assert await wait_for(lambda: "turn_end" in ws.types()), "the turn never ended"
        assert seen["n"] >= 2, "the crash was never reached"
        assert not s._audio_open, "the crash left an audio_start unanswered"
        assert ws.brackets() and ws.brackets()[-1]["type"] == "audio_end", (
            f"the bracket was left open on the client: {ws.brackets()!r}"
        )
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)

    asyncio.run(main())
