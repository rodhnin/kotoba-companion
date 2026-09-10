"""The seam between model iterations — and the proof that no voice caller gets one.

A turn with tools streams its text in bursts, one per iteration, and the model never writes anything at
the boundary: the loop is the only place that knows where one burst ends. Without a seam the CLI's
paragraph renderer received them glued ('…te lo debía.Ya lo encontré…' — the owner's screenshot). Voice
is different on purpose: the TTS segments sentences and narrate() spaces the canned phrases, so every
voice caller leaves `seam` empty and the default-off contract below is what keeps the /v1 wire
byte-identical.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from kotoba.core import events
from kotoba.core import stream
import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args, cid):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=cid)
    return types.SimpleNamespace(type="response.output_item.done", item=item)


class _FakeStream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _FakeResponses:
    def __init__(self, scripts):
        self._scripts = scripts
        self._i = 0

    async def create(self, **kw):
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _FakeStream(evs)


class _FakeClient:
    def __init__(self, scripts):
        self.responses = _FakeResponses(scripts)


class _FakeDB:
    async def insert_audit_log(self, **kw):
        pass

    async def list_approved_commands(self):
        return []

    async def save_approved_command(self, pattern, scope="command"):
        pass


def _register_quiet_tool(name):
    async def execute(args, ctx):
        return "ok"
    mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": name}, __name__=f"tools.x.{name}",
                                ANNOUNCE="", HEARTBEAT=[], COMPLETE="", FAIL="", execute=execute)
    reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA, toolset="web", risk="read"))


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


BURSTS = [
    [_ev_text("Perdona el retraso, te lo debía."), _ev_call("quiet_search", {"q": "a"}, "c1")],
    [_ev_text("Ya lo encontré, qué alivio."), _ev_call("quiet_search", {"q": "b"}, "c2")],
    [_ev_text("Mmm, hay una pequeña travesura en el paso dos.")],
]


def _chunks(scripts, session, **kw):
    q = events.register(session)
    stream: asyncio.Queue = asyncio.Queue()
    ctx = ToolContext(db=_FakeDB(), session_id=session, client=None, mode="companion")
    ctx.approval = None

    async def go():
        await loop._run_iterations(
            _FakeClient(scripts), ctx, [{"role": "user", "content": "x"}], stream, {},
            max_iterations=5, mode="companion", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None, **kw,
        )
        out = []
        while not stream.empty():
            out.append(stream.get_nowait())
        return out

    out = asyncio.run(go())
    events.unregister(session, q)
    return out


def _text(chunks):
    """What actually reaches a wire. FLUSH_SENTINEL is an object, not bytes: it tells a consumer to
    let go of what its filters hold and is never forwarded anywhere."""
    return [c for c in chunks if isinstance(c, str)]


def test_the_seam_lands_between_bursts_and_nowhere_else(clean_registry):
    _register_quiet_tool("quiet_search")
    chunks = _chunks(BURSTS, "seam_on", seam="\n\n")
    assert _text(chunks) == ["Perdona el retraso, te lo debía.", "\n\n",
                             "Ya lo encontré, qué alivio.", "\n\n",
                             "Mmm, hay una pequeña travesura en el paso dos."]


def test_no_caller_that_omits_seam_sees_a_single_new_byte(clean_registry):
    """The voice-wire pin: every caller that omits `seam` leaves it at its default, so the queue they
    drain must carry exactly what it carried before the seam existed.

    The pin stands and its assertion changed, because its old reasoning did not: "voice is different
    on purpose — the TTS segments sentences" is false when the spoken chain emits nothing to segment.
    UrlFilter holds the token carrying the final '.', ForbiddenPhraseFilter then has no terminator,
    and her whole announcement waited for the NEXT iteration's text, i.e. until after the tool. The
    boundary now carries a sentinel OBJECT for every caller — zero new bytes, which is what this test
    was written to protect."""
    _register_quiet_tool("quiet_search")
    chunks = _chunks(BURSTS, "seam_off")
    assert _text(chunks) == ["Perdona el retraso, te lo debía.",
                             "Ya lo encontré, qué alivio.",
                             "Mmm, hay una pequeña travesura en el paso dos."]


def test_every_tool_boundary_carries_exactly_one_flush(clean_registry):
    """One per ITERATION that called tools, in the right place: after that burst's text, before the
    next one — never two in a row, and never after the final text (DONE closes that)."""
    _register_quiet_tool("quiet_search")
    chunks = _chunks(BURSTS, "seam_flush")
    marks = [i for i, c in enumerate(chunks) if c is stream.FLUSH_SENTINEL]
    assert len(marks) == 2, chunks
    assert chunks[-1] != stream.FLUSH_SENTINEL, chunks
    for i in marks:
        assert isinstance(chunks[i - 1], str) and chunks[i - 1].strip(), chunks


def test_a_burst_with_no_text_earns_no_seam(clean_registry):
    _register_quiet_tool("quiet_search")
    silent_first = [
        [_ev_call("quiet_search", {"q": "a"}, "c1")],
        [_ev_text("Listo.")],
    ]
    assert _text(_chunks(silent_first, "seam_quiet", seam="\n\n")) == ["Listo."]


def test_the_cli_turn_arrives_in_paragraphs_not_glued(clean_registry, monkeypatch):
    """End to end: the REAL agentic_loop behind the REAL Session drain chain — the exact path the
    owner's glued screenshot came down."""
    import kotoba.cli.session as cli_session

    _register_quiet_tool("quiet_search")
    monkeypatch.setattr(loop, "get_client", lambda: _FakeClient(BURSTS))

    async def go():
        session = await cli_session.Session.open()
        reply = await session.ask("¿lo encontraste?")
        async with session.engine.db.conn.execute(
            "SELECT content FROM turns WHERE role='assistant'"
        ) as cur:
            rows = [r["content"] for r in await cur.fetchall()]
        await session.close()
        return reply, rows

    reply, rows = asyncio.run(go())
    assert "debía.Ya" not in reply and "alivio.Mmm" not in reply, reply
    assert reply.count("\n\n") == 2, reply
    assert rows == [reply], "the transcript must hold the same paragraphs the screen showed"
