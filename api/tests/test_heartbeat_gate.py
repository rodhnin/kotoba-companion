"""Who gets the canned "still working" phrases, and when the first one lands.

They exist for one reason: a voice must not go silent while a tool runs. A reasoning model narrates
its own tool use, so they were switched off for one wholesale — on the reasoning that ElevenLabs'
soft_timeout filler covers the gap. It does, in AGENT voice mode. In LOCAL mode there is no EL agent
and nothing fills anything — and local IS the daily configuration here: measured end to end, a 10s tool
produced zero audio for 10.09 seconds. So the suppression is narrowed, not removed, and `register`
keeps the canned English out of a terminal that draws its own spinner.
"""
from __future__ import annotations

import asyncio
import re
import types

import pytest

import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.tools.registry import ToolSpec

PATTERNS = {"probe": {"before": "b", "heartbeat": ["Still running...", "Almost done..."],
                      "after": "a", "fail": "f"}}


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def _register(name, seconds):
    async def execute(args, ctx):
        await asyncio.sleep(seconds)
        return "ok"

    mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": name}, __name__=f"t.{name}",
                                execute=execute, TIMEOUT=600)
    reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA, toolset="web", risk="read"))


@pytest.mark.parametrize("reasoning,voice_mode,register,expected", [
    (True, "local", "voice", True),    # the live config: nothing else fills the silence
    (True, "agent", "voice", False),   # EL's soft_timeout filler does
    (True, "local", "text", False),    # the CLI draws its own spinner
    (True, "agent", "text", False),
    (False, "local", "voice", True),   # unchanged: she does not narrate her own tool use
    (False, "agent", "voice", True),
    (False, "local", "text", True),
    (False, "agent", "text", True),
])
def test_who_gets_a_heartbeat(monkeypatch, reasoning, voice_mode, register, expected):
    monkeypatch.setenv("KOTOBA_VOICE_MODE", voice_mode)
    monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None: reasoning)
    lines = loop._heartbeat_lines("probe", PATTERNS, register, "companion")
    assert bool(lines) is expected


def test_the_gate_reads_the_role_that_is_actually_answering(monkeypatch):
    """`role` was omitted here while the narration gate two frames up passed one, so a delegate on a
    non-reasoning code model was judged by the companion model."""
    seen = []
    monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None: seen.append(role) or False)
    loop._heartbeat_lines("probe", PATTERNS, "voice", "code")
    assert seen == ["code"]


class _Ctx:
    """What the loop puts on the real context before it runs a tool."""

    def __init__(self, register="voice", model_role="companion", mode="companion"):
        self.register = register
        self.model_role = model_role
        self.channel = "voice"
        self.call_id = ""
        self.mode = mode


def test_a_tool_outlasting_the_first_beat_does_not_run_in_silence(clean_registry, monkeypatch):
    """The cadence itself is taste, not protocol (see execute_with_heartbeat), so this pins the
    behaviour rather than the number: once _HEARTBEAT_FIRST has passed, something wordless is heard,
    and it is the first entry of the set. The constant is monkeypatched so the test costs 4s, not 10."""
    monkeypatch.setenv("KOTOBA_VOICE_MODE", "local")
    monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None: True)
    monkeypatch.setattr(loop, "_HEARTBEAT_FIRST", 3.0)
    _register("probe", 4.0)
    q: asyncio.Queue = asyncio.Queue()

    async def go():
        await loop.execute_with_heartbeat("probe", {}, q, PATTERNS, _Ctx())

    asyncio.run(go())
    assert not q.empty(), "a 4s tool must not run in silence on the local voice path"
    assert q.get_nowait().strip() == loop._NEUTRAL_FILLER[0]


def test_what_fills_the_silence_carries_no_language(monkeypatch):
    """The acceptance test for this change. Live QA heard "Working on it... Still running... Almost
    there..." in the middle of a Spanish conversation — canned English, which is the very thing
    is_reasoning_model gives as its reason for suppressing this layer."""
    monkeypatch.setenv("KOTOBA_VOICE_MODE", "local")
    monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None: True)
    lines = loop._heartbeat_lines("probe", PATTERNS, "voice", "companion")
    assert lines
    for line in lines:
        assert re.fullmatch(r"[MmHh]+\.\.\.", line), line


def test_the_filler_ends_in_an_ascii_terminator_or_it_is_never_heard(monkeypatch):
    """A mechanical trap, not a style note: core.stream splits sentences on [.!?\\n], so a filler
    ending in a real "…" sits inside ForbiddenPhraseFilter until LATER text releases it — which
    during a tool run is after the tool, i.e. exactly the silence this exists to fill."""
    from kotoba.core import stream as sse

    monkeypatch.setenv("KOTOBA_VOICE_MODE", "local")
    monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None: True)
    for line in loop._heartbeat_lines("probe", PATTERNS, "voice", "companion"):
        chain = (sse.ToolCallLeakFilter(), sse.CodeFenceFilter(), sse.UrlFilter(),
                 sse.AudioTagFilter(), sse.ForbiddenPhraseFilter())
        leak, code, url, tag, phrase = chain
        out = phrase.feed(tag.feed(url.feed(code.feed(leak.feed(line + " ")))))
        assert out.strip() == line, f"{line!r} was held by the spoken chain: {out!r}"


def test_a_non_reasoning_model_still_gets_its_words(clean_registry, monkeypatch):
    """The other half of the decision: there the phrases are the WHOLE tool narration, not an
    intrusion into hers, so replacing them with hums would remove information."""
    monkeypatch.setenv("KOTOBA_VOICE_MODE", "local")
    monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None: False)
    assert loop._heartbeat_lines("probe", PATTERNS, "voice", "companion") == PATTERNS["probe"]["heartbeat"]


def test_a_reasoning_turn_in_agent_mode_stays_silent(clean_registry, monkeypatch):
    """The half that must NOT change: with an EL agent filling the gap, the canned English would be
    a duplicate of what she already says herself."""
    monkeypatch.setenv("KOTOBA_VOICE_MODE", "agent")
    monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None: True)
    _register("probe", 4.0)
    q: asyncio.Queue = asyncio.Queue()

    async def go():
        await loop.execute_with_heartbeat("probe", {}, q, PATTERNS, _Ctx())

    asyncio.run(go())
    assert q.empty()


def test_a_context_with_no_register_at_all_behaves_like_voice(clean_registry, monkeypatch):
    """execute_with_heartbeat is called with ctx=None in tests and by callers that predate the axis."""
    monkeypatch.setenv("KOTOBA_VOICE_MODE", "local")
    monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None: True)
    monkeypatch.setattr(loop, "_HEARTBEAT_FIRST", 3.0)
    _register("probe", 3.5)
    q: asyncio.Queue = asyncio.Queue()

    async def go():
        await loop.execute_with_heartbeat("probe", {}, q, PATTERNS, None)

    asyncio.run(go())
    assert not q.empty()


def _heard(pieces):
    """What reaches TTS, in order, through the real spoken chain."""
    from kotoba.core import stream as sse

    fs = (sse.ToolCallLeakFilter(), sse.CodeFenceFilter(), sse.UrlFilter(),
          sse.AudioTagFilter(), sse.ForbiddenPhraseFilter())
    leak, code, url, tag, phrase = fs
    out = []
    for p in pieces:
        got = (sse.flush_spoken(*fs, final=False) if p is sse.FLUSH_SENTINEL
               else phrase.feed(tag.feed(url.feed(code.feed(leak.feed(p))))))
        if got.strip():
            out.append(got.strip())
    tail = sse.flush_spoken(*fs, final=True)
    if tail.strip():
        out.append(tail.strip())
    return out


_ANNOUNCE = "[curious] Vale, lo ejecuto y te digo."
_RESULT = " [happily] Listo, salió terminado a los doce."


def test_a_repeated_hum_is_never_swallowed_or_the_silence_comes_back():
    """The filler repeats — the rotation ends by saying its last phrase again — and RepeatCollapse
    drops contiguous replays. If it ate them, a long tool would go quiet again and nothing would say
    so."""
    from kotoba.core import stream as sse

    heard = _heard([_ANNOUNCE, sse.FLUSH_SENTINEL,
                    "Mmm... ", "Hmm... ", "Hmm... ", "Hmm... ", "Hmm... ", _RESULT])
    assert sum(s.count("Hmm") for s in heard) == 4


def test_a_hum_between_the_two_copies_does_not_blind_the_stutter_guard():
    """The stutter guard, with a heartbeat in it. It keys on a CONTIGUOUS replay of the tail, so one
    hum landing between her announcement and its repeat was enough to hide the stutter — measured, the
    announcement was spoken twice. A wordless sound is transparent to the guard for that reason."""
    from kotoba.core import stream as sse

    heard = _heard([_ANNOUNCE, sse.FLUSH_SENTINEL, "Mmm... ", _ANNOUNCE, _RESULT])
    assert sum(s.count("lo ejecuto y te digo") for s in heard) == 1

    block = "Vale, me pongo con ello. Te aviso al terminar."
    heard = _heard([block, sse.FLUSH_SENTINEL, "Mmm... ", " " + block, " Ya está."])
    assert sum(s.count("me pongo con ello") for s in heard) == 1


def test_every_entry_in_the_set_keeps_all_three_invariants():
    """Whatever the set becomes, each entry must: carry no language, reach TTS the moment it is fed
    (the ASCII-terminator trap), and stay transparent to the repeat guard. A new sound that misses one
    of these fails quietly — as silence, as English, or as a stutter nobody catches."""
    from kotoba.core import stream as sse

    assert len(set(loop._NEUTRAL_FILLER)) >= 3, "too few shapes — the caller repeats the last one"
    for filler in loop._NEUTRAL_FILLER:
        assert re.fullmatch(r"[MmHh]+\.\.\.", filler), filler
        chain = (sse.ToolCallLeakFilter(), sse.CodeFenceFilter(), sse.UrlFilter(),
                 sse.AudioTagFilter(), sse.ForbiddenPhraseFilter())
        leak, code, url, tag, phrase = chain
        assert phrase.feed(tag.feed(url.feed(code.feed(leak.feed(filler + " "))))).strip() == filler
    collapse = sse.RepeatCollapse()
    collapse.take("Vale, lo ejecuto y te digo. ")
    for filler in loop._NEUTRAL_FILLER * 2:
        collapse.take(filler + " ")
    assert collapse.take("Vale, lo ejecuto y te digo. ").strip() == ""


def test_a_hum_is_transparent_but_never_silent():
    """Transparent to the repeat guard, still spoken — the two are not the same thing."""
    from kotoba.core import stream as sse

    heard = _heard([sse.FLUSH_SENTINEL, "Mmm... ", "Hmm... ", "Hmm... ", _RESULT])
    assert [s for s in heard if "mm" in s.lower()] == ["Mmm...", "Hmm...", "Hmm..."]


def _register_mcp(name, seconds):
    """A tool exactly as an MCP server's arrives: `toolset` "mcp:<server>", no SOUL entry, and the
    voice defaults _MCPProxy carries for every server in the world."""
    from kotoba.core.mcp.client import _MCPProxy

    async def execute(args, ctx):
        await asyncio.sleep(seconds)
        return "ok"

    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": name}, __name__=f"t.{name}", execute=execute, TIMEOUT=600,
        ANNOUNCE=_MCPProxy.ANNOUNCE, HEARTBEAT=list(_MCPProxy.HEARTBEAT),
        COMPLETE=_MCPProxy.COMPLETE, FAIL=_MCPProxy.FAIL,
    )
    reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA, toolset="mcp:notion", risk="read"))


def _drain(q: asyncio.Queue) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_an_mcp_tool_does_not_speak_english_the_narration_gate_already_suppressed(clean_registry,
                                                                                 monkeypatch):
    """`suppress_narration = is_mcp or is_reasoning_model(...)` silences an MCP tool's before/after
    on EVERY model — but is_mcp never reached this gate, so on a NON-reasoning model (the fresh-clone
    default, since .env.example ships KOTOBA_REASONING_EFFORT commented out) the heartbeat went on
    speaking _MCPProxy's "Working on it..." into whatever language the conversation was in."""
    monkeypatch.setenv("KOTOBA_VOICE_MODE", "local")
    monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None: False)
    monkeypatch.setattr(loop, "_HEARTBEAT_FIRST", 3.0)
    _register_mcp("notion__search", 4.0)
    q: asyncio.Queue = asyncio.Queue()

    async def go():
        # An MCP server's tools belong to a background job and are offered nowhere else, so a
        # conversation is not where this hum can happen.
        await loop.execute_with_heartbeat("notion__search", {}, q, {}, _Ctx(mode="work"))

    asyncio.run(go())
    heard = _heard(_drain(q))
    assert heard == [loop._NEUTRAL_FILLER[0]], f"reached TTS: {heard!r}"


def test_an_mcp_tool_is_judged_by_the_same_axis_as_its_before_and_after(monkeypatch):
    """The gate itself, both halves of the model axis. A hum, never nothing: the before/after are
    suppressed here precisely because the canned phrases are wrong for MCP, which leaves the heartbeat
    as the only thing that can still show she is alive — so it must carry no language rather than go
    away."""
    monkeypatch.setenv("KOTOBA_VOICE_MODE", "local")
    for reasoning in (True, False):
        monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None, r=reasoning: r)
        assert loop._heartbeat_lines("m", PATTERNS, "voice", "companion", is_mcp=True) == \
            list(loop._NEUTRAL_FILLER)


def test_an_mcp_tool_in_a_terminal_still_gets_nothing(monkeypatch):
    """The register half is unchanged by MCP: a CLI draws its own spinner, so canned anything is noise
    there whatever the tool came from."""
    monkeypatch.setenv("KOTOBA_VOICE_MODE", "local")
    monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None: False)
    assert loop._heartbeat_lines("m", PATTERNS, "text", "companion", is_mcp=True) == []


def test_the_axis_is_read_off_the_toolset_in_one_place():
    """The loop computed `is_mcp` for the narration gate and execute_with_heartbeat computed nothing —
    that split IS the defect. One helper, read off the spec both frames already resolve."""
    assert loop._is_mcp(ToolSpec(name="a", module=None, schema={}, toolset="mcp:notion"))
    assert not loop._is_mcp(ToolSpec(name="b", module=None, schema={}, toolset="web"))
    assert not loop._is_mcp(None)


def test_the_loop_puts_the_register_on_the_context(clean_registry):
    """The seam itself: _run_iterations is where the axis is known, so that is where it is recorded."""
    import types

    from kotoba.tools import ToolContext

    ctx = ToolContext(db=None, session_id="hb", client=None, mode="companion")
    ctx.approval = None

    class _Empty:
        def __aiter__(self):
            async def gen():
                if False:
                    yield None
            return gen()

    async def create(**kw):
        return _Empty()

    client = types.SimpleNamespace(responses=types.SimpleNamespace(create=create))
    asyncio.run(loop._run_iterations(
        client, ctx, [{"role": "user", "content": "x"}], asyncio.Queue(), {},
        max_iterations=1, mode="companion", allow_risk={"read"}, toolset_filter=None,
        register="text", model_role="code",
    ))
    assert ctx.register == "text"
    assert ctx.model_role == "code"
