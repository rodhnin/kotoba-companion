"""The per-turn transport mark: is an ElevenLabs agent holding this turn's clock?

Every limit ElevenLabs imposes needs an axis before it can be relaxed, and `voice_mode` is not it — it
is an intention stored in settings, while the chat endpoint is gated by its bearer alone. With
`voice_mode=local` set and the tunnel up, an EL turn still arrives there, so a check that relaxes
anything on `voice_mode` relaxes it for exactly the caller the limit was written for, and EL cuts the
call — the defect these tests exist to make impossible.

The mark is set by the one place that knows, carried on the tool context, and read from the loop and a
tool — each entry exercised THROUGH ITSELF, since the mark surviving the route is the whole question."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import kotoba.cli.session as cli_session
import kotoba.core.voice.session as vs
import kotoba.server as main
from kotoba.core import app_settings, transport
from kotoba.tools import ToolContext

_AUTH = {"Authorization": "Bearer k"}


def _recorder(seen: dict):
    """A stand-in agentic_loop that records the mark the way its two readers see it: from the
    ContextVar (core.loop's own view) and from a ToolContext built where the loop builds one."""

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        seen["contextvar"] = transport.el_call_bound()
        ctx = ToolContext(db=db, session_id=session_id, mode="companion")
        seen["ctx"] = ctx.el_call_bound
        seen["child"] = ctx.child("sub").el_call_bound
        await queue.put("ok")
        return "ok"

    return fake_loop


# ---- entry point 1: /v1/chat/completions, the only route an ElevenLabs agent can reach -------------


@pytest.fixture
def v1_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "mark.db"))
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    with TestClient(main.app) as c:
        yield c


def _post_turn(client, sid="mark-v1"):
    return client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": [{"role": "user", "content": "hola"}], "session_id": sid, "stream": True,
    })


def test_a_v1_turn_is_marked_el_call_bound(v1_client, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(main, "agentic_loop", _recorder(seen))

    assert _post_turn(v1_client).status_code == 200

    assert seen["contextvar"] is True, "the /v1 producer did not mark its own turn"
    assert seen["ctx"] is True, "the mark did not reach the ToolContext the loop builds"
    assert seen["child"] is True, "a subagent's context lost the transport it was spawned under"


def test_voice_mode_local_does_not_unmark_a_v1_turn(v1_client, monkeypatch):
    """The reproduction. `voice_mode=local` is the daily configuration AND the tunnel can be up: this
    turn arrives from an ElevenLabs agent while the setting says otherwise. Anything keying a
    relaxation on the setting would relax it here, on the one caller that cannot survive it."""
    app_settings.set_runtime("voice_mode", "local")
    assert app_settings.runtime_all()["voice_mode"] == "local"
    seen: dict = {}
    monkeypatch.setattr(main, "agentic_loop", _recorder(seen))

    assert _post_turn(v1_client, "mark-v1-local").status_code == 200

    assert seen["ctx"] is True, "the setting overrode the transport — this is the §0.1 defect"


def test_the_mark_does_not_leak_out_of_the_turn_that_set_it(v1_client, monkeypatch):
    """It rides a ContextVar on the producer's own task. The request handler that created that task,
    and everything it spawns afterwards (the memory extraction), must still read False."""
    seen: dict = {}
    monkeypatch.setattr(main, "agentic_loop", _recorder(seen))

    assert _post_turn(v1_client, "mark-v1-leak").status_code == 200

    assert seen["ctx"] is True
    assert transport.el_call_bound() is False, "the mark escaped the turn's task"


# ---- entry point 2: the local voice WebSocket ------------------------------------------------------


def test_the_local_voice_socket_is_not_el_call_bound(monkeypatch):
    """Our own transport: we call ElevenLabs' STT/TTS APIs outbound, but no agent holds a call clock
    over the turn. Driven through the real WS route, with the session's own fake EL clients."""
    from test_voice_ws import FakeStt, FakeTts

    FakeStt.instances, FakeStt.script, FakeStt.fail_connect = [], ["hola"], None
    FakeStt.drop_after_first_event = False
    FakeStt.die_instantly = False
    FakeTts.instances, FakeTts.fail_connect = [], None
    monkeypatch.setattr(vs, "SttClient", FakeStt)
    monkeypatch.setattr(vs, "TtsClient", FakeTts)

    async def no_memory(user_text, db):
        return None

    monkeypatch.setattr(vs, "extract_and_save_memory", no_memory)
    seen: dict = {}
    monkeypatch.setattr(vs, "agentic_loop", _recorder(seen))

    with TestClient(main.app) as c:
        with c.websocket_connect("/api/voice/mark-ws") as ws:
            ws.receive()  # ready
            ws.send_bytes(b"\x00\x01" * 320)
            ws.send_text(json.dumps({"type": "commit"}))
            for _ in range(60):
                msg = ws.receive()
                if msg.get("bytes") is not None:
                    continue
                if json.loads(msg["text"])["type"] == "turn_end":
                    break

    assert seen["contextvar"] is False
    assert seen["ctx"] is False, "the local socket claimed an ElevenLabs call it does not have"


# ---- entry point 3: the CLI, which is neither voice mode -------------------------------------------


def test_the_cli_is_not_el_call_bound(monkeypatch):
    """Named for the CONSTRAINT and not for the setting, precisely so this reads correctly: the CLI is
    neither `agent` nor `local`, and "not agent mode" would have marked it wrong."""
    seen: dict = {}
    monkeypatch.setattr(cli_session, "agentic_loop", _recorder(seen))

    async def go():
        session = await cli_session.Session.open()
        try:
            await session.ask("hola")
        finally:
            await session.close()

    asyncio.run(go())

    assert seen["contextvar"] is False
    assert seen["ctx"] is False


# ---- the tool's-eye view ---------------------------------------------------------------------------


def test_a_tool_reads_the_mark_off_its_own_context(v1_client, monkeypatch):
    """Not the loop's private business: a tool's execute() gets the same fact, on the ctx it already
    has, which is what lets an approval window or a budget be chosen where it is spent."""
    seen: dict = {}

    async def loop_that_runs_a_tool(input_items, session_id, db, queue, soul_patterns, **kw):
        ctx = ToolContext(db=db, session_id=session_id, mode="companion")

        async def execute(args, c):
            seen["in_tool"] = c.el_call_bound
            return "done"

        await execute({}, ctx)
        await queue.put("ok")
        return "ok"

    monkeypatch.setattr(main, "agentic_loop", loop_that_runs_a_tool)
    assert _post_turn(v1_client, "mark-tool").status_code == 200

    assert seen["in_tool"] is True


def test_a_hand_built_context_defaults_to_the_safe_side():
    """Nothing set it, so nothing may be relaxed for it. The tri-state consumers see (None for an
    object with no mark at all) is core.interaction's; a real ToolContext always answers."""
    assert ToolContext(db=None).el_call_bound is False
