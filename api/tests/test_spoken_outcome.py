"""What she SAYS OUT LOUD when a tool call ends — found by live QA, filed as a launch blocker.

The terminal row moved onto its own witness while the voice stayed on `ok`, which answers whether the
tool handed back usable text for the model, not what she should say. A command that exited 2, a
declined action, and an unanswered card all spoke "Done! Here's what came back:"; a declined MCP
install spoke "All set — I've got those tools now!" under a row reading "you said no — it never ran".

These assert on the queue that becomes TTS audio, never on the already-correct row. The canned layer is
now reached only at `reasoning_effort=off`, which is what the fixture pins — a stock install suppresses
it, so what is under test here is the escape hatch, not the default path.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from conftest import shell_that

import kotoba.tools.action.mcp_install as mcp_install
import kotoba.tools.action.shell as shell
from contextlib import contextmanager

from kotoba.core import deferred_exec, events, interaction, sandbox, transport, workspace
from kotoba.core.llm import is_reasoning_model
from kotoba.core.loop import _after_line


@pytest.fixture(autouse=True)
def _a_stock_clone(monkeypatch):
    """Pin the one setting under which these lines are spoken at all, rather than stubbing the gate.

    Deleting the variable is not enough and used to be actively misleading: any earlier test that boots
    the app runs `load_dotenv()`, which puts the developer's own effort into os.environ for the rest of
    the session — measured, these tests passed alone and passed VACUOUSLY in a full run, having narrated
    nothing. `off` is unambiguous either way."""
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "off")
    monkeypatch.setenv("KOTOBA_WORK_REASONING_EFFORT", "off")


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


class _DB:
    async def list_approved_commands(self):
        return []

    async def insert_audit_log(self, **kw):
        return None


class _MCP:
    def __init__(self):
        self.server_tools: dict[str, list[str]] = {}
        self.connected: list[tuple] = []

    async def connect(self, name, cfg):
        self.connected.append((name, cfg))
        self.server_tools[name] = [f"{name}__do_thing"]
        return self.server_tools[name]


_SAID = [_Event("response.output_text.delta", delta="[happily] Ya está.")]


@contextmanager
def _transport_of(marker):
    """A row asking for the voice path is asking for the ElevenLabs one — that transport is what makes
    a gated command defer to a card instead of blocking on it."""
    if marker is True or marker == "voice":
        with transport.el_call_turn():
            yield
    else:
        yield


def _call(tool: str, args: dict):
    return [_Event("response.output_item.done",
                   item=_Item(type="function_call", name=tool, call_id="c1",
                              arguments=json.dumps(args)))]


def _turn(monkeypatch, tmp_path, sid: str, tool: str, args: dict, *, approved: bool = True,
          channel: str = "text", mode: str = "companion", mcp=None,
          el_agent: bool | None = None) -> tuple[str, str]:
    """One real turn. Returns (what the row says, what she says) — the two halves that disagreed.

    The card is answered by replacing the WAIT, never the asker, so `request_approval` writes the real
    verdict and the refusal witness core.loop reads is the production one."""
    import kotoba.core.loop as loop

    assert not is_reasoning_model(role=mode), (
        "the canned narration is suppressed in this environment — this measurement would be vacuous"
    )

    async def _wait(session_id, request_id, fut, timeout):
        (interaction._pending.get(session_id) or {}).pop(request_id, None)
        return {"approved": approved}

    monkeypatch.setattr(sandbox, "backend_name", lambda: "local")
    monkeypatch.setattr(workspace, "resolve_workdir", lambda _sid: tmp_path)
    monkeypatch.setattr(interaction, "_await_response", _wait)
    monkeypatch.setattr(loop, "get_client", lambda: _Client([_call(tool, args), _SAID]))

    spoken: asyncio.Queue = asyncio.Queue()

    async def go():
        queue = events.register(sid)
        try:
            with _transport_of(channel if el_agent is None else el_agent):
                await loop.agentic_loop([{"role": "user", "content": "hazlo"}], sid, _DB(), spoken, {},
                                        max_iterations=2, mode=mode, channel=channel, mcp=mcp)
        finally:
            events.unregister(sid, queue)
            deferred_exec.cancel(sid)
            deferred_exec.forget_session(sid)
        return [queue.get_nowait() for _ in range(queue.qsize())]

    frames = asyncio.run(go())
    done = [f for f in frames if f.get("kind") == "step" and f.get("phase") == "done"]
    assert len(done) == 1, done
    # Only the TEXT is what she says: the queue also carries stream.FLUSH_SENTINEL, an object that
    # tells a consumer to let go of what its filters hold and reaches no wire.
    items = [spoken.get_nowait() for _ in range(spoken.qsize())]
    said = "".join(i for i in items if isinstance(i, str))
    return done[0]["outcome"], said


def test_a_command_that_failed_is_never_announced_as_done(monkeypatch, tmp_path):
    """First row of the reproduction: exit 2, `failed` on screen, “Done! Here's what came back:” in her
    mouth. `ok` is True here on purpose — the tool DID answer, so the old signal could not have known any
    better; the row has said so since an earlier QA round fixed it, and now the voice does too."""
    outcome, said = _turn(monkeypatch, tmp_path, "say-failed", "shell", {"command": shell_that("fails", code=2)})

    assert outcome == "failed"
    assert shell.COMPLETE not in said, f"she announced a failed command as done: {said!r}"
    assert shell.FAIL in said


def test_an_action_the_user_declined_is_never_announced_as_done(monkeypatch, tmp_path):
    """Second row. Nothing ran, and the row knew it — she still said it had come back."""
    outcome, said = _turn(monkeypatch, tmp_path, "say-refused", "shell",
                          {"command": "rm -rf build"}, approved=False)

    assert outcome == "refused"
    assert shell.COMPLETE not in said, f"she announced a refused action as done: {said!r}"
    assert (tmp_path / "build").exists() is False


def test_an_action_the_user_declined_is_not_spoken_of_as_a_failure_either(monkeypatch, tmp_path):
    """Their decision is not a stumble of hers. “That command didn't go through — want me to try
    another way?” answers a No by offering to go around it, which is the other half of the same lie."""
    _outcome, said = _turn(monkeypatch, tmp_path, "say-refused-not-fail", "shell",
                           {"command": "rm -rf build"}, approved=False)

    assert shell.FAIL not in said, f"she blamed the tool for the user's own decision: {said!r}"


def test_a_deferred_action_says_nothing_until_something_has_happened(monkeypatch, tmp_path):
    """Third row: the card is up on the voice path and NOTHING has run. Both canned lines are false
    here, and the truthful one is already spoken — the tool hands the model its own first-person
    sentence about having asked on screen, and the model relays it in the user's language."""
    outcome, said = _turn(monkeypatch, tmp_path, "say-pending", "shell",
                          {"command": shell_that("touches", marker="ran-anyway")}, channel="voice")

    assert outcome == "pending"
    assert shell.COMPLETE not in said and shell.FAIL not in said, said
    assert said.strip().startswith(shell.ANNOUNCE), "the announcement before it runs is untouched"


def test_a_declined_mcp_install_never_says_it_has_the_tools(monkeypatch, tmp_path):
    """The worst of the four, and the one the toolset guard misses: `mcp_install`'s TOOLSET is "mcp",
    not "mcp:<server>", so the suppression that covers an MCP server's own tools does not cover it.
    Row: “you said no — it never ran”. Voice: “All set — I've got those tools now!”"""
    monkeypatch.setattr(mcp_install, "_resolve",
                        lambda args: ("time", {"command": "npx", "args": ["-y", "time-mcp@1.0"]}))
    mcp = _MCP()

    outcome, said = _turn(monkeypatch, tmp_path, "say-mcp-no", "mcp_install", {"name": "time"},
                          approved=False, mode="work", mcp=mcp)

    assert outcome == "refused"
    assert mcp.connected == []
    assert mcp_install.COMPLETE not in said, f"she claimed tools she was refused: {said!r}"
    assert mcp_install.FAIL not in said, "nothing failed — they said no"


def test_an_approved_install_still_says_it_has_the_tools(monkeypatch, tmp_path):
    """The confirmation has to keep meaning something: approved, connected, and said out loud."""
    monkeypatch.setattr(mcp_install, "_resolve",
                        lambda args: ("time", {"command": "npx", "args": ["-y", "time-mcp@1.0"]}))
    mcp = _MCP()

    outcome, said = _turn(monkeypatch, tmp_path, "say-mcp-yes", "mcp_install", {"name": "time"},
                          mode="work", mcp=mcp)

    assert outcome == "ok"
    assert [n for n, _cfg in mcp.connected] == ["time"]
    assert mcp_install.COMPLETE in said


def test_a_command_that_worked_is_still_announced_as_done(monkeypatch, tmp_path):
    """The regression that matters most: silencing the lie must not silence the truth. A clean run is
    still confirmed out loud, which is what the canned narration exists for on a non-reasoning model."""
    outcome, said = _turn(monkeypatch, tmp_path, "say-ok", "shell", {"command": shell_that("prints")})

    assert outcome == "ok"
    assert shell.ANNOUNCE in said and shell.COMPLETE in said


@pytest.mark.parametrize("outcome,expected", [
    ("ok", "AFTER"),
    ("failed", "FAIL"),
    ("refused", ""),        # they said no / the card expired / nobody could be asked / the tool declined
    ("pending", ""),        # a card is on screen and nothing has happened yet
    ("interrupted", ""),    # the turn was cut mid-command
    ("unknown", ""),        # an ending nothing could name is not one to claim either
])
def test_the_spoken_line_follows_the_witness_not_the_return_value(outcome, expected):
    """The vocabulary is `_step_outcome`'s, and every word outside `ok`/`failed` is silence — including
    one nobody has invented yet, which must never fall through to a claim of success."""
    assert _after_line({"after": "AFTER", "fail": "FAIL"}, outcome) == expected


def test_a_fresh_clone_does_not_hear_these_lines(monkeypatch):
    """The blocker this file was opened for, pinned from the other side.

    These lines are English, and a stock install answers in the user's own language, so hearing them
    was the defect: a Spanish spoken turn interrupted by "Let me put together a little report for you."
    The default effort is what closes it, and `off` is what reopens it on purpose."""
    monkeypatch.delenv("KOTOBA_REASONING_EFFORT", raising=False)
    assert is_reasoning_model() is True

    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "off")

    assert is_reasoning_model() is False


def test_the_two_channels_cannot_drift_apart_again():
    """The defect in one line: the row and the voice read one value now. This pins that the loop still
    computes exactly one outcome per call and feeds both from it.

    Read off `_iterate`, which is the loop body itself: `_run_iterations` is now the door around it that
    witnesses a subagent run dying in there."""
    import inspect

    import kotoba.core.loop as loop

    src = inspect.getsource(loop._iterate)

    assert src.count("outcome = _step_outcome(") == 1
    assert "_after_line(_voice_for(tc.name, soul_patterns), outcome)" in src
    assert 'patt.get("after", "") if ok else' not in src
