"""The tool-call boundary flush: her announcement must be SPOKEN as the tool starts, not after it.

Two stages of the spoken chain hold text that only LATER text can release: a URL filter releases up to
the last whitespace, so a reply ending "…el código." keeps its final token, and the phrase filter then
has no terminator to split on and holds the sentence. Nothing else arrives until the tool has already
run, so the announcement and the result were spoken together, past tense wearing the future. A trailing
space had hidden this by accident until a reasoning model stopped being narrated for.

Every consumer of the queue is driven here: a sentinel is truthy, and a consumer that does not know it
feeds an object to a filter and takes the turn down with it."""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from kotoba.core import events, stream as sse
import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args, cid):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args),
                                 call_id=cid)
    return types.SimpleNamespace(type="response.output_item.done", item=item)


class _FakeStream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _FakeClient:
    def __init__(self, scripts):
        self._s, self._i = scripts, 0

        async def create(**kw):
            evs = self._s[min(self._i, len(self._s) - 1)]
            self._i += 1
            return _FakeStream(evs)

        self.responses = types.SimpleNamespace(create=create)


class _FakeDB:
    async def insert_audit_log(self, **kw):
        pass

    async def list_approved_commands(self):
        return []

    async def save_approved_command(self, pattern, scope="command"):
        pass


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def _register(name):
    async def execute(args, ctx):
        return "ok"

    mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": name}, __name__=f"t.{name}",
                                ANNOUNCE="", HEARTBEAT=[], COMPLETE="", FAIL="", execute=execute)
    reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA, toolset="web", risk="read"))


def _drive(scripts, session):
    q = events.register(session)
    stream: asyncio.Queue = asyncio.Queue()
    ctx = ToolContext(db=_FakeDB(), session_id=session, client=None, mode="companion")
    ctx.approval = None

    async def go():
        await loop._run_iterations(
            _FakeClient(scripts), ctx, [{"role": "user", "content": "x"}], stream, {},
            max_iterations=5, mode="companion", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )
        out = []
        while not stream.empty():
            out.append(stream.get_nowait())
        return out

    out = asyncio.run(go())
    events.unregister(session, q)
    return out


def _chain():
    return (sse.ToolCallLeakFilter(), sse.CodeFenceFilter(), sse.UrlFilter(),
            sse.AudioTagFilter(), sse.ForbiddenPhraseFilter())


def _spoken(items):
    """The voice consumer's chain, in emission order — one entry per moment she could be heard."""
    fs = _chain()
    leak, code, url, tag, phrase = fs
    out = []
    for it in items:
        if it is sse.FLUSH_SENTINEL:
            held = sse.flush_spoken(*fs, final=False)
            if held:
                out.append(held)
            continue
        if not it:
            continue
        got = phrase.feed(tag.feed(url.feed(code.feed(leak.feed(it)))))
        if got:
            out.append(got)
    tail = sse.flush_spoken(*fs, final=True)
    if tail:
        out.append(tail)
    return out


ANNOUNCE_THEN_TOOL = [
    [_ev_text("[curious] Vale, ejecutaré el código."), _ev_call("flush_probe", {}, "c1")],
    [_ev_text(" [happily] Ya lo hice, el resultado es hola.")],
]


def test_the_announcement_leaves_the_chain_before_the_tool_result(clean_registry):
    """The defect, as a wire fact: the announcement is its own utterance, complete, and it is the
    FIRST one — not a prefix of the sentence that reports the result."""
    _register("flush_probe")
    said = _spoken(_drive(ANNOUNCE_THEN_TOOL, "flush_1"))
    assert said[0].rstrip() == "[curious] Vale, ejecutaré el código."
    assert "Ya lo hice" not in said[0]
    assert any("Ya lo hice" in s for s in said[1:])


def test_without_the_flush_nothing_can_be_said_until_the_tool_is_over(clean_registry):
    """The regression witness, stated the way the defect actually works: it is a TIMING failure, not
    a fusion. Feed the chain everything the first iteration produced — all of it, to the last byte —
    and it emits NOT ONE CHARACTER. The announcement only came out when the NEXT iteration's text
    arrived, which is after the tool ran; both sentences then reached TTS inside one batching window
    and were synthesized as a single eleven_v3 request, which is the "in one breath" heard live.

    The sentinel is what makes the first line sayable on its own, so this is the assertion that must
    keep failing to hold: if the chain ever stops holding, the flush stops being load-bearing."""
    _register("flush_probe")
    items = _drive(ANNOUNCE_THEN_TOOL, "flush_2")
    upto_tool = items[:items.index(sse.FLUSH_SENTINEL)]
    fs = _chain()
    leak, code, url, tag, phrase = fs
    emitted = "".join(phrase.feed(tag.feed(url.feed(code.feed(leak.feed(i))))) for i in upto_tool)
    assert emitted == ""
    assert sse.flush_spoken(*fs, final=False).rstrip() == "[curious] Vale, ejecutaré el código."


def test_one_flush_per_tool_iteration_and_none_after_the_last_text(clean_registry):
    _register("flush_probe")
    items = _drive([
        [_ev_text("Primero esto."), _ev_call("flush_probe", {}, "c1")],
        [_ev_text(" Ahora lo otro."), _ev_call("flush_probe", {}, "c2")],
        [_ev_text(" Y ya terminé.")],
    ], "flush_3")
    assert sum(1 for i in items if i is sse.FLUSH_SENTINEL) == 2
    assert items[-1] is not sse.FLUSH_SENTINEL
    assert [s.strip() for s in _spoken(items)] == ["Primero esto.", "Ahora lo otro.", "Y ya terminé."]


def test_two_tools_in_one_iteration_flush_once_and_lose_nothing(clean_registry):
    _register("flush_probe")
    _register("flush_probe2")
    items = _drive([
        [_ev_text("Voy a hacer dos cosas."), _ev_call("flush_probe", {}, "c1"),
         _ev_call("flush_probe2", {}, "c2")],
        [_ev_text(" Las dos hechas.")],
    ], "flush_4")
    assert sum(1 for i in items if i is sse.FLUSH_SENTINEL) == 1
    assert [s.strip() for s in _spoken(items)] == ["Voy a hacer dos cosas.", "Las dos hechas."]


def test_a_turn_with_no_tool_is_untouched(clean_registry):
    items = _drive([[_ev_text("[happily] Hola, qué tal.")]], "flush_5")
    assert not any(i is sse.FLUSH_SENTINEL for i in items)
    assert _spoken(items) == ["[happily] Hola, qué tal."]


def test_the_cli_drain_survives_the_sentinel(clean_registry):
    """A sentinel is TRUTHY: the CLI's `if chunk:` would hand an object to ToolCallLeakFilter and
    die of a TypeError. Driven through the real Session so the whole drain runs."""
    import kotoba.cli.session as cli_session

    _register("flush_probe")

    async def go():
        session = await cli_session.Session.open()
        session.engine  # noqa: B018 — force the engine up before we swap the client
        loop_client = _FakeClient(ANNOUNCE_THEN_TOOL)
        orig = loop.get_client
        loop.get_client = lambda: loop_client
        try:
            reply = await session.ask("¿lo hiciste?")
        finally:
            loop.get_client = orig
            await session.close()
        return reply

    reply = asyncio.run(go())
    assert "Vale, ejecutaré el código." in reply
    assert "Ya lo hice" in reply


def test_the_v1_wire_speaks_the_announcement_before_the_result(clean_registry, tmp_path, monkeypatch):
    """The ElevenLabs custom-LLM path has the same chain and the same defect. Driven through the real
    endpoint, the announcement must arrive as its own content chunk, ahead of the result's."""
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    _register("flush_probe")
    saved_cache, saved_disabled = dict(reg._check_cache), set(reg._disabled_toolsets)
    from fastapi.testclient import TestClient

    import kotoba.server as server

    orig = loop.get_client
    loop.get_client = lambda: _FakeClient(ANNOUNCE_THEN_TOOL)
    try:
        with TestClient(server.app) as c:
            r = c.post("/v1/chat/completions", headers={"Authorization": "Bearer k"}, json={
                "messages": [{"role": "user", "content": "hazlo"}],
                "session_id": "flush-v1", "stream": True,
            })
        assert r.status_code == 200
        chunks = []
        for line in r.text.splitlines():
            if line.startswith("data:") and "[DONE]" not in line:
                try:
                    d = json.loads(line[5:].strip())["choices"][0]["delta"]
                except Exception:
                    continue
                if d.get("content"):
                    chunks.append(d["content"])
    finally:
        loop.get_client = orig
        reg._check_cache.clear(); reg._check_cache.update(saved_cache)
        reg._disabled_toolsets.clear(); reg._disabled_toolsets.update(saved_disabled)
    spoken = [c for c in chunks if c.strip() and c.strip() != "..."]
    assert spoken, chunks
    assert "Vale, ejecutaré el código." in spoken[0]
    assert "Ya lo hice" not in spoken[0]


def test_the_boundary_does_not_glue_her_sentence_to_what_follows_it():
    """Live QA read back "…qué sale.Mmm...". Her announcement comes from the model's own deltas, which
    end where the sentence ends, and narrate() carries its space AFTER the phrase, not before — so the
    filler landed against the full stop. The audio survived (separate TTS requests); the caption is one
    string, and that is what the user reads."""
    fs = _chain()
    leak, code, url, tag, phrase = fs
    phrase.feed(tag.feed(url.feed(code.feed(leak.feed("Lo ejecuto y te digo qué sale.")))))
    announcement = sse.flush_spoken(*fs, final=False)
    filler = phrase.feed(tag.feed(url.feed(code.feed(leak.feed("Mmm... ")))))
    assert announcement.endswith(" ")
    assert "sale. Mmm" in (announcement + filler)
    assert "sale.Mmm" not in (announcement + filler)


def test_the_final_flush_adds_no_trailing_space():
    """Only the seam does. The end of a turn has nothing to be glued to, and a trailing space there
    would be a byte on the wire that no earlier version sent."""
    fs = _chain()
    leak, code, url, tag, phrase = fs
    phrase.feed(tag.feed(url.feed(code.feed(leak.feed("Ya está.")))))
    assert sse.flush_spoken(*fs, final=True) == "Ya está."


def test_every_filter_is_re_feedable_after_a_boundary_flush():
    """A mid-turn flush is not an end of stream: each stage must keep working afterwards, or the rest
    of the turn goes silent."""
    fs = _chain()
    leak, code, url, tag, phrase = fs
    phrase.feed(tag.feed(url.feed(code.feed(leak.feed("Uno. ")))))
    sse.flush_spoken(*fs, final=False)
    got = phrase.feed(tag.feed(url.feed(code.feed(leak.feed("Dos. Tres. ")))))
    assert got == "Dos. Tres."
    assert sse.flush_spoken(*fs, final=True).strip() == ""


def test_the_stutter_guard_still_collapses_a_block_repeated_across_the_boundary():
    """She writes a line, calls a tool, is told by the result to say it, and writes it again.
    RepeatCollapse answers that by remembering what was said — the flush must clear what is PENDING,
    never that memory."""
    fs = _chain()
    leak, code, url, tag, phrase = fs
    first = phrase.feed(tag.feed(url.feed(code.feed(leak.feed(
        "Vale, me pongo con ello. Te aviso al terminar.")))))
    first += sse.flush_spoken(*fs, final=False)
    again = phrase.feed(tag.feed(url.feed(code.feed(leak.feed(
        " Vale, me pongo con ello. Te aviso al terminar.")))))
    again += sse.flush_spoken(*fs, final=True)
    assert first.strip() == "Vale, me pongo con ello. Te aviso al terminar."
    assert again.strip() == ""


def test_an_open_code_fence_survives_the_boundary_unspoken():
    """The fence filter is the one stage the seam does NOT flush: its flush clears `_in_fence`, and
    the rest of the block would then be read aloud."""
    fs = _chain()
    leak, code, url, tag, phrase = fs
    out = phrase.feed(tag.feed(url.feed(code.feed(leak.feed(
        "[curious] Mira esto: ```python\nprint('secreto')")))))
    out += sse.flush_spoken(*fs, final=False)
    out += phrase.feed(tag.feed(url.feed(code.feed(leak.feed("\nprint('mas')\n``` listo.")))))
    out += sse.flush_spoken(*fs, final=True)
    assert "print" not in out
    assert out.strip() == "[curious] Mira esto:  listo."


def test_an_unclosed_aside_around_a_link_is_not_half_spoken_at_the_boundary():
    """UrlFilter holds a "(label: url" aside whole so it drops as ONE aside. Flushing it at the seam
    speaks the orphan "(Reuters:" — so the seam flush is `partial` and keeps that one hold."""
    fs = _chain()
    leak, code, url, tag, phrase = fs
    out = phrase.feed(tag.feed(url.feed(code.feed(leak.feed(
        "Lo saqué de (Reuters: https://ejemplo.com/x")))))
    out += sse.flush_spoken(*fs, final=False)
    out += phrase.feed(tag.feed(url.feed(code.feed(leak.feed(" y lo confirmé aparte).")))))
    out += sse.flush_spoken(*fs, final=True)
    assert "Reuters" not in out and "http" not in out
    assert out.strip() == "Lo saqué de ."


def test_a_half_arrived_tool_call_leak_is_not_spoken_at_the_boundary():
    """The leak regex is anchored on the recipient, so an emission whose `to=functions.…` has not
    been written yet matches nothing and the JSON would be read aloud. The seam keeps that hold."""
    fs = _chain()
    leak, code, url, tag, phrase = fs
    out = phrase.feed(tag.feed(url.feed(code.feed(leak.feed('Claro {"query":"tokio"}')))))
    out += sse.flush_spoken(*fs, final=False)
    out += phrase.feed(tag.feed(url.feed(code.feed(leak.feed(
        'to=functions.web_search<|call|> ya lo busco.')))))
    out += sse.flush_spoken(*fs, final=True)
    assert "query" not in out and "functions" not in out
    assert " ".join(out.split()) == "Claro ya lo busco."


def test_a_url_at_the_end_of_the_pre_tool_message_is_still_never_spoken():
    fs = _chain()
    leak, code, url, tag, phrase = fs
    out = phrase.feed(tag.feed(url.feed(code.feed(leak.feed(
        "Lo encontré en https://ejemplo.com/ruta")))))
    out += sse.flush_spoken(*fs, final=False)
    out += phrase.feed(tag.feed(url.feed(code.feed(leak.feed(" Y ya está listo.")))))
    out += sse.flush_spoken(*fs, final=True)
    assert "http" not in out and "ejemplo.com" not in out
    assert "su web oficial" in out


def test_a_queue_nobody_drains_neither_wedges_nor_grows_unbounded(clean_registry):
    """work_runner and delegate hand the loop a throwaway queue. Putting a sentinel on an unbounded
    queue can never block, and it must cost one slot per tool iteration — not one per chunk."""
    _register("flush_probe")
    q: asyncio.Queue = asyncio.Queue()
    ctx = ToolContext(db=_FakeDB(), session_id="flush_6", client=None, mode="companion")
    ctx.approval = None
    ev = events.register("flush_6")

    async def go():
        return await asyncio.wait_for(loop._run_iterations(
            _FakeClient(ANNOUNCE_THEN_TOOL), ctx, [{"role": "user", "content": "x"}], q, {},
            max_iterations=5, mode="companion", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        ), timeout=10)

    text = asyncio.run(go())
    events.unregister("flush_6", ev)
    assert text.strip()
    assert sum(1 for i in q._queue if i is sse.FLUSH_SENTINEL) == 1
