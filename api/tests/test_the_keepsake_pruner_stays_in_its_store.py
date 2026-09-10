"""The pruner is the third writer that touches `file`, and the only one that trusted it.

`file` is written from model-supplied data. `_entry_file()` basenames it and re-checks the jail before
reading, and `delete()` basenames it before unlinking — but `add()`'s prune, which drops the oldest
keepsakes once the store is full, joined it raw. A doctored index therefore deleted outside the store.
"""
from __future__ import annotations

import json

from kotoba.core import visual_memory


def _fill_index(rows):
    visual_memory.images_dir().mkdir(parents=True, exist_ok=True)
    visual_memory._index_path().write_text(json.dumps(rows), encoding="utf-8")


def test_a_doctored_entry_cannot_reach_outside_the_store(tmp_path, monkeypatch):
    outside = visual_memory.images_dir().parent.parent / "not-a-keepsake.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("this belongs to the user", encoding="utf-8")

    monkeypatch.setattr(visual_memory, "_MAX_ENTRIES", 1)
    _fill_index([
        {"id": "a", "about": "x", "note": "n", "kind": "k", "file": "../../not-a-keepsake.txt"},
        {"id": "b", "about": "x", "note": "n", "kind": "k", "file": "b.png"},
    ])
    (visual_memory.images_dir() / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    visual_memory.add(about="x", note="fresh", kind="k", raw=b"\x89PNG\r\n\x1a\n")

    assert outside.exists(), "the pruner deleted a file outside the keepsake store"
