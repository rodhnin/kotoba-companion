"""A due microphone hold waits out the sentence the user is already saying.

Reproduced before fixing: a blocking card put `audio_start hold=true` on the wire mid-word, and
the frames lost between the gate shutting and recovery are gone for good.

The fix, `_hold_can_wait`, defers a due hold while STT partials are hot and lands it in the
silence after the sentence commits — capped at `HOLD_DEFER_CAP_SECS` so a noisy room cannot
defer it forever. An idle room still gets its hold on the first poll, exactly as before, and a
straggler partial can never release an already-standing hold.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import kotoba.core.voice.session as vs
from kotoba.core import interaction
from kotoba.core.voice.stt import SttCommitted, SttPartial

POLL = 0.05
HOT = 0.4
CAP = 1.5
PARTIAL_EVERY = 0.1  # < HOT, the cadence EL sustains while someone is actually talking


class FakeWS:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.frames: list[tuple[float, dict]] = []

    async def accept(self) -> None:
        pass

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, t: str) -> None:
        self.frames.append((asyncio.get_running_loop().time(), json.loads(t)))

    async def send_bytes(self, b: bytes) -> None:
        pass

    def holds(self) -> list[float]:
        return [t for t, f in self.frames if f.get("type") == "audio_start" and f.get("hold")]

    def ends(self) -> list[float]:
        return [t for t, f in self.frames if f.get("type") == "audio_end"]


class FakeDB:
    async def ensure_session(self, sid):
        pass

    async def insert_turn(self, *a, **k):
        pass

    async def fetch_soul_config(self):
        return None


class FakeStt:
    """Emits whatever the test puts on its queue; None ends the stream."""

    queue: asyncio.Queue | None = None

    def __init__(self, **kw) -> None:
        FakeStt.queue = asyncio.Queue()

    async def connect(self) -> None:
        pass

    async def send_audio(self, pcm: bytes) -> None:
        pass

    async def events(self):
        while True:
            ev = await FakeStt.queue.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _wired(monkeypatch):
    monkeypatch.setattr(interaction, "_reachable", lambda sid, what: True)
    monkeypatch.setattr(interaction, "emit_emotion", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(interaction, "emit_task", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(vs, "SttClient", FakeStt)
    monkeypatch.setattr(vs, "MIC_HOLD_POLL_SECS", POLL)
    monkeypatch.setattr(vs, "PARTIAL_HOT_SECS", HOT)
    monkeypatch.setattr(vs, "HOLD_DEFER_CAP_SECS", CAP)

    async def fake_loop(items, sid, db, queue, patterns, **kw):
        return ""

    async def fake_load_context(request, db, session_id, **kw):
        return [{"role": "user", "content": request.messages[-1]["content"]}]

    async def no_memory(user_text, db):
        return None

    monkeypatch.setattr(vs, "agentic_loop", fake_loop)
    monkeypatch.setattr(vs, "load_context", fake_load_context)
    monkeypatch.setattr(vs, "extract_and_save_memory", no_memory)
    yield monkeypatch


async def start_session(sid: str) -> tuple[FakeWS, vs.VoiceSession, asyncio.Task]:
    ws = FakeWS()
    s = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
    runner = asyncio.create_task(s.run())
    await asyncio.sleep(0.02)
    ws.incoming.put_nowait({"bytes": b"\x00\x00"})  # the first mic frame opens STT
    for _ in range(100):
        if FakeStt.queue is not None and s._stt is not None:
            break
        await asyncio.sleep(0.01)
    return ws, s, runner


async def open_card(sid: str, label: str) -> asyncio.Task:
    task = asyncio.create_task(
        interaction.request_approval(sid, label, timeout=30.0, channel="text")
    )
    for _ in range(200):
        if interaction.has_pending(sid):
            return task
        await asyncio.sleep(0.005)
    raise AssertionError("the card never opened")


async def hang_up(ws: FakeWS, runner: asyncio.Task, *cards: asyncio.Task) -> None:
    ws.incoming.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(runner, 10)
    for c in cards:
        c.cancel()
    await asyncio.gather(*cards, return_exceptions=True)


def speak(text: str = "córreme un df en tu carpeta") -> None:
    FakeStt.queue.put_nowait(SttPartial(text=text))


def test_a_card_landing_mid_sentence_waits_for_the_commit():
    """The audited defect, inverted. Partials are hot when the card opens, and they stay hot for
    longer than the poll — the hold must not land inside the sentence. HOT is left large relative to
    the wait after the commit, so only the commit-clears-the-stamp path can explain the hold landing
    as fast as it must."""

    async def main():
        sid = "hold-waits"
        ws, s, runner = await start_session(sid)
        talker = asyncio.create_task(_keep_talking())
        await asyncio.sleep(PARTIAL_EVERY * 2)
        card = await open_card(sid, "du -sh .")
        opened = asyncio.get_running_loop().time()

        await asyncio.sleep(HOT + 3 * POLL)  # several polls, sentence still in flight
        assert ws.holds() == [], (
            f"the hold landed mid-sentence, {ws.holds()[0] - opened:.2f}s after the card"
        )
        talker.cancel()
        committed = asyncio.get_running_loop().time()
        FakeStt.queue.put_nowait(SttCommitted(text="córreme un df en tu carpeta"))
        for _ in range(200):
            if ws.holds():
                break
            await asyncio.sleep(0.005)
        assert ws.holds(), "the hold never landed after the sentence committed"
        assert ws.holds()[0] - committed < HOT, (
            "the hold waited out the full HOT window after the commit — the commit did not clear "
            "the stamp, it only aged out"
        )
        await hang_up(ws, runner, card)

    async def _keep_talking():
        while True:
            speak()
            await asyncio.sleep(PARTIAL_EVERY)

    asyncio.run(main())


def test_a_room_whose_noise_is_speech_gets_its_hold_at_the_cap():
    """The noisy-room objection, answered instead of dodged: a television is partials that never go
    stale, so without the cap the deferral would be the old unguarded mic back again. With it, the
    hold lands within HOLD_DEFER_CAP_SECS plus poll slack, however long the noise keeps talking."""

    async def main():
        sid = "hold-tv"
        ws, s, runner = await start_session(sid)
        tv = asyncio.create_task(_tv())
        await asyncio.sleep(PARTIAL_EVERY * 2)
        card = await open_card(sid, "du -sh .")
        opened = asyncio.get_running_loop().time()

        for _ in range(1000):
            if ws.holds():
                break
            await asyncio.sleep(0.01)
        assert ws.holds(), "the hold never landed — the noise deferred it forever"
        landed = ws.holds()[0] - opened
        assert landed >= CAP - 2 * POLL, f"the cap was not what landed it ({landed:.2f}s)"
        assert landed < CAP + 0.5, f"the hold overshot the cap ({landed:.2f}s)"
        tv.cancel()
        await hang_up(ws, runner, card)

    async def _tv():
        while True:
            speak("canal de televisión que no calla")
            await asyncio.sleep(PARTIAL_EVERY)

    asyncio.run(main())


def test_an_idle_room_still_gets_its_hold_on_the_first_poll():
    """The protection the hold exists for, untouched: no partials, no deferral — the mic shuts as
    fast as it ever did, so an idle room cannot spend a card wait committing its own noise."""

    async def main():
        sid = "hold-idle"
        ws, s, runner = await start_session(sid)
        card = await open_card(sid, "du -sh .")
        opened = asyncio.get_running_loop().time()

        for _ in range(200):
            if ws.holds():
                break
            await asyncio.sleep(0.005)
        assert ws.holds(), "no hold for an idle room"
        assert ws.holds()[0] - opened < HOT, (
            f"the hold waited {ws.holds()[0] - opened:.2f}s with nobody talking"
        )
        await hang_up(ws, runner, card)

    asyncio.run(main())


def test_a_straggler_partial_never_releases_a_standing_hold():
    """The deferral is the hold's front edge only. Once the bracket is shut a partial can only be a
    straggler of pre-hold audio — deferring must be doing nothing, never releasing, or the straggler
    would hand an idle room the open mic the hold just took away."""

    async def main():
        sid = "hold-straggler"
        ws, s, runner = await start_session(sid)
        card = await open_card(sid, "du -sh .")
        for _ in range(200):
            if s._mic_hold:
                break
            await asyncio.sleep(0.005)
        assert s._mic_hold, "the hold never engaged"
        ends_before = len(ws.ends())

        speak("straggler")  # pre-hold audio coming back late
        await asyncio.sleep(HOT + 4 * POLL)
        assert s._mic_hold, "a straggler partial broke a standing hold"
        assert len(ws.ends()) == ends_before, "the watcher released the mic behind the hold"
        await hang_up(ws, runner, card)

    asyncio.run(main())


def test_a_card_arriving_during_the_yield_waits_for_the_sentence_too():
    """The named case end to end: he spoke over card A (the yield), he is mid-sentence, and a
    background job opens card B. B must still shut the mic — the yield's snapshot does not cover it —
    but not through his word: only once his sentence goes stale."""

    async def main():
        sid = "hold-yield-defer"
        ws, s, runner = await start_session(sid)
        a = await open_card(sid, "card A")
        for _ in range(200):
            if s._mic_hold:
                break
            await asyncio.sleep(0.005)
        assert s._mic_hold, "no hold for card A"
        ws.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": 0})})
        for _ in range(200):
            if s._mic_yielded and not s._mic_hold:
                break
            await asyncio.sleep(0.005)
        assert s._mic_yielded, "the yield never happened"

        talker = asyncio.create_task(_keep_talking())
        await asyncio.sleep(PARTIAL_EVERY * 2)
        holds_before = len(ws.holds())
        b = await open_card(sid, "card B")
        await asyncio.sleep(HOT + 3 * POLL)
        assert len(ws.holds()) == holds_before, "card B cut the mic mid-sentence"
        assert not s._mic_yielded, "card B should have ended the yield even while deferring"

        talker.cancel()
        await asyncio.sleep(HOT + 4 * POLL)
        assert len(ws.holds()) > holds_before, "card B never got its hold after the sentence"
        await hang_up(ws, runner, a, b)

    async def _keep_talking():
        while True:
            speak()
            await asyncio.sleep(PARTIAL_EVERY)

    asyncio.run(main())
