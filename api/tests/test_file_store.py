"""file_store — the in-memory snapshot used to REHYDRATE a fresh sandbox so cross-turn edits survive a
sandbox recreate/reap. (The durable, browsable copy of files lives in the on-disk library.)"""
from __future__ import annotations

from kotoba.core import file_store


def test_stash_get_and_caps():
    file_store.clear("s1")
    file_store.stash("s1", "a.py", "print(1)")
    assert file_store.get("s1", "a.py") == "print(1)"
    assert file_store.get("s1", "missing.py") is None
    assert file_store.get(None, "a.py") is None
    # oversize is truncated, not dropped
    file_store.stash("s1", "big.txt", "X" * 300_000)
    assert "truncated" in file_store.get("s1", "big.txt")
    file_store.clear("s1")
    assert file_store.get("s1", "a.py") is None


def test_text_items_feed_rehydration():
    """text_items() returns (path, content) for TEXT files only — what session_sandbox uses to rebuild a
    fresh sandbox's working dir. Images are excluded (they're not source to re-create)."""
    file_store.clear("s2")
    file_store.stash("s2", "app.py", "print(1)")
    file_store.stash_image("s2", "shot.png", "data:image/png;base64,QUJD")
    items = dict(file_store.text_items("s2"))
    assert items == {"app.py": "print(1)"}  # only the text file
    file_store.clear("s2")
