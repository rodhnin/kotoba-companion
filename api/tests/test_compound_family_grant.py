"""Pressing `a` on a COMPOUND command persisted a family that is a lie.

`command_family()` is the first token, so the card over a compound command read "always allow npm";
saving it let bare `npm publish` and `npm install anything` run unprompted, while the metachar guard
in matching meant the saved row could never match the compound itself again — the grant covered
everything EXCEPT what the person was looking at.

`persistable()` is now the single source of truth: the gate refuses to save a derived family off a
command with shell metacharacters, and `request_approval` computes `can_always` off the same rule, so
no card offers a key the backend will refuse."""
from __future__ import annotations

import asyncio

from kotoba.core import events, interaction
from kotoba.core.approval import ApprovalGate, persistable

COMPOUND = "npm run build && ./deploy.sh"
SCREENSHOT = "pwd && ls -la && find . -maxdepth 2 -name 'israel_news_comparison*'"


def _gate(tmp_path, **kw) -> ApprovalGate:
    return ApprovalGate(host_exec=True, workspace_root=tmp_path, **kw)


def _always_yes(tmp_path) -> tuple[ApprovalGate, list[str]]:
    saved: list[str] = []

    async def persist(fam: str) -> None:
        saved.append(fam)

    async def ask(action: str, risk: str, fam):
        return (True, True)

    return _gate(tmp_path, ask=ask, on_persist=persist), saved


def test_persistable_refuses_compounds_and_keeps_the_grants_that_are_real():
    assert persistable("npm run build") is True
    assert persistable(COMPOUND) is False
    assert persistable(SCREENSHOT) is False
    assert persistable(COMPOUND, "npm") is False
    assert persistable("run Python:\nprint(42)", "execute_code") is True
    assert persistable("rm -rf /tmp/x") is False
    assert persistable("Install the “notion” MCP server and connect it?", "") is False


def test_an_always_yes_on_a_compound_persists_nothing_and_npm_publish_still_asks(tmp_path):
    gate, saved = _always_yes(tmp_path)
    assert asyncio.run(gate.confirm(COMPOUND, "exec")) is True
    assert saved == [] and not gate.is_saved("npm")
    assert gate.would_auto_allow("npm publish", "exec") is False
    assert gate.would_auto_allow("npm install some-package", "exec") is False


def test_the_deferred_grant_path_refuses_the_same_compound(tmp_path):
    gate = _gate(tmp_path)
    asyncio.run(gate.persist_always(COMPOUND))
    assert not gate.is_saved("npm")
    asyncio.run(gate.persist_always(SCREENSHOT))
    assert not gate.is_saved("pwd")


def test_a_simple_command_still_persists_and_the_grant_still_means_its_family(tmp_path):
    gate, saved = _always_yes(tmp_path)
    assert asyncio.run(gate.confirm("npm run build", "exec")) is True
    assert saved == ["npm"]
    assert gate.would_auto_allow("npm test", "exec") is True


def test_an_explicit_family_keeps_persisting_across_its_actions_newlines(tmp_path):
    gate = _gate(tmp_path)
    asyncio.run(gate.persist_always("run Python:\nprint(42)", "execute_code"))
    assert gate.is_saved("execute_code")


def test_the_wire_stops_offering_always_on_a_compound():
    """Both shapes the wire really sees: the deferred path passes family=None, the agentic loop passes
    the family the gate already resolved — the frame must refuse `a` in both."""

    async def go(action, **kw):
        q = events.register("compound-card")
        try:
            await interaction.request_approval("compound-card", action, timeout=0.05, **kw)
            frames = [q.get_nowait() for _ in range(q.qsize())]
        finally:
            events.unregister("compound-card")
        return next(f for f in frames if f.get("kind") == "need_input" and f.get("mode") == "approval")

    derived = asyncio.run(go(COMPOUND))
    assert derived["family"] == "npm" and derived["can_always"] is False
    resolved = asyncio.run(go(COMPOUND, family="npm"))
    assert resolved["can_always"] is False
    simple = asyncio.run(go("npm run build"))
    assert simple["can_always"] is True
    code = asyncio.run(go("run Python:\nprint(42)", family="execute_code"))
    assert code["can_always"] is True
