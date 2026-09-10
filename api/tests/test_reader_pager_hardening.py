"""The never-auto-approve family list was missing a class: pagers/editors with a shell escape (`less`/`vi`
run `!cmd`, an interpreter hole in a reader's clothes) plus plain dump/read tools (`base64`/`xxd`/`od`/
`strings`/…) that, being neither auto-safe nor never-auto, let a saved grant read any file on the host
without a card. Membership is DERIVED, not pasted: pagers/editors that spawn a shell satisfy the
INTERPRETER rule (an arbitrary payload runs, the file argument is a decoy the jail can't confine) and go
into `_NEVER_AUTO_FAMILIES`; plain dump/read tools satisfy neither the interpreter nor the exec-wrapper
rule, since they only read the named files, so their home is the jailed reader set (`_AUTO_SAFE_COMMANDS`)
like `cat` — confining a saved grant while keeping in-workdir reads working, whereas the never-auto list
would break the legitimate "always base64 my files" case. `make` stays out on purpose: its recipes come
from a workspace Makefile the user controls, making it a project-script family like `npm`/`cargo`."""
from __future__ import annotations

import asyncio

import pytest

from kotoba.core.approval import (
    ApprovalGate,
    family_never_auto_approves,
    is_auto_safe_family,
    persistable,
)


def _confirm(gate, action, **kw):
    return asyncio.run(gate.confirm(action, "exec", **kw))


PAGERS_EDITORS = ["less", "more", "pg", "vi", "vim", "view", "vimdiff", "rvim", "rview",
                  "nano", "pico", "emacs", "ed", "ex"]
READERS = ["base64", "xxd", "od", "strings", "hexdump", "tac", "nl"]


@pytest.mark.parametrize("fam", PAGERS_EDITORS)
def test_a_pager_or_editor_never_auto_approves_even_when_saved(tmp_path, fam):
    """A saved grant for a shell-escaping pager/editor is a blank cheque `!cmd` could cash — refuse it at
    decision time on both surfaces, in lockstep, and refuse to persist it."""
    action = f"{fam} /etc/shadow"
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={fam})
    assert family_never_auto_approves(fam) is True, fam
    assert gate.would_auto_allow(action, "exec") is False
    assert _confirm(gate, action) is False
    assert gate.would_auto_allow(action, "exec") == _confirm(gate, action)
    assert persistable(action) is False, "the card's `a` (always) key is withheld"


@pytest.mark.parametrize("fam", READERS)
def test_a_saved_reader_is_jailed_not_a_blank_cheque(tmp_path, fam):
    """The dump/read tools become jailed readers: a saved grant reads inside the workdir but asks for a
    path outside it — the same scrutiny `cat` already had, so /etc/shadow no longer rides the grant."""
    (tmp_path / "notes.txt").write_text("hi")
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={fam})
    assert family_never_auto_approves(fam) is False, "a reader is not an interpreter"
    assert is_auto_safe_family(fam) is True, "it is a jailed reader family"
    assert gate.would_auto_allow(f"{fam} notes.txt", "exec") is True, "in-workdir read still works"
    assert gate.would_auto_allow(f"{fam} /etc/shadow", "exec") is False, "out-of-jail read now asks"
    assert gate.would_auto_allow(f"{fam} ~/.ssh/id_rsa", "exec") is False


def test_a_reader_in_the_workdir_auto_runs_even_without_a_grant(tmp_path):
    """Being in the reader set means an in-workdir read needs no grant (like cat), while an out-of-jail
    one still asks — closing the leak without a UX regression."""
    (tmp_path / "data.bin").write_text("x")
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path)
    assert gate.auto_safe("base64 data.bin", "exec") is True
    assert gate.auto_safe("base64 /etc/shadow", "exec") is False
    assert gate.auto_safe("xxd /etc/passwd", "exec") is False


def test_make_is_deliberately_left_off_the_never_auto_list(tmp_path):
    """`make` runs a workspace Makefile the user controls — a project-script family like npm/cargo/pytest,
    not a blank cheque. A saved grant still honours it, matching the accepted subcommand-family policy."""
    assert family_never_auto_approves("make") is False
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={"make"})
    assert gate.would_auto_allow("make build", "exec") is True


def test_legit_dev_families_still_auto_approve_under_a_saved_grant(tmp_path):
    """The feature must stay usable: git/npm/pytest/cargo and an in-workdir ls keep auto-approving under a
    saved grant, or the owner turns it off."""
    (tmp_path / "notes.txt").write_text("hi")
    for fam, action in [("git", "git status"), ("npm", "npm install left-pad"),
                        ("pytest", "pytest -q"), ("cargo", "cargo build"),
                        ("ls", "ls"), ("cat", "cat notes.txt")]:
        gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={fam})
        assert gate.would_auto_allow(action, "exec") is True, action


@pytest.mark.parametrize("path,fam", [("/bin/cat", "cat"), ("/usr/bin/base64", "base64"),
                                      ("/usr/bin/tail", "tail")])
def test_the_jail_recheck_reads_the_command_not_its_spelling(tmp_path, path, fam):
    """The same reader written with its absolute path must face the same jail.

    `_saved_allows` normalizes the token for the never-auto denylist (`_base_family`) and then compares
    the RAW first token against `_AUTO_SAFE_COMMANDS`. `/bin/cat` is in neither set, so the jail re-check
    short-circuited and the grant was honoured unconditionally: a saved `/bin/cat` read `/etc/shadow` on
    the host with no card and no audit row naming a person, while a saved `cat` was refused and NO grant
    at all was refused. The grant bought LESS scrutiny than no grant — the one thing `_saved_allows`'
    docstring says it may never do. `persistable('/bin/cat notes.txt')` is True, so the card really does
    offer the `a` key for that spelling."""
    (tmp_path / "notes.txt").write_text("hi")
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={path})

    assert gate.would_auto_allow(f"{path} /etc/shadow", "exec") is False, "out-of-jail must still ask"
    assert gate.would_auto_allow(f"{path} notes.txt", "exec") is True, "in-workdir read still works"
    # …and never less scrutiny than the bare spelling gets.
    bare = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={fam})
    assert gate.would_auto_allow(f"{path} /etc/shadow", "exec") == bare.would_auto_allow(
        f"{fam} /etc/shadow", "exec")


def test_an_escape_flag_is_seen_through_an_absolute_path_too(tmp_path):
    """`tail -f` never returns, so it is an escape flag — `_ESCAPE_FLAGS` is keyed on `tail`, and
    `_safe_host_read` looked it up under the raw `/usr/bin/tail`, finding nothing."""
    (tmp_path / "app.log").write_text("x")
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={"/usr/bin/tail"})
    assert gate.would_auto_allow("/usr/bin/tail -f app.log", "exec") is False
    assert gate.would_auto_allow("/usr/bin/tail -n 5 app.log", "exec") is True


def test_the_grant_listing_describes_an_absolute_path_grant_correctly(tmp_path):
    """`is_auto_safe_family` is the one input to the sentence `/settings security` and `/approvals` show
    for a saved grant, and it compared the RAW token while enforcement compares the resolved command.

    So a `/bin/cat` grant — which IS jailed to the workdir — was described to the person deciding
    whether to revoke it as one that "runs without asking", i.e. broader than it is. Same defect shape
    as the enforcement bug above, on the surface that explains it."""
    from kotoba.core.approval import is_auto_safe_family

    assert is_auto_safe_family("cat") is True
    assert is_auto_safe_family("/bin/cat") is True, "same command, same sentence"
    assert is_auto_safe_family("/usr/bin/base64") is True
    assert is_auto_safe_family("npm") is False
    assert is_auto_safe_family("") is False and is_auto_safe_family(None) is False
