"""A file over 250,000 characters must survive a simulated sandbox rehydrate, in both modes.

`_publish_file` called `stash()` unconditionally, which truncates content at 200,000 characters, and
`_rehydrate` then wrote that truncated copy back into the sandbox — in default mode, where the workdir
IS the library, that overwrote the real file on disk with truncated content. Now `_publish_file` no
longer stashes in default mode, and `_rehydrate` skips writing when the workdir is the library, since
the durable file is already there. In advanced mode both behaviours are preserved intact; sending the
truncated stash on to a remote sandbox is a known trade-off, pinned by the last assertion here.
"""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.session_sandbox as ss
from kotoba.core import file_library, file_store

BIG = "A" * 260_000  # over both caps: file_store._MAX_FILE and file_library._MAX_TEXT, each 200 000


class _FakeSB:
    """Minimal sandbox stand-in: records write calls without touching the real filesystem."""
    _lifetime = 300

    def __init__(self):
        self.writes: list[tuple[str, bytes]] = []

    async def start(self):
        pass

    async def write(self, path: str, data: bytes):
        self.writes.append((path, data))

    async def kill(self):
        pass


@pytest.fixture(autouse=True)
def _clean_sandbox():
    ss._live.clear()
    ss._active.clear()
    ss._pending_close.clear()
    yield
    ss._live.clear()
    ss._active.clear()
    ss._pending_close.clear()


# ---------------------------------------------------------------------------
# Default mode: workdir IS the library
# ---------------------------------------------------------------------------

def test_large_file_survives_rehydrate_default_mode(tmp_path, monkeypatch):
    """Default mode: _rehydrate must skip writing, so a >200 KB library file is never overwritten
    with a truncated stash copy.

    The stash is pre-loaded with the truncated copy the bug would have produced, then the rehydrate
    runs against a fake sandbox whose workdir is the library: it must write nothing at all, and the
    file on disk must come out untouched."""
    lib = tmp_path / "lib"
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    monkeypatch.delenv("KOTOBA_WORKSPACE_DIR", raising=False)
    file_library.clear()   # rmtrees the old dir; re-create below
    lib.mkdir(parents=True, exist_ok=True)

    target = lib / "big.txt"
    target.write_text(BIG)

    file_store.clear("s-default")
    file_store.stash("s-default", "big.txt", BIG)
    stashed = file_store.get("s-default", "big.txt")
    assert stashed is not None and len(stashed) < len(BIG), "precondition: stash is truncated"

    sb = _FakeSB()
    asyncio.run(ss._rehydrate(sb, "s-default", lib))

    assert sb.writes == [], (
        "_rehydrate must not write when workdir IS the library — "
        "it would overwrite the real file with truncated stash content"
    )

    assert target.read_text(encoding="utf-8") == BIG


def test_large_file_not_stashed_in_default_mode(tmp_path, monkeypatch):
    """Default mode: _publish_file must not stash a large file, so there is nothing for
    _rehydrate to (accidentally) write back."""
    from kotoba.core.loop import _publish_file

    lib = tmp_path / "lib"
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    monkeypatch.delenv("KOTOBA_WORKSPACE_DIR", raising=False)
    file_library.clear()   # rmtrees the old dir; re-create below
    lib.mkdir(parents=True, exist_ok=True)
    file_store.clear("s-nostash")

    target = lib / "big.txt"
    target.write_text(BIG)

    _publish_file("s-nostash", lib, "big.txt", BIG)

    assert file_store.get("s-nostash", "big.txt") is None, (
        "_publish_file must not stash in default mode"
    )
    assert target.read_text(encoding="utf-8") == BIG, "on-disk file must be unchanged"


# ---------------------------------------------------------------------------
# Advanced mode: workdir is KOTOBA_WORKSPACE_DIR (not the library)
# ---------------------------------------------------------------------------

def test_large_file_survives_rehydrate_advanced_mode(tmp_path, monkeypatch):
    """Advanced mode: _rehydrate DOES write, which is what a remote or docker sandbox needs.

    There `_publish_file` stashes (truncated) while the file itself lives in the workspace. The fake
    sandbox records writes without touching disk, so the workspace file stays full-length; what
    matters is that the write still happened, and that what reached the sandbox is the shorter
    stash — the trade-off named in the module docstring."""
    ws = tmp_path / "workspace"
    lib = tmp_path / "lib"
    monkeypatch.setenv("KOTOBA_WORKSPACE_DIR", str(ws))
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    file_library.clear()   # rmtrees old lib dir; re-create both below
    ws.mkdir(parents=True, exist_ok=True)
    lib.mkdir(parents=True, exist_ok=True)

    target = ws / "big.txt"
    target.write_text(BIG)

    file_store.clear("s-adv")
    file_store.stash("s-adv", "big.txt", BIG)

    sb = _FakeSB()
    asyncio.run(ss._rehydrate(sb, "s-adv", ws))

    assert len(sb.writes) == 1, "_rehydrate must write stashed files in advanced mode"
    written_path, written_data = sb.writes[0]
    assert written_path == "big.txt"

    assert target.read_text(encoding="utf-8") == BIG, (
        "workspace file on disk must survive intact (FakeSandbox does not write to disk)"
    )

    assert len(written_data.decode("utf-8", "replace")) < len(BIG), (
        "advanced mode still sends the stash to the sandbox; this is a known trade-off "
        "until large-file stashing is redesigned (out of scope for this fix)"
    )
