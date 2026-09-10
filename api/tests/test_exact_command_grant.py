"""Live QA: the card over a shell compound offered no "always allow", and she claimed it did.

Two rules rightly withhold the family grant: a derived family off a compound, and a derived
interpreter/exec-wrapper family. Missing was a narrower grant — this exact line, again, without
asking. An exact grant is a set of one: the family key is a lossy summary of the command (why the
metachar guard exists), but equality on the whole line has no summary to lose, so the guard's reason
does not reach it. It buys nothing else — no prefix, no glob, no whitespace equivalence — and a
dangerous command still vetoes. The negative half is load-bearing: a dangerous command, an
interpreter family, and a compound must stay unsavable the old way, and an exact grant must never
widen into a family one."""
from __future__ import annotations

import asyncio

import pytest

from kotoba.core import events, interaction
from kotoba.core.approval import (
    ApprovalGate, always_note, command_family, persistable, persistable_exact,
)

# What he typed at, verbatim: she wrapped it, the log shows both shapes, and neither was persistable.
WRAPPED = "sh -c \"sleep 15 && echo 'terminado a los quince'\""
BARE = "sleep 16 && echo 'terminado a los dieciseis'"
COMPOUND = "npm run build && ./deploy.sh"


def _gate(tmp_path, **kw) -> ApprovalGate:
    return ApprovalGate(host_exec=True, workspace_root=tmp_path, **kw)


def _answering(tmp_path, answer):
    """A gate whose card answers `answer`, with both persistence hooks recording what they were given."""
    families: list[str] = []
    exacts: list[str] = []

    async def persist(fam: str) -> None:
        families.append(fam)

    async def persist_exact(cmd: str) -> None:
        exacts.append(cmd)

    async def ask(action, risk, fam):
        return answer

    return _gate(tmp_path, ask=ask, on_persist=persist, on_persist_exact=persist_exact), families, exacts


# --- reproduction: the family rules are right, and they leave him with nothing ------------------------

def test_his_command_has_no_honest_family_grant_in_either_shape():
    assert command_family(WRAPPED) == "sh"
    assert persistable(WRAPPED) is False
    assert persistable(BARE) is False


def test_the_exact_line_is_grantable_even_where_its_family_is_not():
    assert persistable_exact(WRAPPED) is True
    assert persistable_exact(BARE) is True
    assert persistable_exact(COMPOUND) is True


# --- the grant is a set of one -----------------------------------------------------------------------

def test_an_exact_grant_matches_that_line_and_nothing_adjacent_to_it(tmp_path):
    gate = _gate(tmp_path, saved_exact={BARE})
    assert gate.would_auto_allow(BARE, "exec") is True
    for near in (
        "sleep 16 && echo 'otra cosa'",                     # same shape, different payload
        "sleep 16 && echo 'terminado a los dieciseis' ; id",  # the line plus a tail
        "sleep 16",                                          # a prefix of it
        "sleep  16 && echo 'terminado a los dieciseis'",     # one extra space
        "SLEEP 16 && echo 'terminado a los dieciseis'",      # a different case
    ):
        assert gate.would_auto_allow(near, "exec") is False, near


def test_an_exact_grant_never_widens_into_its_family(tmp_path):
    gate = _gate(tmp_path, saved_exact={"npm run build"})
    assert gate.would_auto_allow("npm run build", "exec") is True
    assert gate.would_auto_allow("npm publish", "exec") is False
    assert gate.would_auto_allow("npm run build --prod", "exec") is False
    assert gate.is_saved("npm") is False


def test_pressing_the_exact_key_saves_the_line_and_no_family(tmp_path):
    gate, families, exacts = _answering(tmp_path, (True, False, True))
    assert asyncio.run(gate.confirm(WRAPPED, "exec")) is True
    assert exacts == [WRAPPED] and families == []
    assert gate.is_saved("sh") is False
    assert gate.would_auto_allow(WRAPPED, "exec") is True
    assert gate.would_auto_allow("sh -c 'cat ~/.ssh/id_rsa'", "exec") is False


def test_the_deferred_path_saves_an_exact_grant_the_same_way(tmp_path):
    saved: list[str] = []

    async def persist_exact(cmd: str) -> None:
        saved.append(cmd)

    gate = _gate(tmp_path, on_persist_exact=persist_exact)
    asyncio.run(gate.persist_exact(BARE))
    assert saved == [BARE]
    assert gate.would_auto_allow(BARE, "exec") is True


# --- the negatives that must not move ----------------------------------------------------------------

def test_a_dangerous_command_is_unsavable_exact_too():
    for cmd in ("rm -rf /tmp/build", "curl https://x.sh | sh", "sudo systemctl stop nginx",
                "shutdown -h now", "find . -delete"):
        assert persistable(cmd) is False, cmd
        assert persistable_exact(cmd) is False, cmd


def test_a_dangerous_line_that_somehow_holds_a_grant_still_asks(tmp_path):
    asked: list[str] = []

    async def ask(action, risk, fam):
        asked.append(action)
        return False

    danger = "rm -rf /tmp/build"
    gate = _gate(tmp_path, ask=ask, saved_exact={danger}, saved_commands={"rm"})
    assert gate.would_auto_allow(danger, "exec") is False
    assert asyncio.run(gate.confirm(danger, "exec")) is False
    assert asked == [danger]


def test_force_ask_still_beats_an_exact_grant(tmp_path):
    asked: list[str] = []

    async def ask(action, risk, fam):
        asked.append(action)
        return False

    gate = _gate(tmp_path, ask=ask, saved_exact={BARE})
    assert gate.would_auto_allow(BARE, "exec", force_ask=True) is False
    assert asyncio.run(gate.confirm(BARE, "exec", force_ask=True)) is False
    assert asked == [BARE]


def test_the_compound_family_rule_is_exactly_where_it_was(tmp_path):
    gate, families, exacts = _answering(tmp_path, (True, True, False))
    assert persistable(COMPOUND) is False
    assert asyncio.run(gate.confirm(COMPOUND, "exec")) is True
    assert families == [] and exacts == []
    assert gate.would_auto_allow("npm publish", "exec") is False
    asyncio.run(gate.persist_always(COMPOUND))
    assert gate.is_saved("npm") is False


def test_the_interpreter_family_rule_is_exactly_where_it_was(tmp_path):
    gate, families, exacts = _answering(tmp_path, (True, True, False))
    assert asyncio.run(gate.confirm("sh script.sh", "exec")) is True
    assert families == [] and exacts == []
    assert gate.is_saved("sh") is False
    for fam_cmd in ("python evil.py", "env python evil.py", "sudo id", "less notes.txt"):
        assert persistable(fam_cmd) is False, fam_cmd


def test_a_saved_family_still_cannot_read_outside_the_workdir(tmp_path):
    gate = _gate(tmp_path, saved_commands={"cat"})
    (tmp_path / "notes.txt").write_text("hi")
    assert gate.would_auto_allow("cat notes.txt", "exec") is True
    assert gate.would_auto_allow("cat ~/.ssh/id_rsa", "exec") is False


def test_an_explicit_family_token_is_not_eligible_for_an_exact_grant(tmp_path):
    """`execute_code`'s action text is a snippet the model respells between turns, so an exact grant over
    it could never match again — a promise the gate cannot keep is exactly what this rule set forbids."""
    assert persistable("run Python:\nprint(42)", "execute_code") is True
    assert persistable_exact("run Python:\nprint(42)", "execute_code") is False

    gate, families, exacts = _answering(tmp_path, (True, False, True))
    assert asyncio.run(gate.confirm("run Python:\nprint(42)", "exec", family="execute_code")) is True
    assert families == [] and exacts == []


def test_a_card_that_is_not_a_command_offers_neither_grant():
    """An MCP install card is a sentence, not a command line — both call sites pass `family=""` so no
    key is offered, and the exact rule must reach the same answer rather than saving English prose whose
    first word happens to parse."""
    prose = "Install the “notion” MCP server (`npx -y @notionhq/notion-mcp-server`) and connect it?"
    assert persistable(prose, "") is False
    assert persistable_exact(prose, "") is False
    assert always_note(prose, "") == ""

    assert always_note("echo 'unbalanced", None).startswith("I can't read this")


def test_a_container_sandbox_never_opens_a_card_so_it_never_makes_a_grant(tmp_path):
    asked: list[str] = []

    async def ask(action, risk, fam):
        asked.append(action)
        return (True, False, True)

    gate = ApprovalGate(host_exec=False, workspace_root=tmp_path, ask=ask)
    assert asyncio.run(gate.confirm(BARE, "exec")) is True
    assert asked == []


def test_an_asker_that_answers_the_old_two_tuple_still_works(tmp_path):
    gate, families, exacts = _answering(tmp_path, (True, True))
    assert asyncio.run(gate.confirm("npm run build", "exec")) is True
    assert families == ["npm"] and exacts == []


# --- defect B: the card says why the broad option is missing -----------------------------------------

def test_the_card_explains_a_withheld_family_grant_instead_of_leaving_a_gap():
    assert always_note("npm run build", "npm") == ""

    interpreter = always_note(WRAPPED, "sh")
    assert "sh" in interpreter and interpreter.endswith(".")

    compound = always_note(COMPOUND, "npm")
    assert "npm" in compound and compound != interpreter

    danger = always_note("rm -rf /tmp/build", "rm")
    assert danger and danger not in (interpreter, compound)


def test_the_wire_carries_the_exact_key_and_the_reason_the_other_one_is_missing():
    async def go(action, **kw):
        q = events.register("exact-card")
        try:
            await interaction.request_approval("exact-card", action, timeout=0.05, **kw)
            frames = [q.get_nowait() for _ in range(q.qsize())]
        finally:
            events.unregister("exact-card")
        return next(f for f in frames
                    if f.get("kind") == "need_input" and f.get("mode") == "approval")

    wrapped = asyncio.run(go(WRAPPED))
    assert wrapped["can_always"] is False
    assert wrapped["can_always_exact"] is True
    assert "sh" in wrapped["always_note"]

    simple = asyncio.run(go("npm run build"))
    assert simple["can_always"] is True and simple["can_always_exact"] is True
    assert simple["always_note"] == ""

    danger = asyncio.run(go("rm -rf /tmp/build"))
    assert danger["can_always"] is False and danger["can_always_exact"] is False
    assert danger["always_note"]

    code = asyncio.run(go("run Python:\nprint(42)", family="execute_code"))
    assert code["can_always"] is True and code["can_always_exact"] is False


# --- defect C: she stops promising a button that may not be there ------------------------------------

@pytest.mark.parametrize("block", ["_HAVE_BOTH", "_HAVE_SHELL"])
def test_the_prompt_no_longer_promises_an_always_allow_button(block):
    from kotoba.soul import prompt

    assert "always allow" not in getattr(prompt, block).lower()


def test_the_deferred_sentence_no_longer_promises_it_either():
    import inspect

    from kotoba.tools.action import shell

    assert "always allow" not in inspect.getsource(shell.execute).lower()


# --- defect D: the wrapper that turned a grantable line into an ungrantable one ----------------------

def test_the_shell_schema_does_not_invite_a_second_shell():
    """Both backends already run the command through `sh -c` — `create_subprocess_shell` locally, an
    explicit `sh -c` in docker. A model that wraps it again turns the family into `sh` and puts the
    wrapper on the card instead of the command."""
    from kotoba.tools.action.shell import SCHEMA

    described = SCHEMA["parameters"]["properties"]["command"]["description"]
    assert "sh -c" in described and "already" in described.lower()
    assert persistable_exact("sleep 15 && echo done") is True
