"""Live QA found one bubble holding the whole reply twice, verbatim, with no separator.

Not a turn-bucket collision: `assistant_text` always carries the same turn number, so
accumulating frames into one bubble is how streaming builds a caption — what doubled was the
text itself, said twice inside ONE response.

Iteration text is ASSIGNED per iteration, never appended, so a repeat split across two model
calls should persist ONE copy — yet the live database held two. The dedup guard caught "S. S."
but never "S1 S2 S1 S2", where each copy is separated from its original by the other sentence.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import kotoba.core.voice.session as vs
from kotoba.core import stream as sse
from kotoba.core.voice import config

SAID = ("[excited] Me pongo con eso ahora mismo, y además no se me olvida: llama a tu hermana "
        "cuando tengas un momento. Ve mirando la pantalla, que te lo voy dejando listo.")


class FakeWS:
    def __init__(self):
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.frames: list = []

    async def accept(self):
        pass

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, t):
        self.frames.append(json.loads(t))

    async def send_bytes(self, b):
        pass

    def types(self):
        return [f["type"] for f in self.frames]

    def captions(self):
        return [f for f in self.frames if f["type"] == "assistant_text"]


class FakeDB:
    async def ensure_session(self, sid):
        pass

    async def insert_turn(self, *a, **k):
        pass

    async def fetch_soul_config(self):
        return None


class FakeTTS:
    made: list = []

    def __init__(self):
        self.sent: list[str] = []
        FakeTTS.made.append(self)

    async def connect(self):
        pass

    async def send_text(self, t):
        self.sent.append(t)

    async def end(self):
        pass

    async def close(self):
        pass

    async def audio_chunks(self):
        await asyncio.sleep(0.05)
        return
        yield b""


def bubble_of(frames: list[dict]) -> str:
    """lib/local-voice.ts's `assistant_text` case: same turn accumulates into one bubble."""
    agent_turn, agent_text, bubble = 0, "", ""
    for f in frames:
        if f["type"] != "assistant_text" or not f.get("text"):
            continue
        n = f.get("turn")
        turn = n if isinstance(n, (int, float)) else agent_turn
        if turn != agent_turn:
            agent_turn, agent_text = turn, ""
        agent_text += f["text"]
        bubble = agent_text
    return bubble


def spoken(text: str) -> str:
    f = sse.ForbiddenPhraseFilter()
    return f.feed(text) + f.flush()


@pytest.fixture
def voice_env(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-for-f15")
    config.set_api_key(None)
    FakeTTS.made = []

    async def fake_load_context(request, db, session_id, **kw):
        return [{"role": "user", "content": request.messages[-1]["content"]}]

    async def no_memory(user_text, db):
        return None

    monkeypatch.setattr(vs, "load_context", fake_load_context)
    monkeypatch.setattr(vs, "extract_and_save_memory", no_memory)
    monkeypatch.setattr(vs, "TtsClient", lambda **kw: FakeTTS())
    yield monkeypatch


def run_turn(ws, loop_fn, monkeypatch):
    monkeypatch.setattr(vs, "agentic_loop", loop_fn)

    async def main():
        session = vs.VoiceSession(ws, "f15", db=FakeDB(), soul_patterns={})
        runner = asyncio.create_task(session.run())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "investígame PixiJS"})})
        for _ in range(200):
            if "turn_end" in ws.types():
                break
            await asyncio.sleep(0.05)
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)

    asyncio.run(main())


def doubled_loop():
    async def loop(items, sid, db, queue, patterns, **kw):
        for part in (SAID, SAID):
            await queue.put(part)
            await asyncio.sleep(0.05)
        return SAID + SAID

    return loop


def test_the_announcement_said_twice_reaches_one_bubble_once(voice_env):
    """The whole path: a loop that emits the reply twice must still build one bubble holding it
    once, with nothing of the reply lost off its end."""
    ws = FakeWS()
    run_turn(ws, doubled_loop(), voice_env)

    caps = ws.captions()
    assert caps, "the turn must still caption something"
    assert {c["turn"] for c in caps} == {1}, "every caption belongs to the one turn that ran"
    assert bubble_of(ws.frames).count("Me pongo con eso ahora mismo") == 1, (
        "F-15: the whole reply appeared twice inside a single bubble"
    )
    assert bubble_of(ws.frames).strip().endswith("dejando listo."), "and nothing of it was lost"


def test_she_does_not_say_it_twice_either(voice_env):
    """The caption and the voice come off the same filtered stream — the audio must lose the copy too."""
    ws = FakeWS()
    run_turn(ws, doubled_loop(), voice_env)

    said = "".join(t for client in FakeTTS.made for t in client.sent)
    assert said.count("Me pongo con eso ahora mismo") == 1, "she spoke the announcement twice"


def test_a_two_sentence_block_repeated_collapses():
    """Two sentences said, then said again in the same order: the second pass is dropped whole."""
    assert spoken(SAID + SAID).count("Ve mirando la pantalla") == 1


def test_a_filler_between_the_copies_does_not_hide_the_stutter():
    """The shape seen live: a short filler ("Mmm...") opens both copies. An ack that brief must not
    break the match."""
    one = "Mmm... me pongo con eso a fondo; puedes ver el avance en la pantalla."
    assert spoken(one + " " + one).count("me pongo con eso a fondo") == 1


def test_a_lone_repeat_is_released_in_order_with_what_follows():
    """A repeat that does NOT continue the block is deliberate emphasis: it is held, then released
    BEFORE the sentence that broke the match — order is what the hold could most easily lose."""
    out = spoken("Abro la página ahora mismo. Te muestro lo que encuentre. "
                 "Abro la página ahora mismo. Dime si prefieres otra cosa.")
    assert out.count("Abro la página ahora mismo") == 2
    assert out.index("Dime si prefieres otra cosa") > out.rindex("Abro la página ahora mismo")


def test_three_copies_collapse_to_one():
    """Collapsing is not a one-shot: a third copy is dropped as well as the second."""
    assert spoken(SAID * 3).count("Ve mirando la pantalla") == 1
