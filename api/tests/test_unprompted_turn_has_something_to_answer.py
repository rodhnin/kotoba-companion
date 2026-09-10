"""A background event (a resolved approval card, a due reminder) must arrive in the position a
reply answers, not as a developer note trailing an already-answered turn, or the last real
exchange reads as still-open and produces a stray re-greeting with the news as a postscript.

The fix is the ROLE, not the wording: the event goes in the USER position, pinned across all
three transports that build context through `load_context` — the ElevenLabs route, the local
voice WebSocket, the CLI — for both trigger kinds, since a fix landing on only one is not one.

Out of scope: what she then SAYS — that is generation, and nothing here calls a model.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from kotoba.core import pending_reminder, work_state
from kotoba.core.context import inject_work_note
from kotoba.models.schemas import ChatRequest

REMINDER = "__reminder__"
WORK_DONE = "__work_done__"

HISTORY = [
    {"role": "user", "content": "Hola."},
    {"role": "assistant", "content": "[warmly] Hola, Jordan. Qué gusto verte por aquí."},
]


@pytest.fixture(autouse=True)
def _clean_state():
    work_state._state.clear()
    pending_reminder._pending.clear()
    yield
    work_state._state.clear()
    pending_reminder._pending.clear()


def _arm_reminder(sid):
    pending_reminder.add(sid, "escribirle a Marta")


def _arm_work_done(sid):
    work_state.start(sid, "run the command")
    work_state.set_step(sid, "waiting for approval")
    work_state.finish(sid, "I didn't run it — I never got the go-ahead.", [])


def _roles(items):
    return [m["role"] for m in items]


def _assert_event_is_the_last_thing_asked(items, *, says):
    """The shape every transport has to produce on a trigger turn."""
    assert _roles(items) == ["developer", "user", "assistant", "user"], _roles(items)
    tail = items[-1]
    assert "BACKGROUND EVENT" in tail["content"]
    assert says in tail["content"]
    assert "did not write or say this" in tail["content"]
    assert not any(
        m["role"] == "developer" and says in m["content"] for m in items[1:]
    ), "the event was ALSO left as a developer note — she would be told it twice"


class _DB:
    """Enough database for load_context: the recent turns it falls back to, plus soul and profile."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    async def fetch_recent_turns(self, *a, **k):
        return list(self.rows)

    async def fetch_soul_config(self):
        return {"name": "Kotoba", "language": "auto"}

    async def fetch_user_profile_as_markdown(self):
        return ""


# --- the shape itself, at the seam that builds it ----------------------------------------------------

def test_the_event_takes_the_user_position_and_the_developer_note_is_gone():
    _arm_reminder("u1")
    items = inject_work_note(
        [{"role": "developer", "content": "SYS"}] + HISTORY, "u1", unprompted=True
    )
    _assert_event_is_the_last_thing_asked(items, says="escribirle a Marta")


def test_a_prompted_turn_is_untouched():
    """He IS asking. The note must stay a developer note at the tail, or it displaces his own message
    from the position she answers — the same defect, mirrored."""
    _arm_reminder("u2")
    items = [{"role": "developer", "content": "SYS"},
             {"role": "user", "content": "¿cómo va todo?"}]
    out = inject_work_note(items, "u2", unprompted=False)
    assert _roles(out) == ["developer", "user", "developer"]
    assert out[:-1] == items and "escribirle a Marta" in out[-1]["content"]


def test_the_conversation_is_only_called_open_when_there_is_one():
    """The clause that says "you have not just arrived" is a fact about this turn, not a rule against
    greeting: with nothing above, an event opening the session may greet, and nothing here says it
    can't."""
    _arm_reminder("u3")
    with_history = inject_work_note(
        [{"role": "developer", "content": "SYS"}] + HISTORY, "u3", unprompted=True
    )[-1]["content"]
    alone = inject_work_note([{"role": "developer", "content": "SYS"}], "u3", unprompted=True)[-1]["content"]
    assert "already open" in with_history and "have not just arrived" in with_history
    assert "already open" not in alone and "have not just arrived" not in alone
    assert "escribirle a Marta" in alone


def test_an_open_approval_card_stays_a_developer_note():
    """deferred_exec's note says "say nothing further about it". In the user position that would be the
    turn's subject — an order to speak and an order to be quiet in the same breath."""
    from kotoba.core import deferred_exec

    deferred_exec._awaiting["u4"] = {"k": "rm -rf build"}
    try:
        _arm_work_done("u4")
        items = inject_work_note(
            [{"role": "developer", "content": "SYS"}] + HISTORY, "u4", unprompted=True
        )
        assert _roles(items) == ["developer", "user", "assistant", "developer", "user"]
        assert "WAITING ON THE USER" in items[-2]["content"]
        assert "WAITING ON THE USER" not in items[-1]["content"]
        assert "BACKGROUND WORK just finished" in items[-1]["content"]
    finally:
        deferred_exec._awaiting.pop("u4", None)


def test_a_trigger_with_nothing_left_to_say_is_told_so():
    """The same hole, with the note already spent (a duplicate work_done frame, a reminder acknowledged
    between the event and the turn). Handed the bare transcript she answers the last thing she can see,
    which is the message before this one."""
    items = inject_work_note(
        [{"role": "developer", "content": "SYS"}] + HISTORY, "u5", unprompted=True
    )
    assert _roles(items) == ["developer", "user", "assistant", "user"]
    assert "nothing left to tell them" in items[-1]["content"]
    assert "already answered" in items[-1]["content"]


# --- transport 1: the ElevenLabs custom-LLM route ----------------------------------------------------

_AUTH = {"Authorization": "Bearer k"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "unprompted.db"))
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(tmp_path / "mcp.yaml"))
    import kotoba.tools.registry as reg
    saved_cache, saved_disabled = dict(reg._check_cache), set(reg._disabled_toolsets)
    from fastapi.testclient import TestClient

    import kotoba.server as main
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        reg._check_cache.clear(); reg._check_cache.update(saved_cache)
        reg._disabled_toolsets.clear(); reg._disabled_toolsets.update(saved_disabled)


def _v1_items(client, monkeypatch, sid, trigger):
    """Drive the real route with the real load_context and keep what reached the loop. ElevenLabs
    resends the whole conversation on every call, so the sentinel arrives behind it."""
    import kotoba.server as main

    seen: dict = {}

    async def _spy(items, *a, **kw):
        seen["items"] = items
        return "ok"

    monkeypatch.setattr(main, "agentic_loop", _spy)
    r = client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": HISTORY + [{"role": "user", "content": trigger}],
        "session_id": sid, "stream": True,
    })
    assert r.status_code == 200
    return seen["items"]


def test_v1_reminder_turn(client, monkeypatch):
    _arm_reminder("v1-rem")
    _assert_event_is_the_last_thing_asked(
        _v1_items(client, monkeypatch, "v1-rem", REMINDER), says="escribirle a Marta"
    )


def test_v1_work_done_turn(client, monkeypatch):
    _arm_work_done("v1-work")
    _assert_event_is_the_last_thing_asked(
        _v1_items(client, monkeypatch, "v1-work", WORK_DONE), says="I never got the go-ahead"
    )


def test_v1_a_real_message_still_ends_on_him(client, monkeypatch):
    """The guard on the guard: a turn he actually spoke must still end on his words."""
    _arm_reminder("v1-real")
    import kotoba.server as main

    seen: dict = {}

    async def _spy(items, *a, **kw):
        seen["items"] = items
        return "ok"

    monkeypatch.setattr(main, "agentic_loop", _spy)
    client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": HISTORY + [{"role": "user", "content": "¿y qué tal el día?"}],
        "session_id": "v1-real", "stream": True,
    })
    items = seen["items"]
    assert items[-1]["role"] == "developer" and "escribirle a Marta" in items[-1]["content"]
    assert items[-2] == {"role": "user", "content": "¿y qué tal el día?"}


# --- transport 2: the local voice WebSocket ----------------------------------------------------------

class _FakeWS:
    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, payload):
        self.sent.append(payload)

    async def accept(self):
        pass


def _ws_items(monkeypatch, sid, trigger):
    """The WS sends only the sentinel, so history comes from the DB fallback — the other of the two
    code paths into the same shape."""
    import kotoba.core.voice.session as vs
    from kotoba.db.database import Database

    seen: dict = {}

    async def _spy(items, *a, **kw):
        seen["items"] = items
        return "ok"

    async def _pump(queue, turn_no):
        while await queue.get() is not vs.sse.DONE_SENTINEL:
            pass
        return True

    async def _speak(line, turn_no):
        pass

    monkeypatch.setattr(vs, "agentic_loop", _spy)

    async def go():
        db = Database("sqlite:///:memory:")
        await db.connect()
        try:
            await db.ensure_session(sid)
            for m in HISTORY:
                await db.insert_turn(sid, m["role"], m["content"])
            s = vs.VoiceSession(_FakeWS(), sid, db=db, soul_patterns={}, mcp=None)
            monkeypatch.setattr(s, "_pump_speech", _pump)
            monkeypatch.setattr(s, "_speak_line", _speak)
            await s._run_turn(trigger, True, 1)
        finally:
            await db.close()

    asyncio.run(go())
    return seen["items"]


def test_ws_reminder_turn(monkeypatch):
    _arm_reminder("ws-rem")
    _assert_event_is_the_last_thing_asked(
        _ws_items(monkeypatch, "ws-rem", REMINDER), says="escribirle a Marta"
    )


def test_ws_work_done_turn(monkeypatch):
    _arm_work_done("ws-work")
    _assert_event_is_the_last_thing_asked(
        _ws_items(monkeypatch, "ws-work", WORK_DONE), says="I never got the go-ahead"
    )


# --- transport 3: the CLI ----------------------------------------------------------------------------

def _cli_items(monkeypatch, tmp_path, arm, trigger):
    """The CLI mints its own session id (register() must not steal a browser's queue), so the event is
    armed against the id it chose."""
    import kotoba.cli.session as cli_session
    from kotoba.core import events
    from kotoba.db.database import Database

    seen: dict = {}

    async def _spy(items, *a, **kw):
        seen["items"] = items
        return ""

    monkeypatch.setattr(cli_session, "agentic_loop", _spy)

    async def go():
        db = Database("sqlite:///" + str(tmp_path / "cli.db"))
        await db.connect()
        session = cli_session.Session(types.SimpleNamespace(db=db, mcp=None, soul_patterns={}))
        sid = session.session_id
        try:
            await db.ensure_session(sid)
            for m in HISTORY:
                await db.insert_turn(sid, m["role"], m["content"])
            arm(sid)
            await session.ask(trigger)
        finally:
            events.unregister(sid, session.queue)
            await db.close()

    asyncio.run(go())
    return seen["items"]


def test_cli_reminder_turn(monkeypatch, tmp_path):
    _assert_event_is_the_last_thing_asked(
        _cli_items(monkeypatch, tmp_path, _arm_reminder, REMINDER), says="escribirle a Marta"
    )


def test_cli_work_done_turn(monkeypatch, tmp_path):
    _assert_event_is_the_last_thing_asked(
        _cli_items(monkeypatch, tmp_path, _arm_work_done, WORK_DONE), says="I never got the go-ahead"
    )


# --- the seam that carries it to all three -----------------------------------------------------------

def test_load_context_decides_unprompted_itself():
    """No transport passes a flag. `load_context` reads the incoming message, so a fourth caller gets
    this for free and cannot forget it."""
    import inspect

    from kotoba.core import context

    src = inspect.getsource(context.load_context)
    assert "unprompted = is_trigger_sentinel" in src
    assert "unprompted=unprompted" in src

    _arm_reminder("seam")
    for messages in ([{"role": "user", "content": REMINDER}],
                     HISTORY + [{"role": "user", "content": WORK_DONE}]):
        req = ChatRequest(messages=messages, session_id="seam")
        items = asyncio.run(context.load_context(req, _DB(HISTORY), "seam", persisted=False))
        assert items[-1]["role"] == "user" and "BACKGROUND EVENT" in items[-1]["content"]


def test_an_image_with_no_caption_is_not_an_unprompted_turn():
    """`__image_only__` is deliberately not a trigger sentinel: the user really did do something, and
    the attachment becomes a user message of its own."""
    from kotoba.core import attachments, context

    attachments.add("img", [{"type": "input_image", "image_url": "data:image/png;base64,AA"}])
    req = ChatRequest(messages=[{"role": "user", "content": "__image_only__"}], session_id="img")
    items = asyncio.run(context.load_context(req, _DB(HISTORY), "img", persisted=False))
    assert all("BACKGROUND EVENT" not in str(m["content"]) for m in items)
    assert items[-1]["role"] == "user" and isinstance(items[-1]["content"], list)
