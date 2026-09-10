"""On-disk file LIBRARY (~/.kotoba/files): durable, session-independent storage for the Files panel.
Covers save/list/tag (new→edited), image decode, the path-traversal jail, size caps, and pruning."""
from __future__ import annotations

import base64

import pytest

from kotoba.core import file_library


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    file_library.clear()
    yield tmp_path / "files"
    file_library.clear()


def test_save_text_lists_and_flips_created_to_edited(lib):
    """A saved file lands on disk with its directory structure intact, and re-writing it flips the
    tag from created to edited in place — one library entry, never a duplicate."""
    file_library.save_text("src/app.py", "print(1)")
    listed = file_library.list_all()
    assert len(listed) == 1
    f = listed[0]
    assert f["path"] == "src/app.py" and f["kind"] == "text" and f["action"] == "created"
    assert (lib / "src" / "app.py").read_text(encoding="utf-8") == "print(1)"

    file_library.save_text("src/app.py", "print(2)")
    f2 = file_library.list_all()[0]
    assert f2["action"] == "edited"
    assert (lib / "src" / "app.py").read_text(encoding="utf-8") == "print(2)"
    assert len(file_library.list_all()) == 1


def test_mark_seen_and_reset_on_edit(lib):
    """Marking a file seen clears its tag; the next write to it brings the tag back.

    `mark_seen` also CREATES an index entry for an untracked file — one made or moved by a raw shell
    command — so that "seen" survives: the file is on disk, it simply never went through
    `_touch_index`. A path that is not on disk at all is refused, because this call is reachable
    from POST /api/files/seen and inventing an index key for a file that does not exist grew the
    sidecar forever while answering ok:true for nothing."""
    file_library.save_text("notes.md", "v1")
    assert next(f for f in file_library.list_all() if f["path"] == "notes.md")["seen"] is False
    assert file_library.mark_seen("notes.md") is True
    assert next(f for f in file_library.list_all() if f["path"] == "notes.md")["seen"] is True
    file_library.save_text("notes.md", "v2")
    f = next(f for f in file_library.list_all() if f["path"] == "notes.md")
    assert f["seen"] is False and f["action"] == "edited"
    (lib / "shell-made.txt").write_text("made by shell", encoding="utf-8")
    assert file_library.mark_seen("shell-made.txt") is True
    assert file_library.mark_seen("never-existed.txt") is False
    assert file_library.mark_seen("../../etc/passwd") is False


def test_import_workdir_adopts_new_and_changed_files(lib, tmp_path):
    """A file a shell command writes to the sandbox workdir is adopted into the library on the next
    scan, so a plain redirect shows up in the Files panel exactly as `write_file` does.

    The baseline of modification times is taken BEFORE the command runs; afterwards only what
    changed is adopted. A file the command did not touch is not re-adopted, and `execute_code`'s
    `_run.py` scratch file is skipped as junk."""
    wd = tmp_path / "work"
    wd.mkdir()
    (wd / "old.txt").write_text("already here", encoding="utf-8")
    base = file_library.snapshot_mtimes(wd)
    assert "old.txt" in base

    (wd / "saludo.txt").write_text("Hola Jordan", encoding="utf-8")
    (wd / "_run.py").write_text("print(1)", encoding="utf-8")
    imported = file_library.import_workdir(wd, base)
    names = {rel for rel, _ in imported}
    assert "saludo.txt" in names
    assert "old.txt" not in names
    assert "_run.py" not in names
    assert (lib / "saludo.txt").read_text(encoding="utf-8") == "Hola Jordan"
    assert "saludo.txt" in {f["path"] for f in file_library.list_all()}


def test_untracked_file_has_no_tag_but_tracked_one_does(lib):
    """A file that appears on disk without going through the tools — pre-existing, or written by a
    raw shell command — has no index entry, and is therefore treated as already seen: no new/edited
    tag, so it does not flare "new" on every reload. A file the tools themselves create does."""
    (lib).mkdir(parents=True, exist_ok=True)
    (lib / "preexisting.txt").write_text("was here", encoding="utf-8")
    f = next(x for x in file_library.list_all() if x["path"] == "preexisting.txt")
    assert f["seen"] is True
    file_library.save_text("made.txt", "by kotoba")
    g = next(x for x in file_library.list_all() if x["path"] == "made.txt")
    assert g["seen"] is False and g["action"] == "created"


def test_note_changes_tags_files_touched_this_turn(lib, tmp_path):
    """When the workdir IS the library, a shell command that creates or moves a file is reflected by
    touching the index rather than copying anything, so the file still gets its tag. Files the
    command left alone keep the state they had."""
    (lib).mkdir(parents=True, exist_ok=True)
    (lib / "old.txt").write_text("old", encoding="utf-8")
    base = file_library.snapshot_mtimes(lib)
    (lib / "fresh.txt").write_text("fresh from shell", encoding="utf-8")
    touched = file_library.note_changes(lib, base)
    assert ("fresh.txt", "created") in touched
    assert all(rel != "old.txt" for rel, _ in touched)
    fresh = next(x for x in file_library.list_all() if x["path"] == "fresh.txt")
    assert fresh["seen"] is False


def test_note_changes_writes_the_index_once_for_the_whole_batch(lib, monkeypatch):
    """One shell command, one index write — it was one per file, and the index is rewritten whole.

    `note_changes` called a per-file touch (flock, full read, json.dumps, fsync, rename) then reloaded
    the index to read back the action — quadratic in a directory that is, by default, the user's entire
    workspace, where `git clone` or `npm install` is exactly this call. Measured on a real filesystem:
    200 files 0.22s, 500 0.90s, 1000 3.40s, 2000 13.41s — and it runs in a thread after the heartbeat
    returns, so no timeout or spinner covers it: the turn simply stops.

    `_prune` was hardened against deleting on a file count, but only that half was fixed, not the
    index-churn half. Pinned on the write COUNT, not the clock — the timings are what it costs."""
    writes = {"n": 0}
    real = file_library._save_index

    def counting(idx):
        writes["n"] += 1
        return real(idx)

    monkeypatch.setattr(file_library, "_save_index", counting)
    lib.mkdir(parents=True, exist_ok=True)
    base = file_library.snapshot_mtimes(lib)
    for i in range(25):
        (lib / f"f{i}.txt").write_text("x", encoding="utf-8")

    touched = file_library.note_changes(lib, base)

    assert len(touched) == 25 and all(a == "created" for _rel, a in touched)
    assert writes["n"] == 1, f"one batch, one index write — got {writes['n']}"


def test_save_image_decodes_real_bytes(lib):
    raw = b"\x89PNG\r\n\x1a\n realish"
    data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
    file_library.save_image("shot-1.png", data_url)
    assert (lib / "shot-1.png").read_bytes() == raw
    f = next(x for x in file_library.list_all() if x["path"] == "shot-1.png")
    assert f["kind"] == "image"


def test_resolve_jails_against_traversal(lib):
    """`resolve` answers only for files inside the library: an escape attempt and an absolute path
    outside the jail are both refused, and a path that is simply missing answers None."""
    file_library.save_text("ok.txt", "hi")
    assert file_library.resolve("ok.txt") is not None
    assert file_library.resolve("../../../etc/passwd") is None
    assert file_library.resolve("/etc/passwd") is None
    assert file_library.resolve("nope.txt") is None


def test_traversal_path_on_save_falls_back_to_basename(lib):
    """A save whose path would escape the jail is written under its basename INSIDE the library,
    never outside it — the write succeeds, it just cannot choose where."""
    file_library.save_text("../../evil.txt", "x")
    assert (lib / "evil.txt").is_file()
    assert {f["path"] for f in file_library.list_all()} == {"evil.txt"}


def test_caps_truncate_text_and_skip_oversized_image(lib, monkeypatch):
    monkeypatch.setattr(file_library, "_MAX_TEXT", 10)
    monkeypatch.setattr(file_library, "_MAX_IMAGE_BYTES", 8)
    file_library.save_text("big.txt", "X" * 100)
    assert (lib / "big.txt").read_text(encoding="utf-8").endswith("… (truncated)")
    big_image = "data:image/png;base64," + base64.b64encode(b"Y" * 50).decode()
    assert file_library.save_image("huge.png", big_image) is None
    assert not (lib / "huge.png").exists()


def test_endpoints_list_and_open(lib):
    """The HTTP layer: /api/files lists the library; /api/files/open serves a real file and refuses a
    traversal path with 404 (never 500)."""
    from fastapi.testclient import TestClient

    import kotoba.server as main

    file_library.save_text("notes.md", "# hello")
    with TestClient(main.app) as c:
        listed = c.get("/api/files")
        assert listed.status_code == 200
        assert "notes.md" in {f["path"] for f in listed.json()["files"]}

        opened = c.get("/api/files/open", params={"path": "notes.md"})
        assert opened.status_code == 200 and "# hello" in opened.text

        assert c.get("/api/files/open", params={"path": "../../../etc/passwd"}).status_code == 404
        assert c.get("/api/files/open", params={"path": "missing.txt"}).status_code == 404


def test_raw_endpoint_serves_assets_and_keeps_token_out_of_url(lib, monkeypatch):
    """/api/files/raw/{path} serves assets under their real paths, so relative links inside a served
    page resolve.

    A request carrying `?token` sets the `kf` cookie and 302-redirects to the token-less URL, so the
    token never stays in the address bar or in history. Every request after that authenticates by
    cookie — the redirect target and a sibling asset the page links to alike. Traversal stays
    refused. The redirect is not followed automatically here so that it can be asserted on."""
    from fastapi.testclient import TestClient

    import kotoba.server as main

    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "pw")
    file_library.save_text("index.html", "<link href='styles.css'>")
    file_library.save_text("styles.css", "body{color:red}")
    with TestClient(main.app) as c:
        r = c.get("/api/files/raw/index.html", params={"token": "pw"}, follow_redirects=False)
        assert r.status_code == 302
        assert "token" not in r.headers["location"]
        assert "kf=" in r.headers.get("set-cookie", "")
        page = c.get("/api/files/raw/index.html")
        assert page.status_code == 200 and "styles.css" in page.text
        css = c.get("/api/files/raw/styles.css")
        assert css.status_code == 200 and "color:red" in css.text
        assert c.get("/api/files/raw/../../../etc/passwd", params={"token": "pw"}).status_code in (404, 422)


def test_prune_keeps_only_newest(lib, monkeypatch, tmp_path):
    """The cap only applies to a library that MIRRORS a separate workdir, where each file is a copy.
    With no separate workdir the library holds the user's originals and nothing may be pruned.

    The six files are seeded with distinct, increasing modification times so that "newest" is
    unambiguous, and the cap is only lowered once the seeding is done."""
    import os
    import time

    monkeypatch.setenv("KOTOBA_WORKSPACE_DIR", str(tmp_path / "work"))
    (tmp_path / "work").mkdir(exist_ok=True)
    monkeypatch.setattr(file_library, "_MAX_FILES", 1000)
    base = time.time()
    for i in range(6):
        file_library.save_text(f"f{i}.txt", "z")
        os.utime(lib / f"f{i}.txt", (base + i, base + i))
    monkeypatch.setattr(file_library, "_MAX_FILES", 3)
    file_library._prune()
    names = {f["path"] for f in file_library.list_all()}
    assert names == {"f5.txt", "f4.txt", "f3.txt"}


def test_delete_removes_file_index_and_tidies_empty_dir(lib):
    """Deleting takes the file, its index row and the parent directory the deletion left empty. A
    second delete of the same path is a no-op that answers False rather than raising."""
    file_library.save_text("notes/todo.txt", "x")
    assert file_library.delete("notes/todo.txt") is True
    assert (lib / "notes" / "todo.txt").exists() is False
    assert "notes/todo.txt" not in file_library._load_index()
    assert (lib / "notes").exists() is False
    assert file_library.delete("notes/todo.txt") is False


def test_delete_refuses_traversal_and_index(lib):
    """`delete` refuses to leave the jail and refuses the index sidecar itself; a neighbouring file
    is left untouched by either attempt."""
    file_library.save_text("keep.txt", "y")
    assert file_library.delete("../../etc/passwd") is False
    assert file_library.delete("/etc/passwd") is False
    assert file_library.delete(".index.json") is False
    assert (lib / "keep.txt").exists() is True


@pytest.mark.parametrize("spelling", ["./.index.json", "sub/../.index.json", "./.index.json.lock"])
def test_the_sidecar_guard_reads_the_resolved_name_not_the_spelling(lib, spelling):
    """`delete` compared the raw string while every neighbour in this module compares the resolved name,
    so a leading `./` walked past the sidecar guard.

    That one unlink cascades: the removed sidecar is re-read, the loader answers empty on failure, and
    the whole index is written back empty — every file's new/edited/seen state gone, with a false
    success on the way out. The `.lock` spelling covers the mutual-exclusion sidecar two processes
    coordinate through, reachable without the HTTP route since the workdir IS the library by default.

    The name is read after path validation, not before, so it matches the file that will actually be
    opened — a symlink resolving onto the index is the same attack with a different spelling."""
    file_library.save_text("keep.txt", "y")
    file_library.save_text("a/deep.txt", "z")
    (lib / file_library._LOCK_NAME).touch()
    before = dict(file_library._load_index())
    assert before, "the index has entries to lose"

    assert file_library.delete(spelling) is False
    assert file_library._load_index() == before, "the index survived intact"
    assert (lib / ".index.json").exists() and (lib / file_library._LOCK_NAME).exists()
    assert (lib / "keep.txt").exists()


def test_delete_drops_the_index_row_under_the_name_the_index_uses(lib):
    """Same root cause, quieter symptom: the row was popped under the RAW spelling, so `./notes.txt`
    unlinked the file and left its index row behind — a listing entry for a file that is gone."""
    file_library.save_text("notes.txt", "hello")
    assert "notes.txt" in file_library._load_index()

    assert file_library.delete("./notes.txt") is True
    assert (lib / "notes.txt").exists() is False
    assert "notes.txt" not in file_library._load_index()
