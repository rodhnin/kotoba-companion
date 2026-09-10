"""One reader for a command line, following the shell that will run it.

Three places had their own POSIX `shlex.split`. On Windows that eats the path separators, so the gate
auto-approved reading a private key and the destructive card drew an empty blast radius. The second
half of this file pins that the three sites USE the shared reader — one nobody calls proves nothing.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from kotoba.core import command_line
from kotoba.cli.render import cards
from kotoba.core import approval


@pytest.fixture
def on_windows(monkeypatch):
    """Forcing `os.name` instead would measure the harness: a Linux build cannot build a WindowsPath."""
    monkeypatch.setattr("kotoba.core.sandbox.local.shell_is_windows", lambda: True)


def test_a_windows_path_keeps_its_separators(on_windows):
    assert command_line.split(r"cat C:\Users\me\.ssh\id_rsa") == [
        "cat", r"C:\Users\me\.ssh\id_rsa"]
    assert command_line.split(r"del %USERPROFILE%\Desktop\notes.txt") == [
        "del", r"%USERPROFILE%\Desktop\notes.txt"]
    assert command_line.split(r"type \\server\share\secrets.txt") == [
        "type", r"\\server\share\secrets.txt"]


def test_a_quoted_windows_path_arrives_without_its_quotes(on_windows):
    """Non-POSIX mode keeps the quotes inside the token; a caller wants the path without them."""
    assert command_line.split(r'Remove-Item "C:\Program Files\thing"') == [
        "Remove-Item", r"C:\Program Files\thing"]


def test_a_backtick_is_refused_rather_than_guessed_at(on_windows):
    """PowerShell's escape, which shlex does not know. Read as an ordinary character it hands back a
    token carrying the backtick and splits the escaped one off — so the card would draw a blast radius
    naming files the command never touches, and a wrong list is worse than none."""
    assert command_line.split(r"Get-Content C:\a` b.txt") == []
    assert command_line.split(r"Remove-Item `-Force") == []


def test_a_doubled_quote_inside_a_run_is_refused(on_windows):
    """PowerShell escapes a quote by doubling it. shlex reads the pair as a close and a reopen, so
    `'it''s.txt'` arrives as three tokens naming two files that do not exist."""
    assert command_line.split("cat 'it''s.txt'") == []
    assert command_line.split('type "a""b.txt"') == []


def test_an_empty_argument_is_not_a_doubled_quote(on_windows):
    """The one character of difference: inside a run a quote followed by its twin escapes, and one
    followed by anything else closes. An empty argument is legitimate and must still read."""
    assert command_line.split("echo ''") == ["echo", ""]
    assert command_line.split("echo '' 'x'") == ["echo", "", "x"]


def test_a_backtick_is_ordinary_on_posix():
    """There it is not an escape and never was — the refusal above must not follow the token home."""
    assert command_line.split("echo a`b") == ["echo", "a`b"]


def test_posix_reading_is_exactly_what_it_always_was():
    """Unchanged on the platform every current install runs on."""
    assert command_line.split("rm -rf /tmp/x") == ["rm", "-rf", "/tmp/x"]
    assert command_line.split(r"cat /home/me/a\ file") == ["cat", "/home/me/a file"]
    assert command_line.split('echo "two words"') == ["echo", "two words"]


@pytest.mark.parametrize("broken", ['cat "unbalanced', "cat 'unbalanced"])
def test_an_unreadable_line_is_empty_and_never_raises(broken, on_windows):
    """Every caller reads a string a model wrote, and empty is the safe answer for all of them."""
    assert command_line.split(broken) == []


def test_the_blast_radius_sees_a_windows_path_at_all(on_windows, monkeypatch):
    """A Windows path cannot exist here, so the oracle is the token that reaches `Path`."""
    asked: list[str] = []
    real_exists = Path.exists

    def spy(self):
        asked.append(str(self))
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", spy)
    cards._targets(r"Remove-Item -Recurse C:\Users\me\Documents")

    # The oracle is the BACKSLASH, not "has a separator": `abspath` prepends the working directory,
    # so a forward slash appears either way and an oracle looking for one passes against the bug.
    assert any("\\" in a for a in asked), (
        "the card looked for a path with its separators eaten, so it can never find anything "
        f"and shows an empty blast radius: {asked}")


def test_every_reader_of_a_command_line_uses_the_shared_one():
    """A fourth `shlex.split` on a command line is the bug coming back, so this reads the source."""
    for module in (cards, approval):
        src = inspect.getsource(module)
        assert "shlex.split(" not in src, (
            f"{module.__name__} reads a command line itself — use core.command_line.split, "
            "which follows the shell that will actually run it")
