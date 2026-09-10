"""Reasoning-model contract: when a reasoning model (gpt-5.x) calls a tool, its `reasoning` item must be
fed back alongside the `function_call` on the next iteration, or the Responses API 400s
("function_call provided without its required reasoning item") and the loop crashes. _run_iterations must
re-add the model's output items (reasoning + function_call) to `input`, and append ONLY the
function_call_output per tool (never re-append the function_call). Also: model_call_kwargs gating."""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from kotoba.core import events
import kotoba.core.loop as loop
import kotoba.core.llm as llm
import kotoba.tools.registry as reg
from kotoba.tools import ToolContext
from kotoba.tools.registry import ToolSpec


def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_reasoning(rid="rs_1"):
    item = types.SimpleNamespace(type="reasoning", id=rid, encrypted_content="ENC", summary=[])
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _ev_call(name, args, cid="fc_1"):
    item = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=cid)
    return types.SimpleNamespace(type="response.output_item.done", item=item)


class _Stream:
    def __init__(self, evs): self._evs = evs
    def __aiter__(self):
        async def gen():
            for e in self._evs: yield e
        return gen()


class _Responses:
    """Scripted; records the `input` each create() received so we can assert what was fed back."""
    def __init__(self, scripts): self.scripts = scripts; self.i = 0; self.inputs = []
    async def create(self, **kw):
        self.inputs.append(list(kw.get("input") or []))
        evs = self.scripts[min(self.i, len(self.scripts) - 1)]; self.i += 1
        return _Stream(evs)


class _Client:
    def __init__(self, scripts): self.responses = _Responses(scripts)


class _DB:
    async def insert_audit_log(self, **k): pass


@pytest.fixture
def clean_registry():
    saved, savedc = dict(reg._REGISTRY), dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear(); reg._REGISTRY.update(saved)
        reg._check_cache.clear(); reg._check_cache.update(savedc)


def _register(name, result="ok", announce="", complete="done"):
    async def execute(args, ctx): return result
    mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": name}, __name__=f"tools.x.{name}",
                                ANNOUNCE=announce, HEARTBEAT=[], COMPLETE=complete, FAIL="oops", execute=execute)
    reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA, toolset="file", risk="write"))


def _drain(queue):
    chunks = []
    while not queue.empty():
        chunks.append(str(queue.get_nowait()))
    return "".join(chunks)


def _run_one_toolcall(monkeypatch_effort, monkeypatch, sid):
    """Run a single write_file tool turn with distinctive canned narration; return queued stream text.

    `monkeypatch_effort=None` picks a non-reasoning model, which keeps the canned narration; any effort
    picks a reasoning one, which narrates in its own words instead."""
    if monkeypatch_effort is None:
        monkeypatch.delenv("KOTOBA_REASONING_EFFORT", raising=False)
        monkeypatch.setenv("KOTOBA_MODEL", "gpt-4o-mini")
    else:
        monkeypatch.setenv("KOTOBA_REASONING_EFFORT", monkeypatch_effort)
        monkeypatch.setenv("KOTOBA_MODEL", "gpt-5.4-mini")
    _register("write_file", result="wrote it", announce="ANNOUNCE_MARK", complete="COMPLETE_MARK")
    scripts = [[_ev_reasoning("rs_1"), _ev_call("write_file", {"path": "a.txt", "content": "hi"}, "fc_1")],
               [_ev_text("MODEL_OWN_WORDS")]]
    client = _Client(scripts)
    events.register(sid)
    queue = asyncio.Queue()
    ctx = ToolContext(db=_DB(), session_id=sid, client=None, mode="work"); ctx.approval = None

    async def go():
        await loop._run_iterations(client, ctx, [{"role": "user", "content": "make a file"}],
                                   queue, {}, max_iterations=5, mode="work",
                                   allow_risk={"read", "write", "exec", "network"}, toolset_filter=None)
    asyncio.run(go())
    events.unregister(sid)
    return _drain(queue)


def test_reasoning_model_suppresses_canned_tool_narration(clean_registry, monkeypatch):
    """A reasoning model narrates in its own voice and language, so the canned English ANNOUNCE and
    COMPLETE strings must NOT be spoken — they would duplicate it and leak English into a reply in
    another language. Its own words still flow."""
    out = _run_one_toolcall("medium", monkeypatch, "rs_narr_on")
    assert "ANNOUNCE_MARK" not in out, out
    assert "COMPLETE_MARK" not in out, out
    assert "MODEL_OWN_WORDS" in out, out


def test_non_reasoning_model_still_narrates(clean_registry, monkeypatch):
    """The other branch: with no effort set the canned narration is the only voice a tool call gets,
    so it stays."""
    out = _run_one_toolcall(None, monkeypatch, "rs_narr_off")
    assert "ANNOUNCE_MARK" in out, out
    assert "COMPLETE_MARK" in out, out


def test_is_reasoning_model_gating(monkeypatch):
    """Both halves of the signal: a reasoning model with an effort set reasons, and nothing else does.

    The last case is the regression worth naming — even with an effort set, a NON-reasoning model is
    never "reasoning"."""
    monkeypatch.setenv("KOTOBA_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "medium")
    assert llm.is_reasoning_model() is True
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "off")
    assert llm.is_reasoning_model() is False
    monkeypatch.delenv("KOTOBA_REASONING_EFFORT", raising=False)
    assert llm.is_reasoning_model() is True
    monkeypatch.setenv("KOTOBA_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "low")
    assert llm.is_reasoning_model() is False


def test_reasoning_item_is_fed_back_with_function_call(clean_registry):
    """The contract itself, over two iterations: reasoning plus a tool call, then the final text.

    The second create() call's input must carry the reasoning item, the function_call exactly once (a
    re-appended duplicate is what 400s) and exactly one function_call_output for it — reasoning first."""
    _register("write_file", result="wrote it")
    scripts = [[_ev_reasoning("rs_1"), _ev_call("write_file", {"path": "a.txt", "content": "hi"}, "fc_1")],
               [_ev_text("Done!")]]
    client = _Client(scripts)
    q = events.register("rs_sess")
    ctx = ToolContext(db=_DB(), session_id="rs_sess", client=None, mode="work"); ctx.approval = None

    async def go():
        await loop._run_iterations(client, ctx, [{"role": "user", "content": "make a file"}],
                                   asyncio.Queue(), {}, max_iterations=5, mode="work",
                                   allow_risk={"read", "write", "exec", "network"}, toolset_filter=None)
    asyncio.run(go())
    events.unregister("rs_sess")

    second_input = client.responses.inputs[1]
    types_in = [(i.get("type") if isinstance(i, dict) else getattr(i, "type", None)) for i in second_input]
    assert "reasoning" in types_in, types_in
    assert types_in.count("function_call") == 1, types_in
    assert types_in.count("function_call_output") == 1, types_in
    assert types_in.index("reasoning") < types_in.index("function_call")


def test_reasoning_encrypted_content_survives_multi_round(clean_registry):
    """A watch item, after a streaming bug elsewhere was seen to drop encrypted_content.

    Across SEVERAL tool rounds, every reasoning item re-fed must still carry a non-empty
    encrypted_content, or stateless (store=False) reasoning 400s on the next call. Two tool rounds and
    a final answer, so the THIRD create() input is the one carrying the whole re-fed history."""
    _register("write_file", result="wrote it")
    scripts = [
        [_ev_reasoning("rs_1"), _ev_call("write_file", {"path": "a.txt", "content": "1"}, "fc_1")],
        [_ev_reasoning("rs_2"), _ev_call("write_file", {"path": "b.txt", "content": "2"}, "fc_2")],
        [_ev_text("Done!")],
    ]
    client = _Client(scripts)
    events.register("rs_multi")
    ctx = ToolContext(db=_DB(), session_id="rs_multi", client=None, mode="work"); ctx.approval = None

    async def go():
        await loop._run_iterations(client, ctx, [{"role": "user", "content": "make two files"}],
                                   asyncio.Queue(), {}, max_iterations=5, mode="work",
                                   allow_risk={"read", "write", "exec", "network"}, toolset_filter=None)
    asyncio.run(go())
    events.unregister("rs_multi")

    final_input = client.responses.inputs[2]
    reasonings = [i for i in final_input if getattr(i, "type", None) == "reasoning"]
    assert len(reasonings) == 2, reasonings
    assert all(getattr(r, "encrypted_content", None) for r in reasonings), reasonings


def test_model_call_kwargs_reasoning_stateless(monkeypatch):
    """The default for a reasoning model: unstored, with the encrypted reasoning included so it can be
    fed back on the next call."""
    monkeypatch.setenv("KOTOBA_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "medium")
    monkeypatch.delenv("KOTOBA_LLM_STORE", raising=False)
    kw = llm.model_call_kwargs()
    assert kw["reasoning"] == {"effort": "medium"}
    assert kw["store"] is False
    assert kw["include"] == ["reasoning.encrypted_content"]


def test_model_call_kwargs_stored_opt_in(monkeypatch):
    """Opting into server-side storage drops the include: the API keeps the reasoning itself."""
    monkeypatch.setenv("KOTOBA_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "high")
    monkeypatch.setenv("KOTOBA_LLM_STORE", "true")
    kw = llm.model_call_kwargs()
    assert kw["store"] is True and "include" not in kw


def test_model_call_kwargs_non_reasoning(monkeypatch):
    """Only "off" and an explicit empty string switch this axis off entirely.

    Unset is NOT one of them any more: it means 'low', which is the whole point of the default. An
    unset effort used to send no `reasoning` block, and `store=False` rode along with it, so a stock
    install left its reasoning retained at the provider."""
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "off")
    assert llm.model_call_kwargs() == {}
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "")
    assert llm.model_call_kwargs() == {}
    monkeypatch.delenv("KOTOBA_REASONING_EFFORT", raising=False)
    assert llm.model_call_kwargs()["reasoning"] == {"effort": "low"}


def test_model_call_kwargs_non_reasoning_model_with_effort_set(monkeypatch):
    """REGRESSION: model and reasoning_effort are independent runtime settings.

    Picking a non-reasoning model in Settings while reasoning_effort is left at 'low' must NOT send the
    reasoning or encrypted-content kwargs — OpenAI 400s with "Encrypted content is not supported with
    this model"."""
    monkeypatch.setenv("KOTOBA_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "low")
    assert llm.model_call_kwargs("companion") == {}
    assert llm.model_call_kwargs("work") == {}


def _ev_message(text, mid="msg_1"):
    part = types.SimpleNamespace(type="output_text", text=text, annotations=[])
    item = types.SimpleNamespace(type="message", id=mid, role="assistant", content=[part],
                                 status="completed")
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _assistant_text(items) -> str:
    """Every word of hers the next call carries back, however it is shaped."""
    out = []
    for it in items:
        get = it.get if isinstance(it, dict) else (lambda k, d=None: getattr(it, k, d))
        if get("type") not in ("message", None) or get("role") != "assistant":
            continue
        content = get("content")
        if isinstance(content, str):
            out.append(content)
            continue
        for part in content or []:
            pget = part.get if isinstance(part, dict) else (lambda k, d=None: getattr(part, k, d))
            out.append(str(pget("text") or ""))
    return "\n".join(out)


def test_what_she_already_said_goes_back_with_the_tool_call_she_made(clean_registry):
    """One response can both SPEAK and call a tool. Only the call was fed onward, so the next iteration
    asked a model with no record of having spoken — and it wrote the same paragraphs again, into the same
    reply, which is what reached the screen twice."""
    _register("write_file", result="wrote it")
    said = "Ya lo estoy delegando.\n\nDame un momento y te cuento."
    scripts = [[_ev_reasoning("rs_1"), _ev_text(said), _ev_message(said),
                _ev_call("write_file", {"path": "a.txt", "content": "hi"}, "fc_1")],
               [_ev_text("Listo.")]]
    client = _Client(scripts)
    events.register("dup_sess")
    ctx = ToolContext(db=_DB(), session_id="dup_sess", client=None, mode="companion")
    ctx.approval = None

    async def go():
        await loop._run_iterations(client, ctx, [{"role": "user", "content": "delega"}],
                                   asyncio.Queue(), {}, max_iterations=5, mode="companion",
                                   allow_risk={"read", "write", "exec", "network"}, toolset_filter=None)
    asyncio.run(go())
    events.unregister("dup_sess")

    second = client.responses.inputs[1]
    assert said in _assistant_text(second), [getattr(i, "type", i) for i in second]
    types_in = [(i.get("type") if isinstance(i, dict) else getattr(i, "type", None)) for i in second]
    assert types_in.count("message") == 1, types_in
    assert types_in.index("reasoning") < types_in.index("function_call")
