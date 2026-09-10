"""A built-in web search says NOTHING to the user, and the code must stop claiming otherwise.

Every narration call in the loop — announce, heartbeat, complete, fail — only fires for a
`function_call`. OpenAI's built-in search instead streams a `web_search_call` item, counted,
carded and logged but never reaching that branch, so all four voice phases were unreachable: a
card appears on screen beside a voice that says nothing.

Routing it into the function_call branch was rejected: `suppress_narration` mutes the canned
lines on every reasoning model this project ships with, so that path would still speak nothing.
"""
from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.core import events
from kotoba.core.voice_patterns import build_soul_patterns
from kotoba.tools import ToolContext
from kotoba.tools.builtin import web_search
from kotoba.tools.registry import ToolSpec

SOUL_PATTERNS = {"web_search": {"before": "Let me look that up.",
                                "heartbeat": ["Going through the results..."],
                                "after": "Got it! So here's what I found:",
                                "fail": "That search didn't go through."}}


class _FakeStream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _Responses:
    def __init__(self, scripts):
        self._scripts, self._i = scripts, 0

    async def create(self, **kw):
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _FakeStream(evs)


class _FakeClient:
    def __init__(self, scripts):
        self.responses = _Responses(scripts)


class _FakeDB:
    async def insert_audit_log(self, **kw):
        pass


def _ws_item(i):
    item = types.SimpleNamespace(type="web_search_call", id=f"ws{i}",
                                 action=types.SimpleNamespace(type="search", query="df -k"))
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _call_item(name, i):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps({}),
                                 call_id=f"c{i}")
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


@pytest.fixture(autouse=True)
def _narration_fully_on(monkeypatch):
    """Canned narration is suppressed on a reasoning model, and whether the process is configured for
    one depends on settings another test may have moved. Pinning it OFF is also what makes the
    reproduction mean something: with narration maximally enabled, a built-in search is STILL silent."""
    monkeypatch.setattr(loop, "is_reasoning_model", lambda *a, **k: False)


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def _register_twin():
    """A function tool wearing web_search's voice, so the two paths differ only in item type."""
    async def execute(args, ctx):
        return "7 results"

    mod = types.SimpleNamespace(
        SCHEMA={"type": "function", "name": "search_twin"},
        __name__="kotoba.tools.x.search_twin",
        ANNOUNCE="Let me look that up.", HEARTBEAT=[], COMPLETE="Got it! So here's what I found:",
        FAIL="That search didn't go through.", execute=execute,
    )
    reg.register(ToolSpec(name="search_twin", module=mod, schema=mod.SCHEMA, toolset="web", risk="read"))


def _spoken(scripts, sess, patterns) -> str:
    events.register(sess)
    ctx = ToolContext(db=_FakeDB(), session_id=sess, client=None, mode="work")
    ctx.approval = None
    q: asyncio.Queue = asyncio.Queue()

    async def go():
        await loop._run_iterations(
            _FakeClient(scripts), ctx, [{"role": "user", "content": "run df -k"}], q, patterns,
            max_iterations=4, mode="work", allow_risk={"read", "write", "exec", "network"},
            toolset_filter=None,
        )
    asyncio.run(go())
    events.unregister(sess)
    out = []
    while not q.empty():
        item = q.get_nowait()
        if isinstance(item, str):
            out.append(item)
    return "".join(out)


def test_a_builtin_search_puts_not_one_word_in_the_speech_queue(clean_registry):
    """The reproduction. Same soul patterns, same loop, same queue — only the item type differs."""
    said = _spoken([[_ws_item(1), _text("Ya está.")]], "silent_builtin", SOUL_PATTERNS)
    assert "Let me look that up." not in said, "the ANNOUNCE would have to be reachable to arrive"
    assert "Got it!" not in said
    assert said.strip() == "Ya está.", f"only her own words may reach the voice: {said!r}"


def test_the_same_voice_on_a_function_call_does_narrate(clean_registry):
    """The control: the patterns are wired correctly and the queue is being read correctly, so the
    silence above is about WHERE web_search arrives, not about a broken fixture."""
    _register_twin()
    patterns = {"search_twin": SOUL_PATTERNS["web_search"]}
    said = _spoken([[_call_item("search_twin", 1)], [_text("Ya está.")]], "loud_function", patterns)
    assert "Let me look that up." in said


def test_the_module_declares_no_voice_it_cannot_use(clean_registry):
    """Four unreachable constants are what the false docstring was written to explain. `_voice_for`
    falls through to the generic voice for it now, and nothing asks."""
    for phase in ("ANNOUNCE", "HEARTBEAT", "COMPLETE", "FAIL"):
        assert not hasattr(web_search, phase), f"web_search still declares {phase}"
    reg.discover()
    assert loop._voice_for("web_search", {}) == loop._GENERIC_VOICE


def test_the_docstring_no_longer_claims_the_patterns_narrate():
    doc = web_search.__doc__ or ""
    assert "feed SOUL_PATTERNS so the personality can still narrate" not in doc
    assert "silent by construction" in doc


def test_the_soul_block_is_labelled_inert():
    """It still parses into SOUL_PATTERNS — a user editing it must be told the edit changes nothing
    audible, or the next person spends an afternoon on it like this one did."""
    from kotoba.soul.loader import load_soul_from_file, resolve_soul_path, DEFAULT_SOUL_PATH

    text = Path(resolve_soul_path(DEFAULT_SOUL_PATH)).read_text(encoding="utf-8")
    marker = text.find("INERT")
    assert marker != -1, "nothing warns that the web_search block never plays"
    assert 0 < marker < text.find("\nweb_search:"), "the warning must come before the block it describes"

    patterns = build_soul_patterns(load_soul_from_file(DEFAULT_SOUL_PATH))
    assert patterns["web_search"]["before"], "labelling it inert must not silently delete it"
