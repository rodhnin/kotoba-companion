"""The gate must never read a command differently from the shell that will run it.

A live jail escape, not a theory: the gate splits with a parser that does not brace-expand, but the
sandbox executes through a real shell, and on bash — most non-Debian systems — that DOES expand. So
a brace-list read arrived as one token starting with a brace, was read as a relative path resolving
inside the workspace, auto-approved with no card, and the shell then handed the command an absolute
path outside it. No saved grant was needed: it rode the default auto-safe read set, reachable from
the model on both text and voice. These tests pin the general rule rather than the specific
character: what the gate cannot resolve the way the shell will, it refuses to auto-approve, and a
divergence test fails if a future parser change reintroduces one."""
from __future__ import annotations

import subprocess

import pytest
from conftest import posix_only

from kotoba.core.approval import ApprovalGate

JAIL = "/home/jordan/.kotoba/files"


def _gate(**kw):
    """Saved grants included on purpose: the escape must stay shut on the saved path too, not only on
    the auto-safe one."""
    return ApprovalGate(saved_commands={"ls", "cat", "grep"}, host_exec=True,
                        workspace_root=JAIL, **kw)


@pytest.mark.parametrize("action", [
    "cat {/etc/passwd,notes.txt}",
    "cat {~/.ssh/id_ed25519,notes.txt}",
    "head {/etc/shadow,notes.txt}",
    "grep -r secret {/etc,.}",
    "tail {/var/log/auth.log,notes.txt}",
    "ls {/,.}",
])
def test_a_braced_path_never_auto_approves(action):
    assert not _gate().would_auto_allow(action), action


@pytest.mark.parametrize("action", [
    "cat notes.txt",
    "cat ./sub/a.txt",
    "ls",
    "grep TODO *.py",
    "wc -l notes.txt",
])
def test_ordinary_reads_in_the_jail_are_untouched(action):
    """The fix is worthless if it makes her ask about everything — a gate people turn off protects no one.
    Globs stay auto-safe because the shell expands them against the cwd, which IS the jail."""
    assert _gate().would_auto_allow(action), action


@posix_only("/bin/sh")
def test_the_shell_really_does_expand_this():
    """The premise, asserted rather than assumed: on a `/bin/sh` that brace-expands, the token the gate
    saw as one relative path becomes an absolute one.

    `/bin/sh` and not bash, because `/bin/sh -c` is what actually runs her commands. Where that is dash
    the skip is the honest answer — no expansion there means no escape there — and pointing this at a
    bash it can always find would assert a premise about a shell nothing uses. The guard above runs
    everywhere regardless; only this premise is platform-bound."""
    out = subprocess.run(["/bin/sh", "-c", 'printf "%s\\n" {/etc/passwd,notes.txt}'],
                         capture_output=True, text=True).stdout.split()
    if out == ["{/etc/passwd,notes.txt}"]:
        pytest.skip("/bin/sh here does not brace-expand (dash) — the escape needs bash/ksh/zsh")
    assert out == ["/etc/passwd", "notes.txt"]
