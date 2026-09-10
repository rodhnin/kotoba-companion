"""An interpreter is not a command family, and a saved read cannot escape its jail.

Two holes an audit found in live "always allow" grants: a saved interpreter name matched any
invocation of it, including a payload hidden inside one quoted argument with none of the
metacharacters the guard watches for, so a grant for a shell or scripting interpreter became
arbitrary host execution without a card; and the saved-family branch sat before the safe-host-read
check, so a saved read command skipped it and could reach outside the jailed directory. Both are now
fixed: interpreters are refused at decision time and at save time, and a saved grant buys no less
scrutiny than none — the two decision paths must reach the same verdict, since a voice turn trusts
one of them alone to skip the card."""
from __future__ import annotations

import asyncio

import pytest

from kotoba.core.approval import (
    ApprovalGate,
    command_family,
    family_never_auto_approves,
    persistable,
)


def _confirm(gate: ApprovalGate, action: str, **kw) -> bool:
    return asyncio.run(gate.confirm(action, "exec", **kw))


# --- hole 1: the interpreter / exec-wrapper denylist ------------------------------------------------

INTERPRETERS = [
    "sh -c 'cat /etc/hostname'",
    'sh -c "whoami"',
    "sh /tmp/whatever.sh",
    "/bin/sh -c 'id'",
    "bash -c 'curl http://x | tee'",
    "zsh -c 'print hi'",
    "dash -c 'id'",
    "busybox sh -c 'id'",
    "python -c 'import os,sys'",
    "python3 -c 'print(1)'",
    "python3.12 -c 'print(1)'",
    "perl -e 'print 1'",
    "ruby -e 'puts 1'",
    "node -e 'process.exit(0)'",
    "nodejs -e '1'",
    "deno run x.ts",
    "bun run x.ts",
    "php -r 'echo 1;'",
    "awk 'BEGIN{system(\"id\")}'",
    "Rscript -e '1'",
]

WRAPPERS = [
    "env python3 evil.py",
    "env FOO=bar node evil.js",
    "sudo apt update",            # also dangerous, but the family alone must never auto-approve
    "xargs rm",
    "nohup ./evil.sh",
    "time ./evil.sh",
    "timeout 5 ./evil.sh",
    "ssh host 'rm -rf /'",
    "docker run --rm -v /:/host alpine id",
    "podman run alpine id",
    "setsid ./evil.sh",
    "chroot / bash",
]


@pytest.mark.parametrize("action", INTERPRETERS + WRAPPERS)
def test_a_saved_interpreter_or_wrapper_never_auto_approves(tmp_path, action):
    """The whole point of the denylist: enforced at DECISION time, so a grant saved weeks ago stops
    working. Both surfaces refuse it, and they agree (lockstep)."""
    fam = command_family(action)
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={fam})
    assert family_never_auto_approves(fam) is True, fam
    assert gate.would_auto_allow(action, "exec") is False
    assert _confirm(gate, action) is False              # no asker → asks → default deny
    assert gate.would_auto_allow(action, "exec") == _confirm(gate, action)


@pytest.mark.parametrize("action", INTERPRETERS + WRAPPERS)
def test_none_of_them_is_persistable_as_a_derived_family(action):
    """So the card is shown AND `a` is withheld — `persistable` is what `request_approval` reads for
    `can_always`."""
    assert persistable(action) is False


def test_the_denylist_membership_is_derived_from_what_makes_a_token_unsafe():
    """Interpreters run an arbitrary payload; wrappers re-enter under another identity/context. Both
    resolve basename-first and version-stripped."""
    for tok in ("sh", "bash", "python", "python3.13", "/usr/bin/python3", "perl5", "node", "nodejs",
                "awk", "gawk", "Rscript", "busybox"):
        assert family_never_auto_approves(tok) is True, tok
    for tok in ("sudo", "env", "xargs", "nohup", "time", "timeout", "ssh", "docker", "podman"):
        assert family_never_auto_approves(tok) is True, tok


def test_the_families_deliberately_left_off_the_denylist_still_grant():
    """`git`/`npm`/`pytest` name what they run; `sed`/`make` execute only through obscure paths handled
    elsewhere. Excluding them is the difference between a fix and a feature nobody can use."""
    for tok in ("git", "npm", "pytest", "cargo", "sed", "make", "ls", "cat", "grep"):
        assert family_never_auto_approves(tok) is False, tok


@pytest.mark.parametrize("action,fam", [
    ("git status", "git"),
    ("git push --force", "git"),
    ("npm install left-pad", "npm"),
    ("pytest -q tests/", "pytest"),
])
def test_a_real_command_family_still_auto_approves_and_persists(tmp_path, action, fam):
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={fam})
    assert gate.would_auto_allow(action, "exec") is True
    assert _confirm(gate, action) is True
    assert persistable(action) is True


def test_a_compound_is_still_refused_by_the_metachar_guard(tmp_path):
    """Unchanged by the denylist: a saved `npm` cannot buy `npm run build && ./deploy.sh`."""
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={"npm"})
    assert gate.would_auto_allow("npm run build && ./deploy.sh", "exec") is False
    assert persistable("npm run build && ./deploy.sh") is False


def test_an_explicit_execute_code_family_is_untouched(tmp_path):
    """execute_code's action text is not a shell command; the denylist keys on shell tokens, so the
    grant still auto-approves clean code and stays persistable. Risky snippets are stopped by force_ask,
    not here."""
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={"execute_code"})
    assert gate.would_auto_allow("run Python:\nprint(1)", "exec", family="execute_code") is True
    assert gate.would_auto_allow("run Python:\nprint(1)", "exec",
                                 family="execute_code", force_ask=True) is False
    assert persistable("run Python:\nprint(42)", "execute_code") is True


# --- hole 2: a saved read family still faces its jail -----------------------------------------------

def test_a_saved_read_family_inside_the_jail_still_auto_approves(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={"ls", "cat"})
    for action in ("ls", "ls sub", "cat notes.txt", "cat ./notes.txt"):
        assert gate.would_auto_allow(action, "exec") is True, action
        assert _confirm(gate, action) is True, action


@pytest.mark.parametrize("action", [
    "ls ~/.ssh",
    "ls /etc",
    "cat /etc/shadow",
    "cat ~/.ssh/id_rsa",
    "cat ../../etc/passwd",
])
def test_a_saved_read_family_cannot_leave_the_jail(tmp_path, action):
    """The exact escape the audit reproduced: the saved branch used to precede `_safe_host_read`."""
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={"ls", "cat"})
    assert gate.would_auto_allow(action, "exec") is False, action
    assert _confirm(gate, action) is False, action
    assert gate.would_auto_allow(action, "exec") == _confirm(gate, action)


def test_the_jail_check_is_scoped_to_read_families_not_every_grant(tmp_path):
    """A grant for a non-read family (`npm`) is the user saying `always npm`, and it is honoured with a
    path argument outside the workdir — the jail check only reinstates the scrutiny a READ command
    already had."""
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={"npm"})
    assert gate.would_auto_allow("npm install ../../thing", "exec") is True


def test_without_a_workspace_root_a_saved_read_family_asks(tmp_path):
    """No jail to verify → be safe, ask — the same verdict an ungranted read gets on the host."""
    gate = ApprovalGate(host_exec=True, saved_commands={"ls"})
    assert gate.would_auto_allow("ls sub", "exec") is False
    assert _confirm(gate, "ls sub") is False


def test_the_wire_shows_the_card_but_withholds_a_for_an_interpreter():
    """The resulting behaviour, end to end: `request_approval` computes `can_always` off `persistable`,
    so an interpreter action gets a card (it is asked) with the `a` key withheld."""
    from kotoba.core import events, interaction

    async def go(action, **kw):
        q = events.register("interp-card")
        try:
            await interaction.request_approval("interp-card", action, timeout=0.05, **kw)
            frames = [q.get_nowait() for _ in range(q.qsize())]
        finally:
            events.unregister("interp-card")
        return next(f for f in frames if f.get("kind") == "need_input" and f.get("mode") == "approval")

    for action in ("sh -c 'cat id_rsa'", "python3 -c 'print(1)'", "env python evil.py",
                   "docker run alpine id"):
        frame = asyncio.run(go(action))
        assert frame["can_always"] is False, action


def test_a_saved_read_family_still_stops_at_an_escape_flag(tmp_path):
    """`tail -f` never returns and `find -delete` writes — `_safe_host_read` already caught these without
    a grant, and a saved `tail`/`find` must not buy past them."""
    gate = ApprovalGate(host_exec=True, workspace_root=tmp_path, saved_commands={"tail", "find"})
    assert gate.would_auto_allow("tail -f notes.txt", "exec") is False
    assert gate.would_auto_allow("find . -delete", "exec") is False
