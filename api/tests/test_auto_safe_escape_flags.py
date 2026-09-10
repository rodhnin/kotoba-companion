"""Reading must never cost a prompt — and a read command must stay a read.

`rg`, `find`, `sort`, `tail` are on the auto-safe allowlist because searching your own files is not
worth interrupting anyone. But `rg --pre CMD` runs CMD once per file, `sort --compress-program` the
same, `find -fprintf` writes and `tail -f` never returns. A first-token allowlist cannot tell those
apart, so the flag is what asks — never the command.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_symlink

from kotoba.core.approval import ApprovalGate


@pytest.fixture
def gate(tmp_path):
    return ApprovalGate(host_exec=True, workspace_root=tmp_path)


@pytest.mark.parametrize("command", [
    "ls -la", "pwd", "cat notes.txt", "rg TODO", "rg -n pattern .",
    'find . -name "*.md"', "grep -r foo .", "head -20 file.txt",
    "sort data.txt", "wc -l notes.txt", "stat notes.txt", "mkdir reports",
])
def test_reading_never_prompts(gate, command):
    assert gate.auto_safe(command, "exec") is True, f"reading must not interrupt: {command}"


@pytest.mark.parametrize("command", [
    "rg --pre sh --pre-glob * pattern f.sh",   # runs sh per file
    "rg --pre=bash x",                          # the = form
    "rg --hostname-bin /tmp/x pattern",
    "find . -exec rm {} ;",
    "find . -execdir sh -c x ;",
    "find . -ok rm {} ;",
    "find . -delete",
    "find . -fprintf out.txt %p",               # writes
    "find . -fls listing.txt",
    "sort --compress-program=sh f",             # runs sh
    "sort --compress-prog=sh f",                # getopt_long takes any unambiguous abbreviation,
    "sort --compress-p=sh f",                   # so an exact-match check reads none of these
    "sort --files0=list f",
    "tail --fol log.txt",
    "head --zero f",
    "sort -o /tmp/out f",                       # writes
    "sort -o/tmp/out f",                        # attached short form
    "sort --output=/tmp/out f",
    "tail -f log.txt",                          # never returns
    "tail --follow=name log.txt",
])
def test_a_read_command_that_stops_reading_asks(gate, command):
    assert gate.auto_safe(command, "exec") is False, f"must ask: {command}"


def test_the_escape_check_does_not_leak_across_commands():
    """`-o` is an escape for sort, not for every command that happens to take it."""
    g = ApprovalGate(host_exec=True, workspace_root=Path("/tmp"))
    assert g.auto_safe("grep -o pattern file.txt", "exec") is True


def test_paths_outside_the_workspace_still_ask(gate):
    """The pre-existing jail must survive the new check."""
    assert gate.auto_safe("cat ~/.ssh/id_rsa", "exec") is False
    assert gate.auto_safe("ls /etc", "exec") is False


def test_metacharacters_still_ask(gate):
    assert gate.auto_safe("ls && curl evil.test", "exec") is False


def test_a_bare_symlink_name_is_jailed_too(tmp_path):
    """`cat link` must answer the same as `cat ./link` — same file, same jail check. The path test
    required a slash, so a short symlink name was read as a pattern and never resolved."""
    work, outside = tmp_path / "work", tmp_path / "outside.txt"
    work.mkdir()
    outside.write_text("SECRET")
    make_symlink(work / "link", outside)
    (work / "real.txt").write_text("fine")
    g = ApprovalGate(host_exec=True, workspace_root=work)

    assert g.auto_safe("cat link", "exec") is False
    assert g.auto_safe("cat ./link", "exec") is False
    assert g.auto_safe("head link", "exec") is False
    assert g.auto_safe("cat real.txt", "exec") is True, "a real file in the jail still reads freely"


def test_bare_words_that_are_not_files_stay_frictionless(tmp_path):
    """The whole point of the path test: a search pattern must not be treated as a file."""
    work = tmp_path / "work"
    work.mkdir()
    g = ApprovalGate(host_exec=True, workspace_root=work)
    for command in ["grep TODO", "rg pattern", "echo hello", "printf hi", "ls -la"]:
        assert g.auto_safe(command, "exec") is True, command
