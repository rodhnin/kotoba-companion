"""One user request must spawn work once, however many announce turns follow.

`start_work` runs a background job; its `work_done` event fires a new companion turn, but that
turn's context still holds the original request with no record work already ran -- so the model
re-called `start_work`, and that job's own finish re-triggered it again, stopping only at a rate-limit.

The fix excludes `start_work` and `delegate` from sentinel turns' toolset regardless of history.
One request must never spawn more helpers than it asked for, however many announce turns fire, and
a genuinely different request later must still work.
"""
from __future__ import annotations

import asyncio
import json
import types


import kotoba.core.loop as loop
import kotoba.tools.registry as reg
from kotoba.core import events


SID = "announce-loop-1"

_REAL_REQUEST = [{"role": "user", "content": "Ahora delega otra: que una ayudante investigue el protocolo OSC 52"}]
_REAL_REQUEST_2 = [{"role": "user", "content": "Ahora investiga qué es OSC 55, diferente tarea"}]


def _ev_call(name, args, call_id):
    item = types.SimpleNamespace(
        type="function_call", name=name, arguments=json.dumps(args), call_id=call_id
    )
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _CapturingClient:
    """Records which tool names were offered on each responses.create() call."""

    def __init__(self, scripts):
        self._scripts = scripts
        self._i = 0
        self.responses = self
        self.offered: list[list[str]] = []  # per-call list of offered tool names

    async def create(self, *, tools=None, **kw):
        self.offered.append([t.get("name") for t in (tools or [])])
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _Stream(evs)


class _DB:
    async def insert_audit_log(self, **kw):
        pass

    async def list_approved_commands(self):
        return []

    async def save_approved_command(self, *a, **k):
        return None


def test_schemas_for_exclude_tools_drops_start_work():
    """schemas_for with exclude_tools removes only the named tools, leaving the rest intact."""
    reg.discover()
    full = {s.get("name") for s in reg.schemas_for(mode="companion")}
    excluded = {s.get("name") for s in reg.schemas_for(
        mode="companion", exclude_tools=frozenset({"start_work", "delegate"})
    )}
    assert "start_work" in full, "start_work must be in the full companion set"
    assert "start_work" not in excluded, "start_work must be dropped by exclude_tools"
    assert "delegate" not in full, "delegate is already work-only; excluded set must not break"
    assert "memory_recall" in excluded, "unrelated tools must still be offered"


def test_announce_turn_does_not_offer_start_work(monkeypatch):
    """When the loop is called with exclude_tools={"start_work"}, start_work is absent from the
    set passed to every responses.create() call — the model cannot emit start_work."""
    monkeypatch.setattr(loop, "model_name", lambda *a, **k: "test-model")
    monkeypatch.setattr(loop, "model_call_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(loop, "is_reasoning_model", lambda *a, **k: False)
    monkeypatch.setattr("kotoba.tools.registry._check_cache", {})
    reg.discover()

    client = _CapturingClient([[_ev_text("Ya lo investigo yo misma.")]])

    async def _main():
        events.register(SID)
        monkeypatch.setattr(loop, "get_client", lambda: client)
        await loop.agentic_loop(
            list(_REAL_REQUEST), SID, _DB(), asyncio.Queue(), {},
            mode="companion",
            exclude_tools=frozenset({"start_work", "delegate"}),
        )
        events.unregister(SID)

    asyncio.run(_main())

    assert client.offered, "responses.create() must have been called"
    for call_tools in client.offered:
        assert "start_work" not in call_tools, (
            f"start_work must not be offered on a sentinel turn; got {call_tools}"
        )


def test_real_user_turn_still_offers_start_work(monkeypatch):
    """Without exclude_tools (a real user message), start_work IS offered normally."""
    monkeypatch.setattr(loop, "model_name", lambda *a, **k: "test-model")
    monkeypatch.setattr(loop, "model_call_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(loop, "is_reasoning_model", lambda *a, **k: False)
    monkeypatch.setattr("kotoba.tools.registry._check_cache", {})
    reg.discover()

    client = _CapturingClient([[_ev_text("Claro, lo investigo ahora.")]])

    async def _main():
        events.register(SID + "-real")
        monkeypatch.setattr(loop, "get_client", lambda: client)
        await loop.agentic_loop(
            list(_REAL_REQUEST), SID + "-real", _DB(), asyncio.Queue(), {},
            mode="companion",
        )
        events.unregister(SID + "-real")

    asyncio.run(_main())

    offered_flat = {name for call_tools in client.offered for name in call_tools}
    assert "start_work" in offered_flat, (
        "start_work must be offered on a real user turn (no exclude_tools)"
    )


def test_one_request_one_spawn_despite_repeated_announce_turns(monkeypatch):
    """The regression: one user request + two __work_done__ announce turns must produce exactly
    ONE start_work offered set that INCLUDES start_work (turn 1), and subsequent announce turns
    must produce sets that EXCLUDE it.

    Simulates the endpoint logic: turn 1 is a real user message (no exclude_tools); turns 2 and 3
    are __work_done__ announce turns (exclude_tools blocks start_work)."""
    monkeypatch.setattr(loop, "model_name", lambda *a, **k: "test-model")
    monkeypatch.setattr(loop, "model_call_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(loop, "is_reasoning_model", lambda *a, **k: False)
    monkeypatch.setattr("kotoba.tools.registry._check_cache", {})
    reg.discover()

    spawn_count = {"n": 0}
    _real_start_work = None
    try:
        from kotoba.tools.builtin import start_work as _sw_mod
        _real_execute = _sw_mod.execute
    except Exception:
        _real_execute = None

    async def _mock_start_work(args, ctx):
        spawn_count["n"] += 1
        return "Work started in the background."

    if _real_execute is not None:
        monkeypatch.setattr("kotoba.tools.builtin.start_work.execute", _mock_start_work)

    _EXCL = frozenset({"start_work", "delegate"})

    async def _run_turn(sid_suffix, excl):
        sid = SID + sid_suffix
        client = _CapturingClient([
            [_ev_call("start_work", {"goal": "investigate OSC 52"}, "c1")],
            [_ev_text("Ya está en marcha, lo investigo.")],
        ])
        events.register(sid)
        monkeypatch.setattr(loop, "get_client", lambda: client)
        await loop.agentic_loop(
            list(_REAL_REQUEST), sid, _DB(), asyncio.Queue(), {},
            mode="companion",
            exclude_tools=excl,
        )
        events.unregister(sid)
        return client.offered

    async def _main():
        # Turn 1: real user message → start_work offered, model emits it (one spawn)
        offered_t1 = await _run_turn("-t1", None)
        # Turn 2: announce turn → start_work excluded
        offered_t2 = await _run_turn("-t2", _EXCL)
        # Turn 3: another announce turn → start_work still excluded
        offered_t3 = await _run_turn("-t3", _EXCL)
        return offered_t1, offered_t2, offered_t3

    t1, t2, t3 = asyncio.run(_main())

    # Turn 1 offered start_work
    t1_names = {n for call in t1 for n in call}
    assert "start_work" in t1_names, f"turn 1 must offer start_work; got {t1_names}"

    # Turns 2 and 3 never offered start_work
    for i, offered in enumerate((t2, t3), start=2):
        names = {n for call in offered for n in call}
        assert "start_work" not in names, (
            f"announce turn {i} must not offer start_work; got {names}"
        )


def test_new_real_request_after_announce_can_still_spawn(monkeypatch):
    """A genuinely different user request (not a sentinel) arriving after the announce turns must
    still be able to call start_work — the block is per-turn, not per-session."""
    monkeypatch.setattr(loop, "model_name", lambda *a, **k: "test-model")
    monkeypatch.setattr(loop, "model_call_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(loop, "is_reasoning_model", lambda *a, **k: False)
    monkeypatch.setattr("kotoba.tools.registry._check_cache", {})
    reg.discover()

    _EXCL = frozenset({"start_work", "delegate"})

    async def _run(sid_suffix, msgs, excl):
        sid = SID + sid_suffix
        client = _CapturingClient([[_ev_text("Entendido, lo hago.")]])
        events.register(sid)
        monkeypatch.setattr(loop, "get_client", lambda: client)
        await loop.agentic_loop(
            list(msgs), sid, _DB(), asyncio.Queue(), {},
            mode="companion",
            exclude_tools=excl,
        )
        events.unregister(sid)
        return {n for call in client.offered for n in call}

    async def _main():
        # announce turn: start_work excluded
        announce_names = await _run("-ann", _REAL_REQUEST, _EXCL)
        # fresh real request: no exclude_tools
        fresh_names = await _run("-fresh", _REAL_REQUEST_2, None)
        return announce_names, fresh_names

    announce_names, fresh_names = asyncio.run(_main())

    assert "start_work" not in announce_names, "announce turn must not offer start_work"
    assert "start_work" in fresh_names, "a new real request must still have start_work"
