"""Expressive TTS engine (eleven_v3 REST) + engine selection — no network.

Covers: engine dispatch (expressive default / fast / runtime override), tag preservation on the
expressive path vs stripping on fast, per-sentence pipelining with ordered audio, sentence batching,
the idle-hold force flush, HTTP error mapping, mid-turn failure recovery (retry once / skip one
sentence / give up and stop dispatching after a streak, an audit finding), and the session seam
(captions tag-free while the engine receives the tags).
"""
from __future__ import annotations

import asyncio
import json

import pytest

import kotoba.core.voice.session as vs
from kotoba.core import app_settings
from kotoba.core.voice import config
from kotoba.core.voice.config import VoiceAuthError, VoiceError, VoiceStreamClosed
from kotoba.core.voice.tts import TagStrippingTts, TtsClient, create_tts_client
from kotoba.core.voice.tts_rest import ExpressiveTtsClient


@pytest.fixture(autouse=True)
def _test_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_test_key_xyz")
    monkeypatch.delenv("KOTOBA_TTS_ENGINE", raising=False)
    monkeypatch.delenv("KOTOBA_TTS_STABILITY", raising=False)
    monkeypatch.delenv("KOTOBA_TTS_SIMILARITY", raising=False)
    monkeypatch.delenv("KOTOBA_TTS_STYLE", raising=False)
    config.set_api_key(None)


class FakeResponse:
    def __init__(self, chunks=(b"pcm",), status_code=200, body=b""):
        self.chunks = list(chunks)
        self.status_code = status_code
        self.body = body
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.finished = False

    async def __aenter__(self):
        self.started.set()
        return self

    async def __aexit__(self, *exc):
        self.finished = True

    async def aread(self):
        return self.body

    async def aiter_bytes(self):
        await self.release.wait()
        for c in self.chunks:
            yield c


class FakeHttp:
    def __init__(self, responses=None, default=None):
        self.responses = list(responses or [])
        self.default = default
        self.requests: list[dict] = []

    def stream(self, method, url, json=None, headers=None):
        self.requests.append({"method": method, "url": url, "json": json})
        if self.responses:
            return self.responses.pop(0)
        return self.default() if self.default else FakeResponse()

    async def aclose(self):
        pass


# ---- engine selection ---------------------------------------------------------------------------


def test_default_engine_is_expressive():
    assert config.tts_engine() == "expressive"
    client = create_tts_client("v1", output_format="pcm_24000", inactivity_timeout=60)
    assert isinstance(client, ExpressiveTtsClient)
    assert "output_format=pcm_24000" in client._url and "/stream" in client._url


def test_fast_engine_selected_by_env(monkeypatch):
    monkeypatch.setenv("KOTOBA_TTS_ENGINE", "fast")
    client = create_tts_client("v1", output_format="pcm_24000", inactivity_timeout=60)
    assert isinstance(client, TagStrippingTts)
    assert isinstance(client._inner, TtsClient)


def test_engine_runtime_override_and_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    assert app_settings.set_runtime("tts_engine", "FAST") == "fast"
    assert config.tts_engine() == "fast"
    with pytest.raises(ValueError):
        app_settings.set_runtime("tts_engine", "turbo")
    assert app_settings.runtime_all()["tts_engine"] == "fast"
    app_settings.set_runtime("tts_engine", "expressive")
    assert config.tts_engine() == "expressive"


def test_bogus_engine_value_falls_back_to_expressive(monkeypatch):
    monkeypatch.setenv("KOTOBA_TTS_ENGINE", "warp9")
    assert config.tts_engine() == "expressive"


# ---- fast path: tags stripped -------------------------------------------------------------------


class RecorderTts:
    def __init__(self):
        self.text: list[str] = []
        self.ended = False

    async def connect(self):
        pass

    async def send_text(self, text):
        self.text.append(text)

    async def end(self):
        self.ended = True

    async def close(self):
        pass


def test_fast_wrapper_strips_tags_even_split_across_chunks():
    """The fast engine cannot perform audio tags, so the wrapper removes them — including a tag whose
    opening bracket and closing bracket arrive in different chunks.

    An unclosed `[` still pending at the end of the stream is literal text, not a tag, and is passed
    through as written."""
    inner = RecorderTts()
    wrapper = TagStrippingTts(inner)

    async def go():
        await wrapper.send_text("[happily] Hola [ner")
        await wrapper.send_text("vous] amiga [")
        await wrapper.end()

    asyncio.run(go())
    joined = "".join(inner.text)
    assert "[happily]" not in joined and "[nervous]" not in joined
    assert "Hola" in joined and "amiga" in joined
    assert joined.endswith("[")
    assert inner.ended


# ---- expressive path ----------------------------------------------------------------------------


def test_tags_reach_the_expressive_payload():
    """On the expressive path the tags are the point: they reach eleven_v3 verbatim, every request
    names that model and the requested output format, and the API key never appears in a payload."""
    http = FakeHttp()

    async def go():
        client = ExpressiveTtsClient("v1", output_format="pcm_24000", http_client=http)
        await client.connect()
        await client.send_text("[happily] ¡Qué alegría verte! [nervous] ¿Puedo contarte algo? Sí")
        await client.end()
        return [c async for c in client.audio_chunks()]

    chunks = asyncio.run(go())
    assert chunks
    sent = " ".join(r["json"]["text"] for r in http.requests)
    assert "[happily]" in sent and "[nervous]" in sent
    assert all(r["json"]["model_id"] == "eleven_v3" for r in http.requests)
    assert all("output_format=pcm_24000" in r["url"] for r in http.requests)
    assert "el_test_key_xyz" not in json.dumps(http.requests)


def test_pipelining_starts_next_request_while_first_still_streams():
    """Sentences are dispatched in parallel but played in order.

    The first response is held open and the second request must still launch — that is the
    pipelining. `end()` flushes the trailing fragment as a third request. What comes back out is
    sentence order, never completion order."""
    r1 = FakeResponse(chunks=[b"one"])
    r1.release.clear()
    r2 = FakeResponse(chunks=[b"two"])
    r3 = FakeResponse(chunks=[b"three"])
    http = FakeHttp([r1, r2, r3])

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("Primera frase completa aquí. Next")
        await asyncio.wait_for(r1.started.wait(), 1)
        await client.send_text(" segunda frase entera va. More")
        await asyncio.wait_for(r2.started.wait(), 1)
        assert not r1.finished
        r1.release.set()
        await client.end()
        return [c async for c in client.audio_chunks()]

    chunks = asyncio.run(go())
    assert chunks == [b"one", b"two", b"three"]
    assert len(http.requests) == 3


def test_burst_sentences_coalesce_into_one_request():
    """Several finished sentences already waiting when the dispatcher starts go out as ONE request
    rather than one apiece — the batching that keeps a long reply from becoming a burst of calls."""
    http = FakeHttp()

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.send_text("Una frase. Otra frase. Y otra más. Fin")
        await client.connect()
        await client.end()
        return [c async for c in client.audio_chunks()]

    asyncio.run(go())
    assert len(http.requests) == 1
    assert http.requests[0]["json"]["text"] == "Una frase. Otra frase. Y otra más. Fin"


def test_idle_hold_flushes_a_finished_sentence(monkeypatch):
    """A finished sentence with nothing following it must not wait for more text.

    This is the tool-call pause: she announces what she is about to do and then goes quiet while the
    tool runs. The idle hold forces that sentence out on its own."""
    monkeypatch.setattr("kotoba.core.voice.tts_rest.HOLD_SECS", 0.05)
    http = FakeHttp()

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("Ya te lo busco.")
        for _ in range(100):
            if http.requests:
                break
            await asyncio.sleep(0.02)
        await client.close()

    asyncio.run(go())
    assert http.requests and http.requests[0]["json"]["text"] == "Ya te lo busco."


def test_http_error_maps_to_voice_error(monkeypatch):
    """The mapping (HTTP failure ↦ VoiceError with the status in the message) still surfaces from
    audio_chunks — it now takes PERSISTENT failure to get there, because single lost requests are
    retried/skipped by design (see the recovery tests below)."""
    monkeypatch.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    http = FakeHttp(default=lambda: FakeResponse(status_code=500, body=b"boom"))

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("Primera frase condenada al fallo. Sig")
        await asyncio.sleep(0.05)
        await client.send_text("ue otra condenada igual. X")
        await asyncio.sleep(0.05)
        await client.end()
        return [c async for c in client.audio_chunks()]

    with pytest.raises(VoiceError, match="500"):
        asyncio.run(go())


def test_transient_failure_is_retried_and_the_sentence_still_plays(monkeypatch):
    monkeypatch.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    http = FakeHttp([FakeResponse(status_code=500, body=b"blip"), FakeResponse(chunks=[b"pcm-ok"])])

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("Una frase que sufre un 500 pasajero.")
        await client.end()
        return [c async for c in client.audio_chunks()]

    chunks = asyncio.run(go())
    assert chunks == [b"pcm-ok"], "one transient 500 must not cost the sentence"
    assert len(http.requests) == 2
    assert http.requests[0]["json"]["text"] == http.requests[1]["json"]["text"]


def test_definitive_failure_skips_only_that_sentence(monkeypatch):
    monkeypatch.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    http = FakeHttp([
        FakeResponse(chunks=[b"one"]),
        FakeResponse(status_code=500, body=b"boom"),
        FakeResponse(status_code=500, body=b"boom"),
        FakeResponse(chunks=[b"three"]),
    ])

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("Primera frase que suena bien. Seg")
        await asyncio.sleep(0.05)
        await client.send_text("unda frase que muere del todo. More")
        await asyncio.sleep(0.05)
        await client.end()
        return [c async for c in client.audio_chunks()]

    chunks = asyncio.run(go())
    assert chunks == [b"one", b"three"], "the sentences around the lost one must still play, in order"
    assert len(http.requests) == 4


def test_persistent_failure_gives_up_and_stops_dispatching(monkeypatch):
    monkeypatch.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    http = FakeHttp(default=lambda: FakeResponse(status_code=429, body=b"quota pressure"))

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("Primera frase que falla. Seg")
        await asyncio.sleep(0.05)
        await client.send_text("unda frase que falla. Ter")
        await asyncio.sleep(0.05)
        await client.send_text("cera frase que ya no debe pedirse. Cuarta que tampoco. Fin")
        await asyncio.sleep(0.05)
        await client.end()
        async for _ in client.audio_chunks():
            pass

    with pytest.raises(VoiceError, match="429"):
        asyncio.run(go())
    assert len(http.requests) == 4, "after the streak, no further request may be made"
    asked = " ".join(r["json"]["text"] for r in http.requests)
    assert "cera frase" not in asked and "Cuarta" not in asked and "Fin" not in asked


def test_http_401_maps_to_auth_error():
    http = FakeHttp([FakeResponse(status_code=401, body=b"bad key")])

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("Hola hola. X")
        await client.end()
        return [c async for c in client.audio_chunks()]

    with pytest.raises(VoiceAuthError):
        asyncio.run(go())


def test_connect_without_key_raises_before_io(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")

    async def go():
        await ExpressiveTtsClient("v1", http_client=FakeHttp()).connect()

    with pytest.raises(VoiceAuthError):
        asyncio.run(go())


def test_send_text_after_end_raises():
    async def go():
        client = ExpressiveTtsClient("v1", http_client=FakeHttp())
        await client.connect()
        await client.end()
        await client.send_text("late")

    with pytest.raises(VoiceStreamClosed):
        asyncio.run(go())


def test_voice_settings_from_env(monkeypatch):
    monkeypatch.setenv("KOTOBA_TTS_STABILITY", "0.5")
    http = FakeHttp()

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("Con estilo. X")
        await client.end()
        async for _ in client.audio_chunks():
            pass

    asyncio.run(go())
    assert http.requests[0]["json"]["voice_settings"] == {"stability": 0.5}


def test_no_voice_settings_key_when_unset():
    http = FakeHttp()

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("Sin ajustes. X")
        await client.end()
        async for _ in client.audio_chunks():
            pass

    asyncio.run(go())
    assert "voice_settings" not in http.requests[0]["json"]


def test_active_emotion_carries_into_tagless_requests():
    """A sustained emotion tag is re-stated on every following request.

    Each request is a separate call to eleven_v3 with no memory of the last one, so a tag written
    once at the top of a reply would otherwise colour only the first sentence."""
    http = FakeHttp()

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("[nervous] ¿Puedo contarte algo? Next")
        await asyncio.sleep(0.05)
        await client.send_text(" es un poco delicado esto. More")
        await asyncio.sleep(0.05)
        await client.end()
        async for _ in client.audio_chunks():
            pass

    asyncio.run(go())
    texts = [r["json"]["text"] for r in http.requests]
    assert texts[0].startswith("[nervous]") and texts[0].count("[nervous]") == 1
    assert all(t.startswith("[nervous] ") for t in texts[1:])


def test_one_shot_tags_are_not_carried_forward():
    """Only a SUSTAINED emotion carries. A one-shot tag such as `[laughs]` is an event: repeating it
    on every later request would have her laugh at the end of every sentence."""
    http = FakeHttp()

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("[happily] ¡Hola! [laughs] Qué bueno verte. Next")
        await asyncio.sleep(0.05)
        await client.send_text(" vamos a empezar ya. More")
        await asyncio.sleep(0.05)
        await client.end()
        async for _ in client.audio_chunks():
            pass

    asyncio.run(go())
    later = " ".join(r["json"]["text"] for r in http.requests[1:])
    assert "[laughs]" not in later
    assert later.count("[happily]") == len(http.requests[1:])


def test_wordless_hum_never_carries_the_active_tag():
    """The tool heartbeat (loop._NEUTRAL_FILLER) reaches this engine as its own request: the splitter
    holds "Mmm..." and _watch_hold forces it out alone. Carrying the emotion onto it produces exactly
    the shape loop._heartbeat_lines argues against — a rare tag on a six-character body, whose failure
    mode is the tag NAME spoken aloud. The prose around it must still be coloured, and the hum must
    not clear the sustained emotion either — the requests after it still carry the tag."""
    http = FakeHttp()

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("[whispers] Voy a mirarlo ahora mismo. Espera")
        await asyncio.sleep(0.6)
        await client.send_text(" un momento, por favor. ")
        await asyncio.sleep(0.6)
        await client.send_text("Mmm... ")
        await asyncio.sleep(0.6)
        await client.send_text("Ya lo tengo aquí delante. ")
        await asyncio.sleep(0.6)
        await client.end()
        async for _ in client.audio_chunks():
            pass

    asyncio.run(go())
    texts = [r["json"]["text"] for r in http.requests]
    assert [t for t in texts if "Mmm" in t] == ["Mmm..."]
    assert texts[0].startswith("[whispers]") and texts[1].startswith("[whispers] ")
    assert texts[-1].startswith("[whispers] ")


# ---- the session seam: captions are tag-free, the engine gets the tags --------------------------


class SessionFakeTts:
    instances: list["SessionFakeTts"] = []

    def __init__(self, voice_id=None, **kwargs):
        self.text: list[str] = []
        self.ended = False
        SessionFakeTts.instances.append(self)

    async def connect(self):
        pass

    async def send_text(self, text):
        if text:
            self.text.append(text)

    async def end(self):
        self.ended = True

    async def audio_chunks(self):
        while not self.ended:
            await asyncio.sleep(0.005)
        yield b"\x01\x02"

    async def close(self):
        pass


def test_session_passes_tags_to_engine_but_not_captions(monkeypatch):
    """Two audiences, one reply: the engine is handed the tags so it can perform them, while the
    captions the user READS come out tag-free."""
    from fastapi.testclient import TestClient

    import kotoba.server as main

    SessionFakeTts.instances = []
    monkeypatch.setattr(vs, "TtsClient", SessionFakeTts)

    async def no_memory(user_text, db):
        return None

    monkeypatch.setattr(vs, "extract_and_save_memory", no_memory)

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        await queue.put("[happily] ¡Hola! Te extrañaba mucho. ")
        await queue.put("[nervous] ¿Está todo bien?")
        return "raw"

    monkeypatch.setattr(vs, "agentic_loop", fake_loop)
    with TestClient(main.app) as client:
        with client.websocket_connect("/api/voice/vws-expr-tags") as ws:
            ws.receive()
            ws.send_text(json.dumps({"type": "text", "text": "hola"}))
            captions = ""
            for _ in range(60):
                msg = ws.receive()
                if msg.get("bytes") is not None:
                    continue
                ev = json.loads(msg["text"])
                if ev["type"] == "assistant_text":
                    captions += ev["text"]
                if ev["type"] == "turn_end":
                    break

    assert "[happily]" not in captions and "[nervous]" not in captions
    assert "Te extrañaba mucho" in captions
    spoken = "".join(t for inst in SessionFakeTts.instances for t in inst.text)
    assert "[happily]" in spoken and "[nervous]" in spoken


TAGGED_REPLY = "[happily] ¡Hola! Te extrañaba mucho."


def _v1_spoken_text(client, session_id: str) -> str:
    r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer k"}, json={
        "messages": [{"role": "user", "content": "hola"}], "session_id": session_id, "stream": True,
    })
    assert r.status_code == 200
    out = ""
    for line in r.text.splitlines():
        if line.startswith("data:") and "[DONE]" not in line:
            try:
                out += json.loads(line[5:].strip())["choices"][0]["delta"].get("content") or ""
            except Exception:
                pass
    return out


def _ws_spoken_text(client, session_id: str) -> str:
    with client.websocket_connect(f"/api/voice/{session_id}") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "hola"}))
        for _ in range(60):
            msg = ws.receive()
            if msg.get("bytes") is not None:
                continue
            if json.loads(msg["text"])["type"] == "turn_end":
                break
    return "".join(t for inst in SessionFakeTts.instances for t in inst.text)


@pytest.mark.parametrize("expressive,tags_survive", [("true", True), ("false", False)])
def test_v1_and_voice_ws_agree_on_expressive_toggle(monkeypatch, tmp_path, expressive, tags_survive):
    """The expressive toggle must mean the SAME thing on both spoken paths. The voice session hardcoded
    keep_valid=True, so with expressive=OFF a valid tag was still performed by v3 on the voice-WS path
    while /v1 stripped it."""
    from fastapi.testclient import TestClient

    import kotoba.server as main

    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("KOTOBA_EXPRESSIVE", expressive)
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    SessionFakeTts.instances = []
    monkeypatch.setattr(vs, "TtsClient", SessionFakeTts)

    async def no_memory(user_text, db):
        return None

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        await queue.put(TAGGED_REPLY)
        return "raw"

    monkeypatch.setattr(vs, "extract_and_save_memory", no_memory)
    monkeypatch.setattr(vs, "agentic_loop", fake_loop)
    monkeypatch.setattr(main, "agentic_loop", fake_loop)

    with TestClient(main.app) as client:
        v1_spoken = _v1_spoken_text(client, "expr-v1")
        ws_spoken = _ws_spoken_text(client, "expr-ws")

    assert ("[happily]" in v1_spoken) is tags_survive
    assert ("[happily]" in ws_spoken) is tags_survive
    assert "Te extrañaba mucho" in v1_spoken and "Te extrañaba mucho" in ws_spoken


# ---- mid-body truncation parity and bounded error bodies ----------------------------------------


class DyingResponse:
    """Streams `chunks`, then dies mid-body — the network failing AFTER partial delivery."""

    status_code = 200

    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        raise AssertionError("error bodies must be read from the stream, bounded — never aread()")

    async def aiter_bytes(self):
        for c in self.chunks:
            yield c
        raise ConnectionError("network died mid-body")


def test_a_request_truncated_mid_sample_cannot_byte_shift_the_next_sentence(monkeypatch):
    """The TTS stream is s16le: a request that died after an ODD number of bytes left the browser's
    split-byte carry permanently misaligned, so every later sentence in the segment played as static.
    The engine now restores parity with one padding byte before moving on."""
    monkeypatch.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    http = FakeHttp([DyingResponse([b"ODD"]), FakeResponse(chunks=[b"EVEN"]), FakeResponse(chunks=[b"ok"])])

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("Primera frase truncada a mitad de muestra. Seg")
        await asyncio.sleep(0.05)
        await client.send_text("unda frase que debe sonar limpia. X")
        await asyncio.sleep(0.05)
        await client.end()
        return [c async for c in client.audio_chunks()]

    chunks = asyncio.run(go())
    assert chunks[:2] == [b"ODD", b"\x00"], f"odd truncation must be padded even, got {chunks}"
    assert b"EVEN" in chunks, "the sentence after the truncated one must still play"
    assert sum(len(c) for c in chunks) % 2 == 0


def test_an_error_body_is_never_buffered_whole(monkeypatch):
    """A non-200 body used to be aread() unbounded — 64 MiB from a broken proxy was 64 MiB in RAM,
    twice (once per attempt). Only ERROR_BODY_CAP bytes may be consumed, and the raised message stays
    a 200-char diagnostic."""
    from kotoba.core.voice.tts_rest import ERROR_BODY_CAP

    monkeypatch.setattr("kotoba.core.voice.tts_rest.RETRY_BACKOFF_SECS", 0)
    consumed = {"chunks": 0}

    class HugeErrorResponse:
        status_code = 503

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def aread(self):
            raise AssertionError("unbounded aread() on an error body")

        async def aiter_bytes(self):
            for _ in range(10_000):
                consumed["chunks"] += 1
                yield b"x" * 1024

    class Http:
        def stream(self, method, url, json=None, headers=None):
            return HugeErrorResponse()

        async def aclose(self):
            pass

    async def go():
        client = ExpressiveTtsClient("v1", http_client=Http())
        await client.connect()
        await client.send_text("Frase condenada. Y")
        await asyncio.sleep(0.05)
        await client.send_text(" otra igual de condenada. X")
        await asyncio.sleep(0.05)
        await client.end()
        return [c async for c in client.audio_chunks()]

    with pytest.raises(VoiceError, match="503") as excinfo:
        asyncio.run(go())
    per_attempt = ERROR_BODY_CAP // 1024 + 1
    assert consumed["chunks"] <= per_attempt * 4, f"read {consumed['chunks']} KiB of a broken body"
    assert len(str(excinfo.value)) < 300


def test_quota_detail_still_classifies_from_the_bounded_read():
    """Capping the error body must not cost the diagnosis: a quota refusal arrives as a 401 whose
    JSON detail is the only thing separating it from a bad key, and it is still classified."""
    body = b'{"detail":{"status":"quota_exceeded","message":"This request exceeds your quota."}}'
    http = FakeHttp([FakeResponse(status_code=401, chunks=[body])])

    async def go():
        client = ExpressiveTtsClient("v1", http_client=http)
        await client.connect()
        await client.send_text("Hola hola. X")
        await client.end()
        return [c async for c in client.audio_chunks()]

    from kotoba.core.voice.config import VoiceQuotaError

    with pytest.raises(VoiceQuotaError):
        asyncio.run(go())
