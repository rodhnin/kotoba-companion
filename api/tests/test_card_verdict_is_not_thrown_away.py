"""The card records which of its four endings happened, and both consumers threw it away.

`request_approval` writes APPROVED / DECLINED / UNANSWERED / UNREACHABLE into the caller's `card`,
but neither the deferred path nor the gate read it, so each told the wrong story in opposite
directions: DEFERRED reported a decline as a timeout, and INLINE reported a timeout as a decline in
the audit log itself, asserting the user saw and refused what it never showed.

Both now route through the ONE verdict, driving the real request_approval with only the wait
replaced, so the verdict under test is the one production writes."""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.deferred_exec as de
from kotoba.core import events, interaction, work_state
from kotoba.core.approval import ApprovalGate


def _answer(monkeypatch, resp):
    """Replace the WAIT and nothing else: `resp` is what the user did (None = the window closed)."""
    async def _wait(session_id, request_id, fut, timeout):
        (interaction._pending.get(session_id) or {}).pop(request_id, None)
        return resp

    monkeypatch.setattr(interaction, "_await_response", _wait)


class _Ctx:
    def __init__(self, gate, sid, channel="voice"):
        self.approval = gate
        self.mode = "companion"
        self.channel = channel
        self.session_id = sid
        self.call_id = "c1"
        self.user_text = "run the thing"
        self._open_steps = {}


def _gate(rows):
    return ApprovalGate(host_exec=True, audit=_recorder(rows))


def _deferred(monkeypatch, sid, action, resp, listening=True):
    """One detached approval, start to finish. Returns (what she says, what the model is told, audit)."""
    de.forget_session(sid)
    work_state.clear(sid)
    rows: list[dict] = []
    _answer(monkeypatch, resp)
    ctx = _Ctx(_gate(rows), sid)
    queue = events.register(sid) if listening else None

    async def go():
        await de._run_on_approval(ctx, action, _never, action, {"id": "c1"}, key=f"k-{sid}")

    try:
        asyncio.run(go())
    finally:
        if queue is not None:
            events.unregister(sid, queue)
    spoken = work_state.get(sid)["summary"]
    told = (de._settled.get(sid) or {}).get(f"k-{sid}", "")
    de.forget_session(sid)
    work_state.clear(sid)
    return spoken, told, rows


async def _never() -> str:
    raise AssertionError("the runner must not run when the approval did not land")


# --- (a) the deferred path ---------------------------------------------------------------------------

def test_a_decline_is_reported_as_the_decision_it_was(monkeypatch):
    spoken, told, _rows = _deferred(monkeypatch, "defer-no", "pip install requests", {"approved": False})

    assert "said no" in spoken.lower()
    assert "never got the go-ahead" not in spoken.lower(), spoken
    assert "timed out" not in spoken.lower() and "expired" not in spoken.lower(), spoken
    assert "said NO" in told


def test_an_expired_card_is_never_reported_as_a_decline(monkeypatch):
    spoken, told, _rows = _deferred(monkeypatch, "defer-silent", "pip install requests", None)

    assert "timed out" in spoken.lower() or "expired" in spoken.lower(), spoken
    for lie in ("said no", "declined", "you didn't want"):
        assert lie not in spoken.lower(), f"a card nobody answered was voiced as a decision: {spoken!r}"
    assert "expired with no answer" in told and "not their choice" in told


def test_a_card_that_could_not_be_shown_says_that_instead(monkeypatch):
    spoken, told, _rows = _deferred(monkeypatch, "defer-blind", "pip install requests", None,
                                    listening=False)

    assert "couldn't" in spoken.lower() or "could not" in spoken.lower(), spoken
    assert "said no" not in spoken.lower()
    assert "could not ask" in told


@pytest.mark.parametrize("resp,who", [({"approved": False}, "user"), (None, "expired")])
def test_the_deferred_audit_row_names_who_actually_decided(monkeypatch, resp, who):
    _spoken, _told, rows = _deferred(monkeypatch, f"defer-audit-{who}", "pip install requests", resp)

    assert [(r["approver"], r["approved"], r["detail"]) for r in rows] == [(who, False, "decision")]


def test_an_unshowable_deferred_card_is_audited_as_error(monkeypatch):
    _spoken, _told, rows = _deferred(monkeypatch, "defer-audit-blind", "pip install x", None,
                                     listening=False)

    assert [(r["approver"], r["approved"]) for r in rows] == [("error", False)]


def test_an_approval_still_runs_and_is_still_the_users(monkeypatch):
    sid = "defer-yes"
    de.forget_session(sid)
    work_state.clear(sid)
    rows: list[dict] = []
    _answer(monkeypatch, {"approved": True})
    queue = events.register(sid)

    async def runner() -> str:
        return "I ran `pip install requests` (exit code 0)."

    try:
        asyncio.run(de._run_on_approval(_Ctx(_gate(rows), sid), "pip install requests", runner,
                                        "pip install requests", {"id": "c1"}, key="k-yes"))
    finally:
        events.unregister(sid, queue)

    assert "exit code 0" in work_state.get(sid)["summary"]
    assert [(r["approver"], r["approved"], r["detail"]) for r in rows] == [
        ("user", True, "decision"), ("user", True, "executed"),
    ]
    de.forget_session(sid)
    work_state.clear(sid)


# --- (b) the inline path: the audit row -------------------------------------------------------------

class _Sandbox:
    """Stands in for the host: a real turn is driven here and nothing may reach the machine."""

    def __init__(self):
        self.ran: list[str] = []

    async def start(self):
        pass

    async def kill(self):
        pass

    async def run(self, command, cwd=".", timeout=60):
        self.ran.append(command)
        return type("R", (), {"stdout": "/dev/sda1  40G", "stderr": "", "exit_code": 0})()


class _Item:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for event in self._evs:
                yield event
        return gen()


class _Client:
    def __init__(self, turns):
        rest = list(turns)
        self.responses = type("R", (), {"create": lambda _s, **kw: _said(_Stream(rest.pop(0)))})()


class _DB:
    def __init__(self):
        self.audit: list[dict] = []

    async def list_approved_commands(self):
        return []

    async def insert_audit_log(self, **kw):
        self.audit.append(kw)


def _inline(monkeypatch, tmp_path, sid, resp, listening=True):
    """One REAL typed turn: the model calls `shell`, the gate asks inline through the loop's own asker,
    and the audit rows are read off the database the loop writes to. Returns (rows, sandbox)."""
    import kotoba.core.loop as loop
    from kotoba.core import sandbox, session_sandbox, workspace

    sb = _Sandbox()
    monkeypatch.setattr(sandbox, "backend_name", lambda: "local")
    monkeypatch.setattr(workspace, "resolve_workdir", lambda _sid: tmp_path)
    monkeypatch.setattr(session_sandbox, "acquire", lambda *a, **k: _said(sb))
    _answer(monkeypatch, resp)
    call = _Item(type="response.output_item.done",
                 item=_Item(type="function_call", name="shell", call_id="c1",
                            arguments='{"command": "df -h"}'))
    said = _Item(type="response.output_text.delta", delta="Ya está.")
    monkeypatch.setattr(loop, "get_client", lambda: _Client([[call], [said]]))
    db = _DB()

    async def go():
        queue = events.register(sid) if listening else None
        try:
            await loop.agentic_loop([{"role": "user", "content": "córreme df -h"}], sid, db,
                                    asyncio.Queue(), {}, max_iterations=2, mode="work", channel="text")
        finally:
            if queue is not None:
                events.unregister(sid, queue)

    asyncio.run(go())
    return [r for r in db.audit if r.get("detail") == "decision"], sb


def _inline_rows(monkeypatch, tmp_path, sid, resp):
    """The same turn, but keeping the terminal `step` frames — what the person actually reads."""
    frames: list[dict] = []
    real = events.emit_task

    async def spy(session_id, kind, **data):
        frames.append({"kind": kind, **data})
        await real(session_id, kind, **data)

    monkeypatch.setattr(events, "emit_task", spy)
    import kotoba.core.loop as loop
    monkeypatch.setattr(loop, "emit_task", spy)
    _inline(monkeypatch, tmp_path, sid, resp)
    return [f for f in frames if f["kind"] == "step" and f.get("phase") == "done"]


def test_the_row_for_an_expired_card_does_not_say_the_user_refused(monkeypatch, tmp_path):
    """The same defect one surface further out, on the two most-used gated tools: the audit trail
    learned to tell the four endings apart, but the ROW did not, because `confirm()` consumed the
    verdict only to pick an approver and dropped it. `shell`/`execute_code` call `_mark_no_execution`
    and never `interaction.note_no_run`, so `no_run_verdict` was always '' and `loop._refusal_line`
    rendered nothing — leaving one canned sentence, "I held off on that one, I didn't get the
    go-ahead", over a card that had merely timed out unread, or one that could never be drawn at all.

    Not an edge case: `cannot_block` is true only under `/v1`, so with `voice_mode=local` — the daily
    configuration — every turn takes this path."""
    expired = _inline_rows(monkeypatch, tmp_path, "row-expired", None)
    declined = _inline_rows(monkeypatch, tmp_path, "row-declined", {"approved": False})

    assert expired and declined
    assert "no answer on the card" in expired[-1]["result"]
    assert "you said no" in declined[-1]["result"]
    assert expired[-1]["result"] != declined[-1]["result"]
    assert expired[-1]["outcome"] == declined[-1]["outcome"] == "refused", "one mark, four sentences"


def test_a_card_that_expired_unread_is_not_audited_as_the_user_refusing(monkeypatch, tmp_path):
    """The measured row: `approved=0 approver="user"` for a card nobody ever answered."""
    decisions, sb = _inline(monkeypatch, tmp_path, "inline-silent", None)

    assert sb.ran == [], "nothing was approved, so nothing may have run"
    assert decisions[-1]["approver"] == "expired", decisions
    assert decisions[-1]["approved"] is False


def test_a_real_refusal_is_still_the_users_own(monkeypatch, tmp_path):
    decisions, sb = _inline(monkeypatch, tmp_path, "inline-no", {"approved": False})

    assert sb.ran == []
    assert decisions[-1]["approver"] == "user" and decisions[-1]["approved"] is False


def test_a_real_approval_is_still_the_users_own(monkeypatch, tmp_path):
    decisions, sb = _inline(monkeypatch, tmp_path, "inline-yes", {"approved": True})

    assert sb.ran == ["df -h"]
    assert decisions[-1]["approver"] == "user" and decisions[-1]["approved"] is True


def test_a_card_with_no_screen_is_audited_as_error_not_as_a_refusal(monkeypatch, tmp_path):
    decisions, sb = _inline(monkeypatch, tmp_path, "inline-blind", None, listening=False)

    assert sb.ran == []
    assert decisions[-1]["approver"] == "error"


def test_an_expired_row_is_distinguishable_from_a_refused_one(monkeypatch, tmp_path):
    """Both rows read `approved=0`. The trail is only worth reading if the two cannot be confused."""
    expired, _a = _inline(monkeypatch, tmp_path, "inline-x", None)
    refused, _b = _inline(monkeypatch, tmp_path, "inline-r", {"approved": False})

    assert expired[-1]["approver"] != refused[-1]["approver"]


def test_an_asker_that_answers_the_old_shapes_is_still_the_user(monkeypatch):
    """Every test double in the tree answers a bool or a 2/3-tuple: that still means the user decided."""
    rows: list[dict] = []

    async def go(answer):
        rows.clear()
        gate = ApprovalGate(host_exec=True, ask=lambda a, r, f: _said(answer), audit=_recorder(rows))
        return await gate.confirm("df -h", "exec")

    for answer in (False, True, (False, False), (True, False, True)):
        asyncio.run(go(answer))
        assert rows[-1]["approver"] == "user", answer


def test_an_asker_cannot_write_its_own_authority_into_the_trail(monkeypatch):
    """The fourth slot names one of the four ENDINGS, and the gate maps it. A string outside that
    vocabulary must not become an audit authority — the trail's words are the gate's, not the asker's."""
    rows: list[dict] = []
    gate = ApprovalGate(host_exec=True, ask=lambda a, r, f: _said((True, False, False, "allowlist")),
                        audit=_recorder(rows))

    assert asyncio.run(gate.confirm("df -h", "exec")) is True
    assert rows[-1]["approver"] == "user"


def _recorder(rows):
    async def audit(action, risk, ok, who, detail):
        rows.append({"action": action, "approved": ok, "approver": who, "detail": detail})

    return audit


def _said(value):
    async def _c():
        return value
    return _c()


# --- the vocabulary itself ---------------------------------------------------------------------------

def test_every_ending_has_exactly_one_authority():
    assert interaction.approver_for(interaction.APPROVED) == "user"
    assert interaction.approver_for(interaction.DECLINED) == "user"
    assert interaction.approver_for(interaction.UNANSWERED) == "expired"
    assert interaction.approver_for(interaction.UNREACHABLE) == "error"
    assert interaction.approver_for("") == "user"


def test_the_read_view_explains_an_expired_card(tmp_path):
    """A word nobody can read is not a fix — the view names what 'expired' means, and views are rebuilt
    on every startup, so the label reaches a database that migrated long ago."""
    import aiosqlite

    from kotoba.db.migrations import run_migrations

    async def go():
        async with aiosqlite.connect(tmp_path / "t.db") as conn:
            await run_migrations(conn)
            await conn.execute(
                "INSERT INTO audit_log (action, risk_kind, approved, approver, detail) "
                "VALUES ('df -h', 'exec', 0, 'expired', 'decision')"
            )
            await conn.commit()
            async with conn.execute("SELECT authority, regime FROM audit_log_read") as cur:
                return await cur.fetchone()

    authority, regime = asyncio.run(go())
    assert "expired" in authority and "nobody" in authority
    assert regime == "current"


# --- the typed box has the same three endings, and two of them were one -----------------------------

def _typed(answer) -> tuple[object, str]:
    """Drive the real request_input, answering the card with exactly what a surface would hand back."""
    async def go():
        events.register("typed-probe")
        card: dict = {}

        async def reply():
            await asyncio.sleep(0.01)
            rid = next(iter(interaction._pending["typed-probe"]))
            interaction.resolve("typed-probe", answer, rid)

        asyncio.get_running_loop().create_task(reply())
        got = await interaction.request_input("typed-probe", "Your postal code", timeout=5, card=card)
        events.unregister("typed-probe")
        return got, card["verdict"]

    return asyncio.run(go())


def test_an_empty_box_is_the_decision_they_made():
    assert _typed({"value": ""}) == ("", interaction.DECLINED)


def test_a_box_nobody_answered_is_not_a_refusal():
    """A closed stdin, a prompt that raised, a surface refusing on their behalf: nobody decided. It
    was folded into the empty box, so she told the model "the user said NO" about a card they never
    saw — the audit trail asserting a refusal that never happened."""
    got, verdict = _typed({"value": None})
    assert got is None
    assert verdict == interaction.UNANSWERED


def _terminal_approval(ask):
    from kotoba.cli.approvals import Approvals, Card

    async def go():
        q = events.register("eof-probe")
        approvals = Approvals("eof-probe", ask=ask)

        async def drain():
            while True:
                frame = await q.get()
                if frame.get("kind") == "need_input" and frame.get("mode") == "approval":
                    approvals.present(Card.from_frame(frame))

        d = asyncio.create_task(drain())
        card: dict = {}
        try:
            ok, _always = await interaction.request_approval(
                "eof-probe", "rm -rf build", timeout=5, channel="text", card=card)
        finally:
            d.cancel()
            events.unregister("eof-probe", q)
        return ok, card["verdict"], interaction.approver_for(card["verdict"])

    return asyncio.run(go())


def test_an_approval_card_nobody_could_answer_is_not_the_users_no():
    def stdin_closed(card):
        return None

    def prompt_raised(card):
        raise RuntimeError("renderer died")

    assert _terminal_approval(stdin_closed) == (False, interaction.UNANSWERED, "expired")
    assert _terminal_approval(prompt_raised) == (False, interaction.UNANSWERED, "expired")


def test_a_pressed_no_at_the_terminal_is_still_the_users_no():
    assert _terminal_approval(lambda card: (False, False, False)) == (False, interaction.DECLINED, "user")


def test_the_two_endings_do_not_say_the_same_thing_to_the_model():
    refused = interaction.refusal_note(interaction.DECLINED, "typing “X”")
    unheard = interaction.refusal_note(interaction.UNANSWERED, "typing “X”")
    assert "said NO" in refused
    assert "said NO" not in unheard and "Nobody decided anything" in unheard

