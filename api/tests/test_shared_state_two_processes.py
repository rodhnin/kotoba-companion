"""Everything under ~/.kotoba a second process can corrupt — which a CLI beside the server creates.

Every shared store used one fixed `.tmp` name for every process, then `replace()`. Measured with two
real processes hammering the real code: visual-memory kept 63 of 222 entries and stranded 177 images;
USER.md raised a raw FileNotFoundError for a fact it had already saved; the file-library index lost 79%
of its updates with no error, because its writer swallows every exception; and a fresh database
converting to WAL crashed one of two booting processes with "database is locked" on about 3% of first
boots. The lock must be RE-ENTRANT: flock belongs to the open file description, so a second os.open in
the same process blocks on the first forever.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from conftest import posix_only, obeys_permissions

from kotoba.core import atomic_file

API = Path(__file__).resolve().parents[1] / "src"


# --- the primitive ----------------------------------------------------------------------------------

def test_two_threads_do_not_lose_index_updates(tmp_path, monkeypatch):
    """THREADS, not just processes. The tool loop runs tools through asyncio.to_thread, so two turns plus
    a Files-panel click are three writers in one process. A first version of the guard counted
    re-entrancy in a process-global dict, so a second THREAD concluded "already ours" and skipped the
    lock: the index then kept 10 of 160 entries, with no CLI anywhere."""
    import importlib
    import threading

    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    from kotoba.core import file_library

    importlib.reload(file_library)
    d = file_library.library_dir()
    d.mkdir(parents=True, exist_ok=True)

    def worker(tag):
        for i in range(80):
            (d / f"{tag}-{i}.txt").write_text("x", encoding="utf-8")
            file_library.touch(f"{tag}-{i}.txt", "text")

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30.0)
        assert not t.is_alive(), "a writer never came back — the lock deadlocked"
    index = json.loads((d / ".index.json").read_text(encoding="utf-8"))
    assert len(index) == 160, f"kept {len(index)} of 160 index entries"


def test_threads_serialize_and_nesting_does_not(tmp_path):
    import threading
    import time

    p = tmp_path / "x.json"
    with atomic_file.exclusive(p):
        with atomic_file.exclusive(p):
            pass                      # same thread: nesting, must not block

    order = []

    def first():
        with atomic_file.exclusive(p):
            order.append("in")
            time.sleep(0.2)
            order.append("out")

    def second():
        time.sleep(0.05)
        with atomic_file.exclusive(p):
            order.append("second")

    ts = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30.0)
        assert not t.is_alive(), "a writer never came back — the lock deadlocked"
    assert order == ["in", "out", "second"], f"threads did not serialize: {order}"


@posix_only("a directory made unwritable with chmod 0500")
@obeys_permissions("a directory made unwritable")
def test_a_failed_acquisition_does_not_look_like_ownership(tmp_path):
    """The depth counter used to be incremented BEFORE the acquisition could fail, so one transient
    OSError disabled locking for the rest of the process's life."""
    import kotoba.core.atomic_file as af

    target = tmp_path / "sub" / "x.json"
    target.parent.mkdir(parents=True)
    target.parent.chmod(0o500)  # cannot create the .lock sidecar
    try:
        with pytest.raises(OSError):
            with af.exclusive(target):
                pass
    finally:
        target.parent.chmod(0o700)
    assert not af._depths().get(os.path.realpath(str(target))), "the counter leaked after a failure"


def test_the_lock_sidecar_is_not_content(tmp_path, monkeypatch):
    """It was deletable through the Files panel and counted as a prunable file — unlinking the inode
    leaves the holder's flock on an orphan and mutual exclusion silently ends."""
    import importlib

    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    from kotoba.core import file_library

    importlib.reload(file_library)
    d = file_library.library_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "notes.md").write_text("hi", encoding="utf-8")
    file_library.touch("notes.md")
    assert (d / ".index.json.lock").exists()
    assert file_library.delete(".index.json.lock") is False
    assert (d / ".index.json.lock").exists()
    assert file_library.resolve(".index.json.lock") is None
    assert [e["path"] for e in file_library.list_all()] == ["notes.md"]


def test_the_lock_is_reentrant(tmp_path):
    """A fact write re-reads and retires older facts under the same guard. Non-re-entrant flock would
    deadlock the whole process there — the suite went from 60s to over 600s when it did."""
    p = tmp_path / "x.json"
    with atomic_file.exclusive(p):
        with atomic_file.exclusive(p):
            with atomic_file.exclusive(p):
                pass
    with atomic_file.exclusive(p):  # and it must be releasable, not leaked
        pass


def test_the_temporary_is_unique_per_call(tmp_path):
    """Per-CALL, not per-process: two threads share a pid, so a pid-suffixed name reproduced the very
    FileNotFoundError this module exists to remove."""
    import threading

    p = tmp_path / "data.json"
    seen: list[str] = []
    real = os.replace

    def spy(src, dst):
        seen.append(str(src))
        return real(src, dst)

    import kotoba.core.atomic_file as af

    af.os.replace = spy
    try:
        ts = [threading.Thread(target=atomic_file.write_text, args=(p, '{"a": %d}' % i)) for i in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30.0)
            assert not t.is_alive(), "a writer never came back — the lock deadlocked"
    finally:
        af.os.replace = real
    # One name per WRITER, not one call per name: on Windows a refused rename is retried with the same
    # temporary, so counting calls counted the retries and failed on the platform it was written for.
    assert len(set(seen)) == len(ts), f"two writers shared a temp name: {seen}"
    assert not list(tmp_path.glob("*.tmp")), "the temporary must be cleaned up"


def test_the_lock_file_is_a_sibling_not_the_target(tmp_path):
    """Locking the target itself is lost the moment replace() swaps the inode."""
    p = tmp_path / "data.json"
    with atomic_file.exclusive(p):
        pass
    assert (tmp_path / "data.json.lock").exists()
    assert not p.exists(), "the lock must not create the target"


# --- the three stores, driven by two real processes ---------------------------------------------------

_CHILD = """
import os, sys
which, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
PNG = bytes([137,80,78,71,13,10,26,10]) + bytes(24)
if which == "vis":
    from kotoba.core import visual_memory as m
    for i in range(n):
        m.add(f"{tag}-{i}", "x", "other", PNG, "png")
elif which == "lib":
    from kotoba.core import file_library as m
    d = m.library_dir(); d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"{tag}-{i}.txt").write_text("hola", encoding="utf-8")
        m.touch(f"{tag}-{i}.txt", "text")
"""


def _unexpected(err: str) -> str:
    """Everything the child said EXCEPT the lock reporting contention it then survived.

    "waiting for the lock — another writer is holding it" is the diagnostic this module exists to
    print, and the property under test is that nothing raised and nothing was lost. Windows is slower
    under contention and crosses the warning threshold routinely, so treating any stderr as a failure
    turned a working lock into two red tests."""
    kept = [ln for ln in err.splitlines()
            if ln.strip() and "waiting for the lock on" not in ln]
    return "\n".join(kept).strip()


def _race(which: str, n: int, env_extra: dict) -> list[str]:
    base = Path(tempfile.mkdtemp())
    child = base / "child.py"
    child.write_text(_CHILD, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(API), **env_extra)
    env.pop("KOTOBA_MASTER_KEY", None)
    procs = [
        subprocess.Popen([sys.executable, str(child), which, tag, str(n)],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for tag in ("A", "B")
    ]
    errors = []
    for p in procs:
        _out, err = p.communicate(timeout=180)
        said = _unexpected(err)
        if said:
            errors.append(said[-400:])
    return errors


def test_two_processes_do_not_lose_visual_memories(tmp_path):
    d = tmp_path / "vm"
    errors = _race("vis", 60, {"KOTOBA_VISUAL_MEMORY_DIR": str(d)})
    assert not errors, errors
    index = json.loads((d / "index.json").read_text(encoding="utf-8"))
    images = list((d / "images").glob("*"))
    assert len(index) == 120, f"kept {len(index)} of 120 keepsakes"
    assert len(images) == len(index), f"{len(images) - len(index)} images stranded with no index entry"


def test_two_processes_do_not_lose_library_index_entries(tmp_path):
    d = tmp_path / "files"
    errors = _race("lib", 60, {"KOTOBA_FILES_DIR": str(d)})
    assert not errors, errors
    index = json.loads((d / ".index.json").read_text(encoding="utf-8"))
    on_disk = list(d.glob("*.txt"))
    assert len(on_disk) == 120
    assert len(index) == 120, f"{120 - len(index)} index updates lost, and _save_index reports nothing"


# --- a fresh database, two processes booting together --------------------------------------------------

def test_a_concurrent_first_boot_does_not_crash(tmp_path):
    """PRAGMA journal_mode=WAL takes an exclusive lock and returns SQLITE_BUSY in ~3ms instead of waiting,
    and busy_timeout used to be set on the line AFTER it."""
    child = tmp_path / "boot.py"
    child.write_text(
        "import asyncio, sys\n"
        "from kotoba.db.database import Database\n"
        "async def go():\n"
        "    db = Database('sqlite:///' + sys.argv[1])\n"
        "    await db.connect()\n"
        "    await db.close()\n"
        "asyncio.run(go())\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PYTHONPATH=str(API))
    failures = []
    for trial in range(12):
        db_path = tmp_path / f"fresh-{trial}.db"
        procs = [
            subprocess.Popen([sys.executable, str(child), str(db_path)],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        for p in procs:
            _o, err = p.communicate(timeout=60)
            if p.returncode != 0:
                failures.append(err.strip()[-300:])
    assert not failures, f"{len(failures)}/24 concurrent first boots died:\n" + "\n".join(failures[:2])


# --- memory keeps a fact and its opposite ---------------------------------------------------------------

def test_a_negated_fact_is_not_a_duplicate_of_its_opposite(tmp_path, monkeypatch):
    """"le gusta el sushi" and "NO le gusta el cilantro" scored exactly at the dedup threshold, so the
    second was discarded as a repeat of the first. A fact and its opposite are never the same fact."""
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path))
    import importlib

    from kotoba.core import user_memory

    importlib.reload(user_memory)
    assert user_memory.append_fact("A Jordan le gusta el sushi") is True
    assert user_memory.append_fact("A Jordan no le gusta el cilantro") is True
    assert user_memory.append_fact("Jordan nunca bebe café") is True
    # and a genuine reworded repeat is still collapsed (a SUPERSET is deliberately let through as a
    # refinement, so the repeat has to carry no new keyword)
    assert user_memory.append_fact("el sushi le gusta a Jordan") is False


def test_two_processes_can_write_memory_without_raising(tmp_path):
    """The user-visible half: memory_write raised a raw FileNotFoundError for a fact it HAD saved."""
    child = tmp_path / "mem.py"
    child.write_text(
        "import sys\n"
        "from kotoba.core import user_memory as m\n"
        "for i in range(40):\n"
        "    m.append_fact(f'{sys.argv[1]} distinct durable fact {i} about topic {i % 7}')\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PYTHONPATH=str(API), KOTOBA_MEMORY_DIR=str(tmp_path / "mem"))
    procs = [subprocess.Popen([sys.executable, str(child), tag], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for tag in ("A", "B")]
    for p in procs:
        _o, err = p.communicate(timeout=180)
        assert not _unexpected(err), _unexpected(err)[-400:]
    assert (tmp_path / "mem" / "USER.md").read_text(encoding="utf-8").strip(), "the index must survive"
