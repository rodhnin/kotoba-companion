"""The MCP approval card: the clock it runs on, and the row it leaves behind.

Measured live: three install cards expired mid-read while every shell card in the same session
waited comfortably. Both MCP tools asked for approval with no channel and no timeout, defaulting to
the 25-second voice window sized for the loop's own tool timeout — yet the MCP card is the one that
most needs reading: a third-party URL, a secrets line, and an attacker-written blurb. Worse, each
expiry was then reported as the user's own decision: the tool returned a soft refusal string, the
loop read it back as "cancelled by user", and it was marked a success because that string alone was
consulted. These drive the real loop and real tools over a fake model; the clock is measured at the
wait itself, and the outcome is read off the emitted frame, never off the tool's prose."""
from __future__ import annotations

import asyncio
import json

import pytest

import kotoba.core.mcp.registry_search as rs
import kotoba.tools.action.mcp_find as mcp_find
import kotoba.tools.action.mcp_install as mcp_install
from kotoba.core import events, interaction, sandbox, workspace
from kotoba.core.loop import TOOL_TIMEOUT


class _Item:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Event:
    def __init__(self, type, **kw):
        self.type = type
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
        self.responses = type("R", (), {"create": lambda _s, **kw: _made(_Stream(rest.pop(0)))})()


def _made(value):
    async def _c():
        return value
    return _c()


def _done(value):
    """An async stand-in that answers `value` — the FUNCTION, for monkeypatching an awaited call."""
    async def _c(*a, **k):
        return value
    return _c


class _DB:
    def __init__(self):
        self.audit: list[dict] = []

    async def list_approved_commands(self):
        return []

    async def insert_audit_log(self, **kw):
        self.audit.append(kw)


class _MCP:
    def __init__(self):
        self.server_tools: dict[str, list[str]] = {}
        self.connected: list[tuple] = []

    async def connect(self, name, cfg):
        self.connected.append((name, cfg))
        self.server_tools[name] = [f"{name}__get_current_time"]
        return self.server_tools[name]


class _Ctx:
    def __init__(self, sid, channel, mcp=None):
        self.session_id = sid
        self.channel = channel
        self.mcp = mcp or _MCP()
        self.call_id = "c1"
        self.db = None


def _cand(env=None):
    return rs.Candidate("io.github.acme/time-mcp", "Tells the time.", "https://gh/t", "1.0",
                        "npm", {"command": "npx", "args": ["-y", "time-mcp@1.0"]}, env=env or [])


def _windows(monkeypatch, resp):
    """Replace the WAIT, not the asker: request_approval, its verdict and the channel plumbing stay real.
    Returns the list of windows (in seconds) the cards were actually given."""
    seen: list[float] = []

    async def _wait(session_id, request_id, fut, timeout):
        seen.append(timeout)
        (interaction._pending.get(session_id) or {}).pop(request_id, None)
        return resp

    monkeypatch.setattr(interaction, "_await_response", _wait)
    return seen


async def _listening(sid, coro):
    queue = events.register(sid)
    try:
        return await coro
    finally:
        events.unregister(sid, queue)


def _ask_find(monkeypatch, sid, channel, resp=None):
    monkeypatch.setattr(mcp_find.registry_search, "search_registry", _done([_cand()]))
    monkeypatch.setattr(mcp_find.registry_search, "vet", lambda cs, *a, **k: cs)
    monkeypatch.setattr(mcp_find.config, "save_server", lambda *a, **k: None)
    seen = _windows(monkeypatch, resp)
    ctx = _Ctx(sid, channel)
    out = asyncio.run(_listening(sid, mcp_find.execute({"query": "time"}, ctx)))
    return seen, out, ctx


def _ask_install(monkeypatch, sid, channel, resp=None):
    monkeypatch.setattr(mcp_install, "_resolve",
                        lambda args: ("time", {"command": "npx", "args": ["-y", "time-mcp@1.0"]}))
    seen = _windows(monkeypatch, resp)
    ctx = _Ctx(sid, channel)
    out = asyncio.run(_listening(sid, mcp_install.execute({"name": "time"}, ctx)))
    return seen, out, ctx


@pytest.mark.parametrize("ask", [_ask_find, _ask_install])
@pytest.mark.parametrize("channel,expected", [("text", 180.0), ("voice", 25.0)])
def test_an_mcp_card_runs_on_the_clock_of_the_channel_it_is_shown_on(monkeypatch, ask, channel, expected):
    """Neither tool passed `channel`, so both took the voice window on every channel — including a
    typed turn where a `shell` card, asked through the same module, correctly got 180 s."""
    seen, _out, _ctx = ask(monkeypatch, f"clock-{ask.__name__}-{channel}", channel)

    assert seen == [expected]
    assert interaction.approval_timeout(channel) == expected


@pytest.mark.parametrize("ask", [_ask_find, _ask_install])
def test_the_voice_window_still_resolves_before_the_loop_can_cancel_the_tool(monkeypatch, ask):
    """The half of the old behaviour that was RIGHT and must survive: on voice the card has to close
    itself while the loop is still waiting on the tool, so a no-answer is an in-character deny and not a
    compute-cancel that abandons the card mid-wait."""
    seen, _out, _ctx = ask(monkeypatch, f"bound-{ask.__name__}", "voice")

    assert seen and seen[0] < TOOL_TIMEOUT
    assert interaction.approval_timeout(None) == interaction.VOICE_APPROVAL_TIMEOUT


@pytest.mark.parametrize("ask", [_ask_find, _ask_install])
def test_a_card_nobody_answered_is_not_reported_as_the_user_declining(monkeypatch, ask):
    """The lie itself: (False, False) means deny AND timeout, so both tools said "I held off" — her
    holding off is a decision, and nobody made one. The model must be told the truth it will relay."""
    _seen, out, ctx = ask(monkeypatch, f"silent-{ask.__name__}", "text", resp=None)

    assert "timed out on screen" in out and "ask again" in out
    for lie in ("said NO", "held off", "declined", "refused"):
        assert lie not in out, f"a card nobody answered was reported as a decision: {out!r}"
    assert interaction.no_run_verdict(ctx, "c1") == interaction.UNANSWERED


@pytest.mark.parametrize("ask", [_ask_find, _ask_install])
def test_an_answered_no_is_still_reported_as_a_no(monkeypatch, ask):
    """And the other half — telling the two apart must not blur the one the user really did make."""
    _seen, out, ctx = ask(monkeypatch, f"no-{ask.__name__}", "text", resp={"approved": False})

    assert "said NO" in out and "did not happen" in out
    assert interaction.no_run_verdict(ctx, "c1") == interaction.DECLINED


def test_a_card_that_could_not_be_shown_says_so(monkeypatch):
    """The fourth ending. With no listener the frame is dropped, so the card was never drawn — reporting
    that as a refusal blames the user for a screen they were never shown."""
    monkeypatch.setattr(mcp_find.registry_search, "search_registry", _done([_cand()]))
    monkeypatch.setattr(mcp_find.registry_search, "vet", lambda cs, *a, **k: cs)
    ctx = _Ctx("nobody-listening", "text")

    out = asyncio.run(mcp_find.execute({"query": "time"}, ctx))

    assert "could not ask" in out
    assert interaction.no_run_verdict(ctx, "c1") == interaction.UNREACHABLE


def test_a_secret_box_that_came_back_empty_installed_nothing(monkeypatch):
    """Approved, then the masked box went unanswered: nothing was connected, so the row must not claim
    the tool ran. This ending had no witness at all — it reached the screen as a green ✓ while the box
    was still waiting to be filled in."""
    monkeypatch.setattr(mcp_find.registry_search, "search_registry",
                        _done([_cand(env=[rs.EnvVar("TOKEN", "a token", True, True)])]))
    monkeypatch.setattr(mcp_find.registry_search, "vet", lambda cs, *a, **k: cs)
    _windows(monkeypatch, {"approved": True})
    ctx = _Ctx("secret-box", "text")

    out = asyncio.run(_listening("secret-box", mcp_find.execute({"query": "time"}, ctx)))

    # The sentence used to be the same for all four endings. It now names the one that happened, and
    # what this test guards is unchanged: nothing was installed and the witness says why.
    assert "expired with no answer" in out and "not their choice" in out
    assert ctx.mcp.connected == []
    assert interaction.no_run_verdict(ctx, "c1") == interaction.UNANSWERED


# --- the row, through the real loop -----------------------------------------------------------------

_SAID = [_Event("response.output_text.delta", delta="[thoughtfully] Te cuento cómo fue.")]


def _find_call():
    return [_Event("response.output_item.done",
                   item=_Item(type="function_call", name="mcp_find", call_id="c1",
                              arguments=json.dumps({"query": "time"})))]


def _turn(monkeypatch, tmp_path, sid, resp):
    """One real work turn: the model calls mcp_find, the card is answered (or not), she talks. Returns
    the done frame, what the MODEL was handed, the audit rows and the fake server."""
    import kotoba.core.loop as loop

    monkeypatch.setattr(sandbox, "backend_name", lambda: "local")
    monkeypatch.setattr(workspace, "resolve_workdir", lambda _sid: tmp_path)
    monkeypatch.setattr(mcp_find.registry_search, "search_registry", _done([_cand()]))
    monkeypatch.setattr(mcp_find.registry_search, "vet", lambda cs, *a, **k: cs)
    monkeypatch.setattr(mcp_find.config, "save_server", lambda *a, **k: None)
    _windows(monkeypatch, resp)
    monkeypatch.setattr(loop, "get_client", lambda: _Client([_find_call(), _SAID]))

    db, mcp = _DB(), _MCP()
    items = [{"role": "user", "content": "instálame el servidor de la hora"}]

    async def go():
        queue = events.register(sid)
        try:
            await loop.agentic_loop(items, sid, db, asyncio.Queue(), {}, max_iterations=2,
                                    mode="work", channel="text", mcp=mcp)
        finally:
            events.unregister(sid, queue)
        return [queue.get_nowait() for _ in range(queue.qsize())]

    done = [f for f in asyncio.run(go()) if f.get("kind") == "step" and f.get("phase") == "done"]
    assert len(done) == 1, done
    told = next(i["output"] for i in items
                if isinstance(i, dict) and i.get("type") == "function_call_output")
    return done[0], told, db.audit, mcp


def test_a_declined_install_never_paints_a_green_check(monkeypatch, tmp_path):
    """As it was read off the live DOM: the mark ✓ "ran and finished cleanly" over the detail
    "cancelled by user". `ok` stays True on purpose — the tool did answer; it was never the right
    question."""
    frame, _told, _audit, mcp = _turn(monkeypatch, tmp_path, "row-mcp-no", {"approved": False})

    assert frame["ok"] is True
    assert frame["outcome"] == "refused"
    assert frame["result"] == "  you said no — it never ran"
    assert mcp.connected == []


def test_an_unanswered_card_is_never_drawn_as_the_users_decision(monkeypatch, tmp_path):
    """Same ⊘ (nothing ran is nothing ran), different sentence. `unknown` would be the second lie: the
    expiry is precisely witnessed, so "nothing said how" is not true either."""
    frame, told, _audit, mcp = _turn(monkeypatch, tmp_path, "row-mcp-silence", None)

    assert frame["outcome"] == "refused"
    assert frame["result"] == "  no answer on the card — it never ran"
    assert "cancelled by user" not in frame["result"] and "you said no" not in frame["result"]
    assert "timed out on screen" in told
    assert mcp.connected == []


def test_a_refused_install_leaves_no_executed_row_in_the_audit_trail(monkeypatch, tmp_path):
    """The third thing the missing witness cost. The loop writes "executed" for every action tool it does
    not know ran nothing, so the trail recorded an install the user had just declined."""
    _frame, _told, audit, _mcp = _turn(monkeypatch, tmp_path, "row-mcp-audit", {"approved": False})

    assert [r for r in audit if "executed" in str(r.get("detail"))] == []


def test_an_approved_install_still_lands_as_done(monkeypatch, tmp_path):
    """The ✓ has to keep meaning something: approved, connected, tools exposed."""
    frame, told, audit, mcp = _turn(monkeypatch, tmp_path, "row-mcp-yes", {"approved": True})

    assert frame["outcome"] == "ok"
    assert [n for n, _cfg in mcp.connected] == ["time-mcp"]
    assert "get_current_time" in told
    assert [r for r in audit if r.get("detail") == "executed"], "a real install belongs on the trail"


def test_the_gate_lane_keeps_its_own_witness_and_its_own_words():
    """Regression for shell/execute_code, which reach the same mark down the other path: their inline
    denial is witnessed by core.deferred_exec and its row still prints the sentence SHE said, which is
    what the CLI renderer and the terminal panel have always shown there."""
    from kotoba.core import deferred_exec
    from kotoba.core.loop import _refusal_line, _step_outcome
    from kotoba.tools import ToolContext

    ctx = ToolContext(db=None)
    deferred_exec._mark_no_execution(ctx, "c9")

    assert _step_outcome(ctx, "c9", True, False, []) == "refused"
    assert _refusal_line(ctx, "c9") == ""


@pytest.mark.parametrize("verdict", [interaction.DECLINED, interaction.UNANSWERED, interaction.UNREACHABLE])
def test_every_ending_at_a_card_has_a_mark_the_row_can_draw(verdict):
    """One mark for one question — did it run — and three sentences for the three ways of answering no.
    A word outside the agreed vocabulary would reach the panel as "?", which claims nothing witnessed
    the ending, and all three of these were witnessed exactly."""
    from kotoba.core.loop import _refusal_line, _step_outcome
    from kotoba.tools import ToolContext

    ctx = ToolContext(db=None, call_id="c1")
    interaction.note_no_run(ctx, verdict)

    assert _step_outcome(ctx, "c1", True, False, []) == "refused"
    assert _refusal_line(ctx, "c1").strip().endswith("it never ran")
    assert _step_outcome(ctx, "other-call", True, False, []) == "ok", "a witness answers for ONE call"
