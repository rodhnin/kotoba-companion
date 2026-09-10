"""Mid-turn TTS failure recovery, the audio bracket, and the crash apology.

The engine contract: a lost request is retried once, then skipped; only a segment-fatal
failure surfaces. A finished reader is treated like a raised send — abort the segment, close
its client, and continue the turn on one fresh segment, instead of dying silently mid-turn.

Every no-more-audio exit now funnels through `_end_audio`, so a failed reopen can no longer
leave `audio_start` unanswered and the mic shut. The crash apology is now language-matched.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import kotoba.core.voice.session as vs
from kotoba.core import stream as sse
from kotoba.core.voice import config
from kotoba.core.voice.config import VoiceError, VoiceStreamClosed
from kotoba.core.voice.tts_rest import ExpressiveTtsClient


class FakeWS:
    def __init__(self):
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.frames: list = []

    async def accept(self):
        pass

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, t):
        self.frames.append(("json", json.loads(t)))

    async def send_bytes(self, b):
        self.frames.append(("bytes", bytes(b)))

    def jsons(self):
        return [f[1] for f in self.frames if f[0] == "json"]

    def blobs(self):
        return [f[1] for f in self.frames if f[0] == "bytes"]

    def types(self):
        return [f[1]["type"] for f in self.frames if f[0] == "json"]

    def errors(self):
        return [(e["code"], e["fatal"]) for e in self.jsons() if e["type"] == "error"]

    def captions(self):
        return "".join(e["text"] for e in self.jsons() if e["type"] == "assistant_text")


class FakeDB:
    async def ensure_session(self, sid):
        pass

    async def insert_turn(self, *a, **k):
        pass

    async def fetch_soul_config(self):
        return None


class MicGatePort:
    """Faithful port of lib/voice-gate.ts MicGate — the trace is judged with the client's own rules."""

    GATE_TAIL_MS = 350

    def __init__(self):
        self.streaming = False
        self.playing = False
        self.tail_until = 0.0

    def is_open(self, now):
        return not self.streaming and not self.playing and now >= self.tail_until

    def drive(self, events):
        t = 0.0
        for ev in events:
            t += 100.0
            if ev["type"] == "audio_start":
                self.streaming = True
            elif ev["type"] == "audio_end":
                self.streaming = False
                if not self.playing:
                    self.tail_until = max(self.tail_until, t + self.GATE_TAIL_MS)
            elif ev["type"] == "interrupted":
                self.streaming = False
                self.playing = False
                self.tail_until = 0.0
        return self.is_open(t + 10_000)


@pytest.fixture
def session_env(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-for-recovery")
    config.set_api_key(None)

    async def fake_load_context(request, db, session_id, **kw):
        return [{"role": "user", "content": request.messages[-1]["content"]}]

    async def no_memory(user_text, db):
        return None

    monkeypatch.setattr(vs, "load_context", fake_load_context)
    monkeypatch.setattr(vs, "extract_and_save_memory", no_memory)
    yield monkeypatch


async def run_turn(ws, session_id, user_text="háblame de algo, por favor"):
    session = vs.VoiceSession(ws, session_id, db=FakeDB(), soul_patterns={})
    runner = asyncio.create_task(session.run())
    ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": user_text})})
    for _ in range(400):
        if "turn_end" in ws.types():
            break
        await asyncio.sleep(0.05)
    ws.incoming.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(runner, 10)


class ScriptedHttp:
    """One FakeResponse-alike per HTTP call, scripted by call NUMBER (1-based): calls whose number is
    in `fail_calls` return 500, the rest stream a single b"AUDIO<n>" chunk."""

    def __init__(self, fail_calls=frozenset()):
        self.fail_calls = set(fail_calls)
        self.calls = 0
        self.texts: list[str] = []

    def stream(self, method, url, json=None, headers=None):
        self.calls += 1
        n = self.calls
        self.texts.append(json["text"])
        failing = n in self.fail_calls

        class Resp:
            status_code = 500 if failing else 200

            async def aread(self_inner):
                return b"simulated ElevenLabs 500"

            async def aiter_bytes(self_inner):
                await asyncio.sleep(0.01)
                yield f"AUDIO{n}".encode()

        class CM:
            async def __aenter__(self_inner):
                return Resp()

            async def __aexit__(self_inner, *a):
                return False

        return CM()

    async def aclose(self):
        pass


def expressive_factory(http):
    def make(voice_id=None, **kw):
        return ExpressiveTtsClient(voice_id, output_format=kw.get("output_format"), http_client=http)

    return make


def three_sentence_loop():
    async def loop(items, sid, db, queue, patterns, **kw):
        await queue.put("Primera frase de la respuesta que suena bien. ")
        await asyncio.sleep(0.2)
        await queue.put("Segunda frase cuya peticion fallara en el servidor. ")
        await asyncio.sleep(0.2)
        await queue.put("Tercera frase que debe sonar tambien. ")
        await asyncio.sleep(0.2)
        return "texto completo"

    return loop


def test_a_transient_500_mid_turn_no_longer_mutes_the_rest_of_the_turn(session_env):
    session_env.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    http = ScriptedHttp(fail_calls={2})
    session_env.setattr(vs, "TtsClient", expressive_factory(http))
    session_env.setattr(vs, "agentic_loop", three_sentence_loop())
    ws = FakeWS()

    asyncio.run(run_turn(ws, "vrec-transient"))

    audio = b"".join(ws.blobs())
    assert b"AUDIO1" in audio
    assert b"AUDIO3" in audio and b"AUDIO4" in audio, \
        "the failed sentence is retried (call 3) and the next one still plays (call 4)"
    assert http.texts[1] == http.texts[2], "call 3 is the RETRY of the sentence call 2 lost"
    assert ws.errors() == [], "a recovered transient is not an error the user needs"
    assert all(s in ws.captions() for s in ("Primera", "Segunda", "Tercera"))
    assert "audio_end" in ws.types() and "turn_end" in ws.types()
    assert MicGatePort().drive(ws.jsons()), "the mic gate must be open after the turn"


def test_a_persistently_failing_tts_gives_up_cleanly_and_says_so(session_env):
    session_env.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    http = ScriptedHttp(fail_calls=set(range(1, 100)))
    session_env.setattr(vs, "TtsClient", expressive_factory(http))

    async def loop(items, sid, db, queue, patterns, **kw):
        for word in ("Primera", "Segunda", "Tercera", "Cuarta", "Quinta", "Sexta"):
            await queue.put(f"{word} frase de una respuesta larga. ")
            await asyncio.sleep(0.15)
        return "texto completo"

    session_env.setattr(vs, "agentic_loop", loop)
    ws = FakeWS()

    asyncio.run(run_turn(ws, "vrec-permanent"))

    assert "turn_end" in ws.types(), "a dead TTS must not hang the turn"
    assert any(code == "tts_stream" and not fatal for code, fatal in ws.errors())
    assert MicGatePort().drive(ws.jsons()), "giving up must still close the audio bracket"
    assert http.calls <= 8, f"{http.calls} requests — synthesis must stop once nobody is listening"
    assert "Sexta" not in " ".join(http.texts), \
        "text arriving after the give-up must never be synthesized (that was the quota burn)"
    assert all(s in ws.captions() for s in ("Primera", "Sexta")), "captions survive a dead voice"


class DyingReaderTts:
    """WS-engine shape: send_text never raises; the FIRST instance's audio stream dies mid-segment
    (what a stall/drop looks like from audio_chunks); later instances behave."""

    instances: list["DyingReaderTts"] = []

    def __init__(self, voice_id=None, **kw):
        DyingReaderTts.instances.append(self)
        self.idx = len(DyingReaderTts.instances)
        self.text: list[str] = []
        self.ended = False

    async def connect(self):
        pass

    async def send_text(self, text):
        if text:
            self.text.append(text)

    async def end(self):
        self.ended = True

    async def audio_chunks(self):
        if self.idx == 1:
            yield b"\x01\x01"
            raise VoiceError("TTS stream stalled (no frame within 30s)")
        while not self.ended:
            await asyncio.sleep(0.005)
        yield b"\x02\x02"

    async def close(self):
        pass


def test_reader_death_recovers_on_the_ws_engine_too(session_env):
    DyingReaderTts.instances = []
    session_env.setattr(vs, "TtsClient", DyingReaderTts)

    async def loop(items, sid, db, queue, patterns, **kw):
        await queue.put("Antes de la caida del lector. ")
        await asyncio.sleep(0.2)
        await queue.put("Despues de la caida del lector. ")
        return "texto completo"

    session_env.setattr(vs, "agentic_loop", loop)
    ws = FakeWS()

    asyncio.run(run_turn(ws, "vrec-wsreader"))

    assert len(DyingReaderTts.instances) == 2, \
        "text after the reader died must go to a FRESH segment, not into the dead one"
    assert any("Despues" in t for t in DyingReaderTts.instances[1].text)
    assert b"\x02\x02" in b"".join(ws.blobs()), "the recovered segment's audio reaches the browser"
    assert ("tts_stream", False) in ws.errors()
    assert "audio_end" in ws.types() and "turn_end" in ws.types()
    assert MicGatePort().drive(ws.jsons())


class ReopenFailsTts:
    """send_text drops the stream and the SECOND connect fails — an EL outage spanning both, the
    exact path that used to leave audio_start unanswered and the mic gated for the rest of the call."""

    built = 0

    def __init__(self, voice_id=None, **kw):
        ReopenFailsTts.built += 1
        self.idx = ReopenFailsTts.built

    async def connect(self):
        if self.idx >= 2:
            raise VoiceError("TTS connect failed: EL unreachable (simulated outage)")

    async def send_text(self, text):
        raise VoiceStreamClosed("TTS socket dropped while sending (simulated)")

    async def end(self):
        pass

    async def audio_chunks(self):
        while True:
            await asyncio.sleep(0.05)
        yield b""

    async def close(self):
        pass


def test_a_failed_reopen_still_closes_the_audio_bracket(session_env):
    ReopenFailsTts.built = 0
    session_env.setattr(vs, "TtsClient", ReopenFailsTts)

    async def loop(items, sid, db, queue, patterns, **kw):
        await queue.put("Hola, esta frase no llegara al altavoz. ")
        return "Hola, esta frase no llegara al altavoz."

    session_env.setattr(vs, "agentic_loop", loop)
    ws = FakeWS()

    asyncio.run(run_turn(ws, "vrec-reopen"))

    types = ws.types()
    assert "audio_start" in types and "turn_end" in types
    assert types.index("audio_end") > types.index("audio_start"), \
        "an unanswered audio_start left the client MicGate shut for the rest of the call"
    assert MicGatePort().drive(ws.jsons())


class QuietTts:
    def __init__(self, voice_id=None, **kw):
        self.ended = False

    async def connect(self):
        pass

    async def send_text(self, text):
        pass

    async def end(self):
        self.ended = True

    async def audio_chunks(self):
        while not self.ended:
            await asyncio.sleep(0.005)
        yield b"\x00\x00"

    async def close(self):
        pass


@pytest.mark.parametrize(
    "user_text",
    ["háblame de tu día, por favor", "tell me about your day please"],
    ids=["asked-in-spanish", "asked-in-english"],
)
def test_a_crashed_turn_still_says_something(session_env, user_text):
    session_env.setattr(vs, "TtsClient", QuietTts)

    async def loop(items, sid, db, queue, patterns, **kw):
        raise RuntimeError("simulated provider outage")

    session_env.setattr(vs, "agentic_loop", loop)
    ws = FakeWS()

    asyncio.run(run_turn(ws, f"vrec-apology-{abs(hash(user_text)) % 9999}", user_text=user_text))

    # One canned line, whatever they asked in: a copy per language covers only the languages somebody
    # remembered to write, and the turn that needs it most is the one where nothing else worked.
    spoken = ws.captions()
    assert "tripped up" in spoken, spoken
    assert "simulated provider outage" not in spoken, "the failure must not reach the stream"


def test_the_v1_twin_apologises_through_the_same_helper(monkeypatch):
    """/v1 (the ElevenLabs tunnel route) has its own generator, and the thing that must never differ
    is that the traceback stays out of what she says."""
    from fastapi.testclient import TestClient

    import kotoba.server as main

    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")

    async def dying_loop(input_items, session_id, db, queue, patterns, **kw):
        raise RuntimeError("secret traceback detail")

    monkeypatch.setattr(main, "agentic_loop", dying_loop)

    async def no_memory(user_text, db):
        return None

    monkeypatch.setattr(main, "extract_and_save_memory", no_memory)

    with TestClient(main.app) as client:
        r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer k"}, json={
            "messages": [{"role": "user", "content": "cuéntame algo de tu día"}],
            "session_id": "vrec-v1-es", "stream": True,
        })
    assert r.status_code == 200
    spoken = ""
    for line in r.text.splitlines():
        if line.startswith("data:") and "[DONE]" not in line:
            spoken += json.loads(line[5:].strip())["choices"][0]["delta"].get("content") or ""
    assert "tripped up" in spoken, spoken
    assert "secret traceback detail" not in r.text


class SlowCloseTts:
    """A magnifier for the zombie window found by live voice QA: instance 1's
    reader dies after one chunk, so the NEXT feed starts recovery (_abort_segment), and its close()
    is slow enough for a barge-in cancel to land inside that teardown — where a broad `except
    BaseException: pass` used to swallow the turn's own CancelledError. Every send_text is recorded,
    so zombie speech (text synthesized after the interrupt) is directly visible."""

    instances: list["SlowCloseTts"] = []
    closing: asyncio.Event | None = None
    reader_died: asyncio.Event | None = None

    def __init__(self, voice_id=None, **kw):
        SlowCloseTts.instances.append(self)
        self.idx = len(SlowCloseTts.instances)
        self.texts: list[str] = []
        self.ended = False
        self.closed = False

    async def connect(self):
        pass

    async def send_text(self, text):
        if text:
            self.texts.append(text)

    async def end(self):
        self.ended = True

    async def audio_chunks(self):
        if self.idx == 1:
            yield b"\x01"
            SlowCloseTts.reader_died.set()
            raise VoiceError("TTS stream stalled (simulated)")
        while not self.ended:
            await asyncio.sleep(0.005)
        yield b"\x02"

    async def close(self):
        if self.idx == 1 and not self.closed:
            self.closed = True
            SlowCloseTts.closing.set()
            await asyncio.sleep(0.5)
        self.closed = True


def test_a_barge_in_landing_inside_recovery_teardown_kills_the_turn(session_env):
    SlowCloseTts.instances = []
    session_env.setattr(vs, "TtsClient", SlowCloseTts)

    async def loop(items, sid, db, queue, patterns, **kw):
        if items[-1]["content"].startswith("Otra"):
            await queue.put("Respuesta a la segunda pregunta. ")
            return "Respuesta a la segunda pregunta."
        await queue.put("Primera frase que si suena. ")
        await SlowCloseTts.reader_died.wait()
        await asyncio.sleep(0.05)
        await queue.put("Segunda frase que hablaria un zombi. ")
        await asyncio.sleep(0.05)
        await queue.put("Tercera frase de zombi. ")
        return "texto completo"

    session_env.setattr(vs, "agentic_loop", loop)

    async def scenario():
        SlowCloseTts.closing = asyncio.Event()
        SlowCloseTts.reader_died = asyncio.Event()
        ws = FakeWS()
        session = vs.VoiceSession(ws, "vrec-zombie", db=FakeDB(), soul_patterns={})
        runner = asyncio.create_task(session.run())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "háblame un rato largo"})})
        await asyncio.wait_for(SlowCloseTts.closing.wait(), 5)
        ws.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": 1})})
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "Otra pregunta"})})
        for _ in range(600):
            if "Respuesta a la segunda" in ws.captions():
                break
            await asyncio.sleep(0.01)
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)
        return ws

    ws = asyncio.run(scenario())

    synthesized = " ".join(t for inst in SlowCloseTts.instances for t in inst.texts)
    assert "zombi" not in synthesized, \
        "text queued after the barge-in must never reach TTS — that was the paid zombie synthesis"
    assert "Tercera" not in ws.captions(), \
        "a cancelled turn must stop consuming its reply, not keep captioning it"
    jsons = ws.jsons()
    cut = next(i for i, j in enumerate(jsons) if j["type"] == "interrupted")
    assert not any(j["type"] == "audio_start" and j.get("turn") == 1 for j in jsons[cut:]), \
        "an interrupted turn must not reopen a segment"
    assert not any(j["type"] == "turn_end" and j.get("turn") == 1 for j in jsons), \
        "the zombie spoke the whole reply and finished as if nothing happened"
    assert "Respuesta a la segunda" in ws.captions(), "the next turn must still run"
    assert any(j["type"] == "turn_end" and j.get("turn") == 2 for j in jsons)


class HangingSynthHttp:
    """A request that never delivers a byte, on a stream whose teardown takes a beat — so close()'s
    await on the freshly-cancelled synth task is a real window for the CALLER's own cancellation."""

    def stream(self, method, url, json=None, headers=None):
        class Resp:
            status_code = 200

            async def aread(self_inner):
                return b""

            async def aiter_bytes(self_inner):
                await asyncio.sleep(3600)
                yield b""

        class CM:
            async def __aenter__(self_inner):
                return Resp()

            async def __aexit__(self_inner, *a):
                await asyncio.sleep(0.3)
                return False

        return CM()

    async def aclose(self):
        pass


def test_a_cancel_landing_inside_expressive_close_completes_teardown_then_propagates(session_env):
    async def scenario():
        client = ExpressiveTtsClient("voz", http_client=HangingSynthHttp())
        await client.connect()
        await client.send_text("Frase que queda en vuelo para siempre. ")
        for _ in range(200):
            if client._tasks:
                break
            await asyncio.sleep(0.005)
        assert client._tasks, "the synth request must be in flight before closing"
        started = asyncio.Event()

        async def do_close():
            started.set()
            await client.close()

        closer = asyncio.create_task(do_close())
        await started.wait()
        await asyncio.sleep(0.05)
        closer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closer, 5)
        assert client._client is None and client._dispatcher is None and client._watchdog is None, \
            "a cancel caught mid-close must not skip the rest of the teardown"
        assert not client._tasks, "no synth task may survive close()"

    asyncio.run(scenario())


class Rejecting401Http:
    """First request streams audio; every later one is a 401 whose body carries the given ElevenLabs
    detail status — the mid-call revocation / exhausted-quota shape (both are HTTP 401, distinguished
    only by detail.status per the EL error docs)."""

    def __init__(self, body: bytes):
        self.body = body
        self.calls = 0

    def stream(self, method, url, json=None, headers=None):
        self.calls += 1
        ok = self.calls == 1
        outer = self

        class Resp:
            status_code = 200 if ok else 401

            async def aread(self_inner):
                return outer.body

            async def aiter_bytes(self_inner):
                await asyncio.sleep(0.01)
                # A real error body arrives on the stream too — the client reads it bounded from here.
                yield b"AUDIO1" if ok else outer.body

        class CM:
            async def __aenter__(self_inner):
                return Resp()

            async def __aexit__(self_inner, *a):
                return False

        return CM()

    async def aclose(self):
        pass


async def run_turns(ws, session_id, texts):
    session = vs.VoiceSession(ws, session_id, db=FakeDB(), soul_patterns={})
    runner = asyncio.create_task(session.run())
    for i, text in enumerate(texts, start=1):
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": text})})
        for _ in range(400):
            if sum(1 for j in ws.jsons() if j["type"] == "turn_end") >= i:
                break
            await asyncio.sleep(0.05)
    ws.incoming.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(runner, 10)


def three_slow_sentences():
    async def loop(items, sid, db, queue, patterns, **kw):
        for s in ("Primera frase con voz. ", "Segunda frase ya rechazada. ", "Tercera frase. "):
            await queue.put(s)
            await asyncio.sleep(0.15)
        return "texto completo"

    return loop


def test_a_key_rejected_mid_call_is_fatal_and_synthesis_stops(session_env):
    http = Rejecting401Http(b'{"detail":{"status":"invalid_api_key","message":"Invalid API key"}}')
    session_env.setattr(vs, "TtsClient", expressive_factory(http))
    session_env.setattr(vs, "agentic_loop", three_slow_sentences())
    ws = FakeWS()

    asyncio.run(run_turns(ws, "vrec-auth-fatal", ["háblame de algo", "sigues ahí"]))

    assert ("tts_auth", True) in ws.errors(), \
        "a revoked key is never coming back this session — the user must get the fatal notice"
    assert ("tts_stream", False) not in ws.errors(), \
        "and not the 'her voice cut out for a moment' pill"
    assert ws.errors().count(("tts_auth", True)) == 1, "one fatal for the session, not one per turn"
    assert http.calls == 2, f"{http.calls} requests — a rejected key must stop synthesis for good"
    assert sum(1 for j in ws.jsons() if j["type"] == "turn_end") == 2, "captions-only turns still end"
    assert all(s in ws.captions() for s in ("Primera", "Tercera")), "captions survive the dead voice"
    assert MicGatePort().drive(ws.jsons())


def test_exhausted_quota_mid_call_is_fatal_and_names_the_quota(session_env):
    http = Rejecting401Http(
        b'{"detail":{"status":"quota_exceeded","message":"This request exceeds your quota."}}'
    )
    session_env.setattr(vs, "TtsClient", expressive_factory(http))
    session_env.setattr(vs, "agentic_loop", three_slow_sentences())
    ws = FakeWS()

    asyncio.run(run_turns(ws, "vrec-quota-fatal", ["háblame de algo", "sigues ahí"]))

    quota_frames = [j for j in ws.jsons() if j["type"] == "error" and j["code"] == "tts_quota"]
    assert quota_frames and quota_frames[0]["fatal"] is True, \
        "exhausted quota is not a hiccup — it must surface as its own fatal code"
    assert "quota" in quota_frames[0]["message"].lower(), \
        "the message must say quota, so nobody rotates a perfectly good key"
    assert ("tts_auth", True) not in ws.errors(), "quota must not masquerade as a rejected key"
    assert ("tts_stream", False) not in ws.errors()
    assert http.calls == 2, f"{http.calls} requests — exhausted quota must stop synthesis for good"
    assert MicGatePort().drive(ws.jsons())


class MarkedHttp:
    """Scripted by request TEXT and attempt number, the way the adversarial review's rig was — call
    NUMBERS lie once batching merges sentences, which is how two of the findings below were missed.
    A request 500s while any marker it contains still has scripted failures left; a `stall` marker
    hangs its first matching request open at 200 with zero bytes (the stalled-stream shape); a `slow` marker
    delays that request's first byte. Successful responses stream the request text itself back as
    the audio bytes, so the byte trace the fake browser received literally names the sentences that
    reached the client."""

    def __init__(self, fail=None, stall=frozenset(), slow=None):
        self.fail = dict(fail or {})
        self.stall = set(stall)
        self.slow = dict(slow or {})
        self.calls = 0
        self.texts: list[str] = []

    def stream(self, method, url, json=None, headers=None):
        self.calls += 1
        text = json["text"]
        self.texts.append(text)
        failing = False
        for marker, left in self.fail.items():
            if marker in text and left > 0:
                self.fail[marker] = left - 1
                failing = True
        hang = False
        for marker in list(self.stall):
            if marker in text:
                self.stall.discard(marker)
                hang = True
        delay = max((secs for marker, secs in self.slow.items() if marker in text), default=0.0)

        class Resp:
            status_code = 500 if failing else 200

            async def aread(self_inner):
                return b"simulated ElevenLabs 500"

            async def aiter_bytes(self_inner):
                if hang:
                    await asyncio.Event().wait()
                await asyncio.sleep(delay or 0.01)
                yield text.encode()

        class CM:
            async def __aenter__(self_inner):
                return Resp()

            async def __aexit__(self_inner, *a):
                return False

        return CM()

    async def aclose(self):
        pass


def heard_by_client(ws) -> str:
    return b"".join(ws.blobs()).decode("utf-8", errors="replace")


def test_sentences_trapped_in_a_dead_client_are_respoken_not_lost(session_env):
    """Trapped sentences, first shape: the reader's death is only OBSERVED at the next feed, so
    everything fed in between died inside the dead client with no resend — measured 3 of 5 sentences
    silent. The dead client still HOLDS that text (it was never sent anywhere), so the recovery must
    drain it into the fresh segment: every sentence of which nothing was ever heard reaches the
    client exactly once."""
    session_env.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    http = MarkedHttp(fail={"Segunda": 2, "Tercera": 2})
    session_env.setattr(vs, "TtsClient", expressive_factory(http))

    async def loop(items, sid, db, queue, patterns, **kw):
        for s in (
            "Primera frase que suena bien. ",
            "Segunda frase que muere. ",
            "Tercera frase que tambien muere. ",
            "Cuarta frase atrapada en la cola. ",
            "Quinta frase que cierra el turno. ",
        ):
            await queue.put(s)
            await asyncio.sleep(0.15)
        return "texto completo"

    session_env.setattr(vs, "agentic_loop", loop)
    ws = FakeWS()

    asyncio.run(run_turn(ws, "vrec-trapped"))

    heard = heard_by_client(ws)
    for sentence in ("Primera", "Segunda", "Tercera", "Cuarta", "Quinta"):
        assert heard.count(sentence) == 1, \
            f"{sentence!r} must reach the client exactly once (heard: {heard!r})"
    assert http.calls == 7, \
        f"{http.calls} requests — 1 ok + 2x2 failed + 1 recovery batch + 1 end-flush"
    assert ws.errors() == [("tts_stream", False)]
    assert "turn_end" in ws.types()
    assert MicGatePort().drive(ws.jsons())


def test_a_death_at_the_end_of_the_turn_still_speaks_the_trapped_tail(session_env):
    """Trapped sentences, second shape: the stream dies after the LAST feed, so no
    later feed ever observes it — the final sentence stays held in the dead client's splitter and
    was never recovered even with the retry budget intact. The graceful close must notice the dead
    reader and refund the trapped tail through one fresh segment."""
    session_env.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    http = MarkedHttp(fail={"Segunda": 2, "Tercera": 2})
    session_env.setattr(vs, "TtsClient", expressive_factory(http))

    async def loop(items, sid, db, queue, patterns, **kw):
        await queue.put("Primera frase que suena bien. ")
        await asyncio.sleep(0.15)
        await queue.put("Segunda frase que muere. ")
        await asyncio.sleep(0.15)
        await queue.put("Tercera frase que muere tambien. Y esta ultima frase queda atrapada.")
        await asyncio.sleep(0.3)
        return "texto completo"

    session_env.setattr(vs, "agentic_loop", loop)
    ws = FakeWS()

    asyncio.run(run_turn(ws, "vrec-tail"))

    heard = heard_by_client(ws)
    assert heard.count("queda atrapada") == 1, \
        f"the last real sentence must be recovered, not die in the splitter (heard: {heard!r})"
    for sentence in ("Primera", "Segunda", "Tercera"):
        assert heard.count(sentence) == 1, f"{sentence!r} heard {heard.count(sentence)}x: {heard!r}"
    assert http.calls == 6, \
        f"{http.calls} requests — 1 ok + 2x2 failed + 1 recovery batch carrying the whole tail"
    assert "turn_end" in ws.types()
    assert MicGatePort().drive(ws.jsons())


def test_a_request_lost_at_the_very_end_is_refunded_at_finish(session_env):
    """A single lost request mid-stream is skipped by contract so the later sentences keep flowing —
    but when the lost request is the LAST one there is nothing after it to protect, nothing of it
    was ever heard, and the turn still holds its retry budget: the graceful close re-speaks it on a
    fresh segment instead of silently ending the turn one sentence short."""
    session_env.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    http = MarkedHttp(fail={"recuperada": 2})
    session_env.setattr(vs, "TtsClient", expressive_factory(http))

    async def loop(items, sid, db, queue, patterns, **kw):
        await queue.put("Primera frase que suena bien. ")
        await asyncio.sleep(0.15)
        await queue.put("Ultima frase recuperada tras el fallo.")
        await asyncio.sleep(0.3)
        return "texto completo"

    session_env.setattr(vs, "agentic_loop", loop)
    ws = FakeWS()

    asyncio.run(run_turn(ws, "vrec-last-skip"))

    heard = heard_by_client(ws)
    assert heard.count("recuperada") == 1, \
        f"the skipped final sentence was never heard, so it must be refunded (heard: {heard!r})"
    assert heard.count("Primera") == 1
    assert http.calls == 4, f"{http.calls} requests — 1 ok + 2 failed attempts + 1 refund"
    assert ws.errors() == [], "an absorbed loss that ends fully spoken is not an error"
    assert "turn_end" in ws.types()
    assert MicGatePort().drive(ws.jsons())


def test_a_stalled_request_pauses_the_dispatcher_instead_of_burning_quota(monkeypatch):
    """The stalled stream, at the client: a 200 that never delivers a byte used to leave the
    dispatcher working for the whole 30s watchdog window — every request it launched was synthesis
    nobody would hear.
    Ordered playback means nothing launched behind the byteless oldest request could be heard before
    it resolves, so once it has been byteless past the suspicion threshold the dispatcher must stop
    launching."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-stall")
    config.set_api_key(None)
    monkeypatch.setattr("kotoba.core.voice.tts_rest.STALL_SUSPECT_SECS", 0.1)
    http = MarkedHttp(stall={"Primera"})

    async def go():
        client = ExpressiveTtsClient("voz", http_client=http)
        await client.connect()
        await client.send_text("Primera frase que se cuelga sin un byte. X")
        for _ in range(200):
            if http.calls:
                break
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.15)
        for s in ("Segunda frase. ", "Tercera frase. ", "Cuarta frase. ", "Quinta frase. "):
            await client.send_text(s)
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        stalled_calls = http.calls
        await client.close()
        return stalled_calls

    stalled_calls = asyncio.run(go())
    assert stalled_calls == 1, \
        f"{stalled_calls} requests during the stall — nothing may launch behind a byteless request"
    assert http.calls == 1, "teardown must not launch what the pause was holding back"


def test_a_slow_but_healthy_request_is_not_punished(monkeypatch):
    """The cost side of that judgement: a request whose first byte arrives late (past suspicion,
    inside the watchdog) is merely slow, not dead. The pause may delay prefetch — which ordered
    playback already hides — but nothing may be killed, dropped, or reordered, and the dispatcher
    must resume: a watchdog that fires on a merely slow request is worse than the bug."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-slow")
    config.set_api_key(None)
    monkeypatch.setattr("kotoba.core.voice.tts_rest.STALL_SUSPECT_SECS", 0.1)
    http = MarkedHttp(slow={"Primera": 0.3})

    async def go():
        client = ExpressiveTtsClient("voz", http_client=http)
        await client.connect()
        await client.send_text("Primera frase lenta pero viva. ")
        await asyncio.sleep(0.15)
        await client.send_text("Segunda frase normal. ")
        await asyncio.sleep(0.05)
        await client.send_text("Tercera frase normal.")
        await client.end()
        return [c async for c in client.audio_chunks()]

    chunks = asyncio.run(go())
    heard = b"".join(chunks).decode()
    assert heard.index("Primera") < heard.index("Segunda") < heard.index("Tercera")
    assert all(heard.count(s) == 1 for s in ("Primera", "Segunda", "Tercera")), heard
    assert all("500" not in t for t in http.texts)


def test_a_stall_costs_the_window_not_the_sentences(session_env):
    """The stalled stream end-to-end at the session: it dies at the reader's watchdog, and because
    the dispatcher paused on suspicion AND the dead client refunds what it never delivered, the turn
    resumes with every sentence — including the stalled one, no byte of which was ever heard —
    spoken exactly once, having burned at most one sibling request inside the suspicion window."""
    session_env.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    session_env.setattr("kotoba.core.voice.tts_rest.STALL_SUSPECT_SECS", 0.1)
    http = MarkedHttp(stall={"Primera"})

    def make(voice_id=None, **kw):
        return ExpressiveTtsClient(
            voice_id, output_format=kw.get("output_format"), http_client=http, recv_timeout=0.4
        )

    session_env.setattr(vs, "TtsClient", make)

    async def loop(items, sid, db, queue, patterns, **kw):
        for s in (
            "Primera frase que se cuelga. ",
            "Segunda frase. ",
            "Tercera frase. ",
            "Cuarta frase. ",
            "Quinta frase final.",
        ):
            await queue.put(s)
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.6)
        return "texto completo"

    session_env.setattr(vs, "agentic_loop", loop)
    ws = FakeWS()

    asyncio.run(run_turn(ws, "vrec-stall"))

    heard = heard_by_client(ws)
    for sentence in ("Primera", "Segunda", "Tercera", "Cuarta", "Quinta"):
        assert heard.count(sentence) == 1, \
            f"{sentence!r} heard {heard.count(sentence)}x — a stall costs latency, never sentences"
    assert http.calls <= 4, \
        f"{http.calls} requests — the stalled one, at most one pre-suspicion sibling, and the refund"
    assert ("tts_stream", False) in ws.errors()
    assert "turn_end" in ws.types()
    assert MicGatePort().drive(ws.jsons())


class DiesAfterLastFeedTts:
    """WS-engine shape for the end-of-turn death: the reader dies after the last feed, when no later
    feed will observe it. Text sent down a WS is gone the moment it is sent — there is nothing to
    refund — so the honest floor is: no phantom reopen, the bracket still closes, the budget stays."""

    instances: list["DiesAfterLastFeedTts"] = []

    def __init__(self, voice_id=None, **kw):
        DiesAfterLastFeedTts.instances.append(self)
        self.text: list[str] = []

    async def connect(self):
        pass

    async def send_text(self, text):
        if text:
            self.text.append(text)

    async def end(self):
        pass

    async def audio_chunks(self):
        yield b"\x01\x01"
        await asyncio.sleep(0.1)
        raise VoiceError("TTS stream stalled (simulated, after the last feed)")

    async def close(self):
        pass


def test_ws_engine_death_at_turn_end_closes_the_bracket_without_a_phantom_reopen(session_env):
    DiesAfterLastFeedTts.instances = []
    session_env.setattr(vs, "TtsClient", DiesAfterLastFeedTts)

    async def loop(items, sid, db, queue, patterns, **kw):
        await queue.put("Una unica frase que suena. ")
        await asyncio.sleep(0.3)
        return "texto completo"

    session_env.setattr(vs, "agentic_loop", loop)
    ws = FakeWS()

    asyncio.run(run_turn(ws, "vrec-ws-tail"))

    assert len(DiesAfterLastFeedTts.instances) == 1, \
        "nothing is recoverable over the WS — a fresh segment with nothing to say must not open"
    assert ws.types().count("audio_start") == 1
    assert "audio_end" in ws.types() and "turn_end" in ws.types()
    assert ("tts_stream", False) in ws.errors()
    assert MicGatePort().drive(ws.jsons())


def test_unspoken_tail_refunds_only_what_was_never_heard(monkeypatch):
    """The drain's honesty contract, pinned at the client: text of which ANY byte was yielded to the
    reader is gone (resending it would repeat speech the user heard); everything behind the last
    heard byte — failed requests that never delivered, queued sentences, the splitter's raw
    remainder — comes back in speaking order, raw remainder last and seamless. A second call
    refunds nothing."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-tail")
    config.set_api_key(None)
    monkeypatch.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    http = MarkedHttp(fail={"Segunda": 2, "Tercera": 2})

    async def go():
        client = ExpressiveTtsClient("voz", http_client=http)
        await client.connect()
        await client.send_text("Primera frase buena. Segunda frase mala. ")
        await asyncio.sleep(0.1)
        await client.send_text("Tercera frase mala. ")
        await asyncio.sleep(0.1)
        await client.send_text("Cuarta frase presa. Quinta sin terminar")
        await asyncio.sleep(0.1)
        heard = b""
        with pytest.raises(VoiceError):
            async for chunk in client.audio_chunks():
                heard += chunk
        tail = client.unspoken_tail()
        second = client.unspoken_tail()
        await client.close()
        return heard.decode(), tail, second

    heard, tail, second = asyncio.run(go())
    assert "Primera" in heard and "Primera" not in tail, "a heard sentence must never be refunded"
    for sentence in ("Segunda", "Tercera", "Cuarta"):
        assert sentence in tail, f"{sentence!r} was never heard — it must be in the tail: {tail!r}"
    assert tail.endswith("Quinta sin terminar"), \
        f"the raw splitter remainder goes last, uncut, so `tail + next_chunk` stays seamless: {tail!r}"
    assert second == "", "a second drain must not refund the same text twice"


# ---- the segment idle clock: TTS input, not queue traffic ---------------------------------------


class ClockedTts:
    """Timestamps every event on its own INPUT side, so a test can ask what the idle clock is really
    about: what is the longest this stream sat open with nothing fed into it?"""

    instances: list["ClockedTts"] = []

    def __init__(self, voice_id=None, **kw):
        self.text: list[str] = []
        self.stamps: list[float] = []
        self.ended = False
        ClockedTts.instances.append(self)

    def _stamp(self):
        self.stamps.append(asyncio.get_running_loop().time())

    def longest_silence(self) -> float:
        return max((b - a for a, b in zip(self.stamps, self.stamps[1:])), default=0.0)

    async def connect(self):
        self._stamp()

    async def send_text(self, text):
        if text:
            self.text.append(text)
            self._stamp()

    async def end(self):
        self.ended = True
        self._stamp()

    async def audio_chunks(self):
        while not self.ended:
            await asyncio.sleep(0.005)
        yield b"\x01\x02"

    async def close(self):
        pass


async def _drive_fenced_reply(session, ticks: int, step: float = 0.05) -> None:
    """One reply shaped like the defect: prose, then a long fenced code block whose every token the
    CodeFenceFilter suppresses while the queue stays continuously busy, then prose again."""
    queue: asyncio.Queue = asyncio.Queue()
    pump = asyncio.create_task(session._pump_speech(queue, 1))
    await queue.put("Aquí lo tienes. Mira:")
    await asyncio.sleep(step)
    await queue.put("```python\n")
    for i in range(ticks):
        await queue.put(f"print({i})\n")
        await asyncio.sleep(step)
    await queue.put("```\n\nY eso es todo.")
    await queue.put(sse.DONE_SENTINEL)
    await asyncio.wait_for(pump, 10)


def test_segment_idle_clock_measures_tts_input_not_queue_activity(session_env):
    """Driving the timer off queue arrivals kept the stream open through the whole fence: on the WS
    engine that is the ~20s input-socket close it exists to prevent, arriving on a healthy client."""
    session_env.setattr(vs, "SEGMENT_IDLE_SECS", 0.3)
    ClockedTts.instances = []
    session_env.setattr(vs, "TtsClient", ClockedTts)
    ws = FakeWS()
    session = vs.VoiceSession(ws, "vseg-fence-ws", db=FakeDB(), soul_patterns={})

    asyncio.run(_drive_fenced_reply(session, ticks=24))

    first = ClockedTts.instances[0]
    assert first.text, "the prose before the fence must have been spoken"
    silence = first.longest_silence()
    assert silence <= vs.SEGMENT_IDLE_SECS + 0.2, (
        f"the stream sat {silence:.2f}s with nothing fed to it (budget {vs.SEGMENT_IDLE_SECS}s)"
    )
    assert len(ClockedTts.instances) == 2, "the prose after the fence belongs to a fresh segment"


def test_a_suppressed_fence_does_not_stall_the_expressive_reader(session_env):
    """The same defect on the DEFAULT engine: audio_chunks() raises "TTS stream stalled" when no new
    request lands inside its watchdog, so a healthy client produced a transient tts_stream frame and
    burned one of SEGMENT_DEATH_RETRIES."""
    session_env.setattr(vs, "SEGMENT_IDLE_SECS", 0.4)
    http = ScriptedHttp()

    def make(voice_id=None, **kw):
        return ExpressiveTtsClient(
            voice_id, output_format=kw.get("output_format"), http_client=http, recv_timeout=0.8
        )

    session_env.setattr(vs, "TtsClient", make)
    ws = FakeWS()
    session = vs.VoiceSession(ws, "vseg-fence-rest", db=FakeDB(), soul_patterns={})

    asyncio.run(_drive_fenced_reply(session, ticks=28))

    assert http.texts, "the prose before the fence must have been synthesized"
    assert ws.errors() == [], f"a healthy client reported a stall: {ws.errors()}"
