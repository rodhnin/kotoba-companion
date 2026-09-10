"""The third outcome state, applied to the tools in this scope: `ok` · `failed` · `refused`.

`refused` means NOTHING RAN: no audit row, no reason given. `failed` means it RAN AND DID NOT WORK:
the row survives, since the attempt may have left traces someone has to find. Three endings were
wrongly graded `refused`: remember_image on any save failure, and mcp_find/mcp_install when a server
was found, spawned, even persisted, but proved useless.

One ending stays `ok` on purpose: `cronjob remove` with an id matching nothing ran a lookup and
returned a true, usable answer — grading it failed would narrate cronjob's one FAIL line, written
for `set` and nonsense after a remove."""
from __future__ import annotations

import asyncio

import pytest

from kotoba.core import interaction
from kotoba.core.loop import _nothing_ran, _step_outcome, execute_with_heartbeat
from kotoba.core.mcp import registry_search
from kotoba.tools import ToolContext
import kotoba.tools.action.cronjob as cronjob
import kotoba.tools.builtin.remember_image as remember_image


def _ctx(**kw):
    kw.setdefault("db", None)
    ctx = ToolContext(session_id="outcomes", mode="work", **kw)
    ctx.call_id = "c1"
    return ctx


async def _graded(tool: str, args: dict, ctx):
    """(ok, outcome, text) exactly as the loop computes them for one call."""
    ok, text = await execute_with_heartbeat(tool, args, asyncio.Queue(), {}, ctx)
    return ok, _step_outcome(ctx, ctx.call_id, ok, False, []), str(text)


class FakeMCP:
    """An MCP manager that connects and hands back nothing usable."""

    def __init__(self, tools=None, raises=None):
        self.server_tools = {}
        self._tools = tools if tools is not None else []
        self._raises = raises
        self.disconnected: list[str] = []

    async def connect(self, name, cfg):
        if self._raises is not None:
            raise self._raises
        return list(self._tools)

    async def disconnect(self, name):
        self.disconnected.append(name)


class FakeDB:
    def __init__(self, jobs=()):
        self._jobs = list(jobs)

    async def list_cronjobs(self):
        return list(self._jobs)

    async def deactivate_cronjob(self, jid):
        self._jobs = [j for j in self._jobs if j["id"] != jid]


@pytest.fixture
def approved(monkeypatch):
    async def _approve(ctx, text, **kw):
        return interaction.APPROVED

    monkeypatch.setattr(interaction, "ask_approval", _approve)


@pytest.fixture
def no_servers(monkeypatch):
    async def _none(query, limit=5):
        return []

    monkeypatch.setattr(registry_search, "search_registry", _none)
    monkeypatch.setattr(registry_search, "search_fallback", _none)


def _candidate():
    return registry_search.Candidate(
        name="io.example/toaster-mcp", description="Drive a toaster.",
        repo_url="https://example.invalid/toaster", version="1.0.0", kind="npm",
        cfg={"command": "npx", "args": ["-y", "toaster-mcp"]},
    )


@pytest.fixture
def one_server(monkeypatch):
    async def _one(query, limit=5):
        return [_candidate()]

    async def _none(query, limit=4):
        return []

    monkeypatch.setattr(registry_search, "search_registry", _one)
    monkeypatch.setattr(registry_search, "search_fallback", _none)
    monkeypatch.setattr(registry_search, "vet", lambda cands, query: list(cands))


# ── remember_image: a save that was attempted and did not land ────────────────────────────────

def test_an_image_that_could_not_be_stored_ran_and_failed():
    ctx = _ctx()
    text = asyncio.run(remember_image._not_saved("Fulano", "", "person", ctx))

    assert "NOTHING WAS SAVED" in text
    assert _nothing_ran(ctx, "c1") is False, (
        "a store that was tried and refused the bytes is not 'nothing ran' — the row has to survive, "
        "the fetch and the half-written file are traces")


def test_the_image_failure_reaches_the_loop_as_failed(monkeypatch):
    """Through the real door, so the witness is proven to land in execute_with_heartbeat's box."""
    async def _execute(args, ctx):
        return await remember_image._not_saved("Fulano", "", "person", ctx)

    monkeypatch.setattr(remember_image, "execute", _execute)
    ok, outcome, text = asyncio.run(_graded("remember_image", {"about": "Fulano"}, _ctx()))

    assert ok is False and outcome == "failed", f"graded {outcome!r}"
    assert "NOTHING WAS SAVED" in text, "the model still has to be told what happened"


def test_an_image_already_kept_is_still_a_success(monkeypatch):
    """The one None that means 'it IS kept' must keep its ✓ — the fix must not blanket-fail add()."""
    from kotoba.core import visual_memory

    monkeypatch.setattr(visual_memory, "duplicate_of", lambda *a: {"id": "x", "note": "at the park"})
    monkeypatch.setattr(visual_memory, "has_image", lambda e: True)
    ctx = _ctx()
    text = asyncio.run(remember_image._not_saved("Fulano", "", "person", ctx))

    assert "ALREADY keep" in text
    assert _nothing_ran(ctx, "c1") is False
    assert _step_outcome(ctx, "c1", True, False, []) == "ok"


# ── mcp_find: the two endings its own docstring names ─────────────────────────────────────────

def test_finding_no_server_at_all_is_a_failure(no_servers):
    ok, outcome, text = asyncio.run(_graded("mcp_find", {"query": "drive my toaster"},
                                            _ctx(mcp=FakeMCP())))

    assert "couldn't find a server" in text
    assert ok is False and outcome == "failed", (
        f"an INTERACTIVE tool graded its own non-connection a success: {outcome!r}")


def test_a_server_that_will_not_start_is_a_failure(one_server, approved):
    mcp = FakeMCP(raises=RuntimeError("npx exited 1"))
    ok, outcome, text = asyncio.run(_graded("mcp_find", {"query": "drive my toaster"}, _ctx(mcp=mcp)))

    assert "couldn't get it running" in text
    assert ok is False and outcome == "failed", f"graded {outcome!r}"


def test_the_user_saying_no_is_still_a_refusal_not_a_failure(one_server, monkeypatch):
    """The line the fix must not cross: a human declining is not the action going wrong, and painting
    their decision red says it was."""
    async def _decline(ctx, text, **kw):
        interaction.note_no_run(ctx, interaction.DECLINED)  # what the real ask_approval leaves
        return interaction.DECLINED

    monkeypatch.setattr(interaction, "ask_approval", _decline)
    ctx = _ctx(mcp=FakeMCP())
    ok, outcome, _ = asyncio.run(_graded("mcp_find", {"query": "drive my toaster"}, ctx))

    assert outcome == "refused", f"a declined install must stay a refusal, got {outcome!r}"


def test_an_already_connected_server_is_a_real_success(monkeypatch):
    mcp = FakeMCP()
    mcp.server_tools = {"toaster": ["toaster__toast"]}
    ok, outcome, text = asyncio.run(_graded("mcp_find", {"query": "toaster"}, _ctx(mcp=mcp)))

    assert "already have" in text
    assert ok is True and outcome == "ok"


# ── mcp_install: connected, and useless ───────────────────────────────────────────────────────

def test_a_server_that_offers_no_tools_is_a_failure(approved, monkeypatch, tmp_path):
    monkeypatch.setattr("kotoba.core.mcp.config.save_server", lambda name, cfg: None)
    ok, outcome, text = asyncio.run(_graded("mcp_install", {"name": "filesystem"},
                                            _ctx(mcp=FakeMCP(tools=[]))))

    assert "didn't offer any tools" in text
    assert ok is False and outcome == "failed", (
        f"a spawned, persisted, useless server was reported as an install: {outcome!r}")


def test_a_server_that_offers_tools_is_untouched(approved, monkeypatch):
    monkeypatch.setattr("kotoba.core.mcp.config.save_server", lambda name, cfg: None)
    ok, outcome, text = asyncio.run(_graded("mcp_install", {"name": "filesystem"},
                                            _ctx(mcp=FakeMCP(tools=["filesystem__read"]))))

    assert ok is True and outcome == "ok" and "I can now" in text


# ── cronjob: the one that stays `ok`, deliberately ────────────────────────────────────────────

def test_asking_to_cancel_a_reminder_that_is_not_there_is_an_answer_not_a_failure():
    """DECIDED, not overlooked. Nothing was attempted, so there is no trace for a row to preserve; the
    lookup ran and handed the model a true, usable sentence, which is what `ok` means. Same shape as
    `list` answering "You don't have any reminders set." Grading it failed would also narrate cronjob's
    only FAIL line — "I couldn't set that reminder — when did you want it?" — over a remove."""
    ok, outcome, text = asyncio.run(
        _graded("cronjob", {"action": "remove", "id": "deadbe"}, _ctx(db=FakeDB())))

    assert "couldn't find a reminder with that id" in text
    assert ok is True and outcome == "ok"
    assert cronjob.FAIL == "I couldn't set that reminder — when did you want it?", (
        "if cronjob ever gets a per-action fail line, revisit the verdict above")


def test_cancelling_a_reminder_that_is_there_still_works():
    db = FakeDB([{"id": "deadbeef01", "message": "call mamá"}])
    ok, outcome, text = asyncio.run(
        _graded("cronjob", {"action": "remove", "id": "deadbe"}, _ctx(db=db)))

    assert "Cancelled: call mamá." in text and ok is True and outcome == "ok"
