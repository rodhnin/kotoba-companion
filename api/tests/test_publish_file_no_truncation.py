"""Tests for the _publish_file helper and the data-loss fixes it underpins.

Three real bugs guarded here: truncation, where mirroring capped text at 200,000 chars and appended
"(truncated)", so a 250,000-char write_file ended up 200,016 bytes on disk with user data destroyed;
an advanced-mode gap, where the completed version was never copied to the library, so the panel always
showed the pre-sources copy; and stash staleness, where the stash was written before the report
completed, so a sandbox recreate would write the stale, no-sources content back over the finished file.
"Default mode" means the workdir IS the library directory; "advanced mode" means a separate workspace
directory mirrored into it.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.core.loop as loop
from kotoba.core import citations, events, file_library, file_store
from kotoba.core.loop import _publish_file
from kotoba.tools import ToolContext


# ---------------------------------------------------------------------------
# Helpers shared by the scripted-client tests
# ---------------------------------------------------------------------------

def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args):
    item = types.SimpleNamespace(
        type="function_call", name=name,
        arguments=json.dumps(args), call_id=f"c_{name}",
    )
    return types.SimpleNamespace(type="response.output_item.done", item=item)


def _ev_annotation(url, title=""):
    ann = types.SimpleNamespace(type="url_citation", url=url, title=title)
    return types.SimpleNamespace(type="response.output_text.annotation.added", annotation=ann)


class _FakeStream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _FakeClient:
    """Scripted Responses API: each create() call returns the next pre-built event list."""
    def __init__(self, scripts):
        self._scripts, self._i = scripts, 0
        self.responses = self

    async def create(self, **kw):
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _FakeStream(evs)


class _DB:
    async def insert_audit_log(self, **kw):
        pass

    async def list_approved_commands(self):
        return []


_PROSE_REPORT = (
    "# Research Report\n\nThis covers the topic in detail.\n\n"
    "## Sources\n- See official documentation.\n"
)
_SOURCE_URL = "https://example.com/real-source"


def _run_loop(session, scripts, tmp_path, monkeypatch):
    """Drive the REAL agentic_loop with a scripted client. Returns loop result."""
    monkeypatch.setattr(loop, "get_client", lambda: _FakeClient(scripts))
    events.register(session)
    try:
        return asyncio.run(loop.agentic_loop(
            [{"role": "user", "content": "research this"}],
            session, _DB(), asyncio.Queue(), {},
        ))
    finally:
        events.unregister(session)


# ---------------------------------------------------------------------------
# file_library.touch — index update only, no byte write
# ---------------------------------------------------------------------------

@pytest.fixture
def lib(tmp_path, monkeypatch):
    d = tmp_path / "files"
    d.mkdir()
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(d))
    file_library.clear()
    yield d
    file_library.clear()


def test_touch_updates_index_without_writing_bytes(lib):
    """touch() moves the index only: the entry appears as created and unseen, the bytes do not move."""
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "existing.txt").write_text("original content", encoding="utf-8")
    file_library.touch("existing.txt")
    entry = next((f for f in file_library.list_all() if f["path"] == "existing.txt"), None)
    assert entry is not None
    assert entry["seen"] is False
    assert (lib / "existing.txt").read_text(encoding="utf-8") == "original content"


def test_touch_on_missing_file_does_not_crash(lib):
    """Indexing a path that is not there is a no-op, not an exception."""
    file_library.touch("no-such-file.txt")


# ---------------------------------------------------------------------------
# _publish_file — default mode (workdir IS library) — no truncation
# ---------------------------------------------------------------------------

def test_default_mode_large_file_not_truncated(tmp_path, monkeypatch):
    """The mirror must NOT truncate a file > _MAX_TEXT when workdir IS the library.

    The test runs in three phases. First it proves the bug was real: file_library.save_text — the
    old mirror call — is applied to the 250 000-char file and does truncate it. Then the full
    content is restored. Then _publish_file is called on the same file, and on a default setup it
    reaches touch() instead of save_text, so nothing is rewritten and nothing is lost."""
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    monkeypatch.delenv("KOTOBA_WORKSPACE_DIR", raising=False)

    big_content = "A" * 250_000
    target = lib / "big.txt"
    target.write_text(big_content, encoding="utf-8")

    file_library.save_text("big.txt", big_content)
    assert target.read_text(encoding="utf-8").endswith("… (truncated)"), "precondition: save_text truncates"

    target.write_text(big_content, encoding="utf-8")

    _publish_file("s", lib, "big.txt", big_content)

    disk_content = target.read_text(encoding="utf-8")
    assert len(disk_content) == 250_000, "file must not be truncated after _publish_file"
    assert not disk_content.endswith("… (truncated)")


def test_default_mode_file_byte_identical_after_publish(tmp_path, monkeypatch):
    """Normal-sized file stays byte-identical — no clobber, no change."""
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    monkeypatch.delenv("KOTOBA_WORKSPACE_DIR", raising=False)

    content = "Hello, world!\n"
    (lib / "hello.txt").write_text(content, encoding="utf-8")

    _publish_file("s", lib, "hello.txt", content)

    assert (lib / "hello.txt").read_text(encoding="utf-8") == content


def test_publish_file_escape_refused(tmp_path, monkeypatch):
    """A traversal path must be refused — _publish_file must not write outside the jail."""
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    monkeypatch.delenv("KOTOBA_WORKSPACE_DIR", raising=False)

    outside = tmp_path / "escape.md"
    outside.write_text("untouched", encoding="utf-8")

    from kotoba.core.path_security import PathSecurityError
    with pytest.raises(PathSecurityError):
        _publish_file("s", lib, "../escape.md", "injected")

    assert outside.read_text(encoding="utf-8") == "untouched", "escape attempt must not touch the outside file"


def test_publish_file_no_stash_in_default_mode(tmp_path, monkeypatch):
    """Default mode (workdir IS library): _publish_file must NOT stash.

    The stash is only used to rehydrate a sandbox, and in default mode the sandbox's workdir
    IS the library — so the file is already on disk and writing the (possibly truncated) stash
    back via _rehydrate would corrupt files larger than file_store._MAX_FILE."""
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    monkeypatch.delenv("KOTOBA_WORKSPACE_DIR", raising=False)

    (lib / "note.txt").write_text("note content", encoding="utf-8")

    _publish_file("sess-stash", lib, "note.txt", "note content")

    assert file_store.get("sess-stash", "note.txt") is None, (
        "must not stash in default mode — stash would truncate >200 KB files"
    )


# ---------------------------------------------------------------------------
# Advanced mode — the turn-end net updates the library copy
# ---------------------------------------------------------------------------

def test_advanced_mode_write_file_url_lands_in_both(tmp_path, monkeypatch):
    """Advanced mode: the url_citation arrives AFTER write_file, and both copies still get it.

    The write-time mirror copies the pre-sources content to the library; complete_reports() then
    adds the URL to the workdir file; the turn-end _publish_file call syncs the library copy. The
    URL has to end up in BOTH the project file (workdir) and the library copy.

    The script is two model iterations: write_file with no citations yet, then a url_citation
    followed by the closing text."""
    wd = tmp_path / "work"
    lib = tmp_path / "lib"
    wd.mkdir()
    lib.mkdir()
    monkeypatch.setenv("KOTOBA_WORKSPACE_DIR", str(wd))
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))

    scripts = [
        [_ev_call("write_file", {"path": "report.md", "content": _PROSE_REPORT})],
        [_ev_annotation(_SOURCE_URL, "Example Source"), _ev_text("done")],
    ]

    _run_loop("sess-adv-write", scripts, tmp_path, monkeypatch)

    wd_content = (wd / "report.md").read_text(encoding="utf-8")
    lib_content = (lib / "report.md").read_text(encoding="utf-8")

    assert _SOURCE_URL in wd_content, "workdir file must contain the URL after complete_reports"
    assert _SOURCE_URL in lib_content, "library copy must also contain the URL after Step-3 _publish_file"
    assert wd_content.count("## Sources") == 1
    assert lib_content.count("## Sources") == 1


def test_advanced_mode_patch_url_lands_in_both(tmp_path, monkeypatch):
    """Same scenario but the write happens via patch instead of write_file.

    The file is pre-written into the workdir so patch has something to patch, and the script is the
    same two iterations: the patch call, then the url_citation and the closing text."""
    wd = tmp_path / "work"
    lib = tmp_path / "lib"
    wd.mkdir()
    lib.mkdir()
    monkeypatch.setenv("KOTOBA_WORKSPACE_DIR", str(wd))
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))

    (wd / "report.md").write_text(_PROSE_REPORT, encoding="utf-8")

    scripts = [
        [_ev_call("patch", {"path": "report.md",
                            "old_string": "This covers the topic in detail.",
                            "new_string": "This covers the topic in detail. Updated."})],
        [_ev_annotation(_SOURCE_URL, "Patch Source"), _ev_text("done")],
    ]

    _run_loop("sess-adv-patch", scripts, tmp_path, monkeypatch)

    wd_content = (wd / "report.md").read_text(encoding="utf-8")
    lib_content = (lib / "report.md").read_text(encoding="utf-8")

    assert _SOURCE_URL in wd_content, "workdir file must contain the URL"
    assert _SOURCE_URL in lib_content, "library copy must also contain the URL"


# ---------------------------------------------------------------------------
# Stash staleness after the turn-end net
# ---------------------------------------------------------------------------

def test_consequence_3_default_mode_no_stash_file_intact(tmp_path, monkeypatch):
    """In default mode (workdir IS the library) _publish_file must NOT stash.

    Stashing would truncate files larger than file_store._MAX_FILE; _rehydrate would then
    write the truncated copy back over the real library file on sandbox recreate.
    The durable file is already on disk, so no stash is needed and _rehydrate is a no-op
    for this directory (it checks workdir == library_dir before writing anything).

    The on-disk file must also still carry the URL: complete_reports wrote it, and _publish_file
    left it alone."""
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    monkeypatch.delenv("KOTOBA_WORKSPACE_DIR", raising=False)

    ctx = ToolContext(db=None, session_id="sess-c3", workdir=lib, mode="work")

    body = "# Report\n\nFindings here.\n\n## Sources\n- See docs.\n"
    (lib / "report.md").write_text(body, encoding="utf-8")

    citations.note_markdown_write(ctx, "report.md")
    citations.collect_from_annotation(ctx, {"type": "url_citation", "url": _SOURCE_URL, "title": "T"})

    done = citations.complete_reports(ctx)
    assert done == ["report.md"], "complete_reports must complete the file"

    for rel in done:
        _publish_file("sess-c3", lib, rel)

    assert file_store.get("sess-c3", "report.md") is None, (
        "_publish_file must not stash in default mode (workdir IS library)"
    )
    assert _SOURCE_URL in (lib / "report.md").read_text(encoding="utf-8")
