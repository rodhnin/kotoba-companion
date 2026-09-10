"""Live QA found the file toolset was work-only, so in conversation she could not read, write,
patch or search files at all — writing three lines became a shell heredoc and changing one word
became a nested interpreter call, each one an approval card.

That inverted the safety model: shell runs anything on the host, while the file tools cannot leave
the jailed library. The fix put file tools into companion. These tests hold the two properties that
decision depends on: the four tools are actually offered in a companion turn, and they remain a
jail — never a way around the approval gate for anything outside the library."""
from __future__ import annotations

import asyncio

import pytest
from conftest import make_symlink

import kotoba.tools.action.file_read as file_read
import kotoba.tools.action.file_write as file_write
import kotoba.tools.action.patch as patch
import kotoba.tools.action.search_files as search_files
from kotoba.tools import registry


class _Ctx:
    def __init__(self, workdir, mode="companion"):
        self.workdir = workdir
        self.mode = mode
        self.session_id = "filetool"
        self.approval = None
        self.channel = "voice"


@pytest.fixture
def jail(tmp_path):
    """A workspace with a secret sitting just outside it, plus a symlink pointing at that secret."""
    root = tmp_path / "library"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    make_symlink(root / "link", secret)
    return root, secret


def test_the_four_file_tools_are_offered_in_a_companion_turn():
    names = {t.get("name") for t in registry.schemas_for(mode="companion")}
    for tool in ("read_file", "write_file", "patch", "search_files"):
        assert tool in names, f"{tool} must be reachable while talking to her"


def test_file_is_in_companion_toolsets_and_the_heavy_ones_are_not():
    assert "file" in registry.COMPANION_TOOLSETS
    for heavy in ("browser", "mcp", "subagent"):
        assert heavy not in registry.COMPANION_TOOLSETS


def test_turning_the_file_toolset_off_still_removes_it_from_companion():
    """The Settings toggle is built from all_toolsets() and checked BEFORE the mode gate — moving `file`
    into companion must not make the switch a no-op there."""
    assert "file" in registry.all_toolsets()
    registry.set_toolset_enabled("file", False)
    try:
        names = {t.get("name") for t in registry.schemas_for(mode="companion")}
        assert "write_file" not in names
    finally:
        registry.set_toolset_enabled("file", True)
    assert "write_file" in {t.get("name") for t in registry.schemas_for(mode="companion")}


@pytest.mark.parametrize("escape", ["../escape.txt", "/etc/kotoba_pwn", "~/.bashrc", "link"])
def test_write_cannot_escape_the_library(jail, escape):
    """`..`, an absolute path, `~` and a symlink pointing out of the jail are all refused. This is what
    makes the un-carded write safe: it is confined, not merely trusted."""
    root, secret = jail
    out = asyncio.run(file_write.execute({"path": escape, "content": "PWNED"}, _Ctx(root)))
    assert "outside my workspace" in out
    assert secret.read_text(encoding="utf-8") == "TOP SECRET"
    assert not (root.parent / "escape.txt").exists()
    assert not (root.parent / "kotoba_pwn").exists()


@pytest.mark.parametrize("escape", ["../secret.txt", "/etc/passwd", "link"])
def test_read_cannot_escape_the_library(jail, escape):
    root, _ = jail
    out = asyncio.run(file_read.execute({"path": escape}, _Ctx(root)))
    assert "outside my workspace" in out
    assert "TOP SECRET" not in out


def test_patch_and_search_cannot_escape_the_library(jail):
    root, secret = jail
    p = asyncio.run(patch.execute(
        {"path": "../secret.txt", "old_string": "TOP", "new_string": "x"}, _Ctx(root)))
    assert "outside my workspace" in p
    assert secret.read_text(encoding="utf-8") == "TOP SECRET"
    for outside in ("..", "/etc"):
        s = asyncio.run(search_files.execute({"query": "SECRET", "path": outside}, _Ctx(root)))
        assert "outside my workspace" in s


def test_the_qa_scenario_works_without_a_single_shell_command(jail):
    """The exact thing that used to need a `sh -c` heredoc and a `python -c` str.replace: write the file,
    change one word, leave the rest intact — three jailed calls, no approval card, no shell."""
    root, _ = jail
    ctx = _Ctx(root)

    wrote = asyncio.run(file_write.execute(
        {"path": "frutas.txt", "content": "manzana\npera\ncereza\n"}, ctx))
    assert "frutas.txt" in wrote
    edited = asyncio.run(patch.execute(
        {"path": "frutas.txt", "old_string": "cereza", "new_string": "uva"}, ctx))
    assert "Updated" in edited
    assert (root / "frutas.txt").read_text(encoding="utf-8") == "manzana\npera\nuva\n"
    assert "uva" in asyncio.run(file_read.execute({"path": "frutas.txt"}, ctx))


def test_search_files_gets_a_short_budget_on_a_live_voice_turn():
    """search_files is the only file tool whose cost scales with the tree, so it is the only one that could
    stall a voice turn. Companion gets a small budget; a background work task keeps the generous one."""
    assert search_files._budget(_Ctx(None, mode="companion")) == search_files._VOICE_BUDGET
    assert search_files._budget(_Ctx(None, mode="work")) == search_files._WORK_BUDGET
    assert search_files._VOICE_BUDGET < search_files._WORK_BUDGET


def test_a_search_that_times_out_asks_to_narrow_instead_of_failing_silently(jail, monkeypatch):
    root, _ = jail
    monkeypatch.setattr(search_files, "_VOICE_BUDGET", 0.0)
    monkeypatch.setattr(search_files.shutil, "which", lambda _n: None)  # exercise the walk fallback too
    (root / "a.txt").write_text("hello\n")
    out = asyncio.run(search_files.execute({"query": "hello"}, _Ctx(root)))
    # Budget 0 means the walk never ran: "No matches found." here would be the silent lie.
    assert out == search_files._NARROW


def test_delegate_file_helper_is_unaffected():
    """delegate(toolset='file') runs the helper in WORK mode with toolset_filter='file' — the companion
    gate is not consulted there, so this move must not change what a file helper is given."""
    import kotoba.tools.action.delegate as delegate

    assert delegate._TOOLSET_MAP["file"] == "file"
    names = {t.get("name") for t in registry.schemas_for(mode="work", toolset_filter="file")}
    for tool in ("read_file", "write_file", "patch", "search_files"):
        assert tool in names
    assert "shell" not in names  # still restricted to its one toolset
