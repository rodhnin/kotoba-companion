"""Durable VISUAL MEMORY: keepsake images, the visual twin of USER.md.

Each entry is tied to an entity and a note, persisted on disk, and recallable across sessions —
unlike a session capture, which dies with the call. The `remember_image` and `recall_image` tools
are covered here too. The names in the fixtures are placeholders ("Fulano Pérez" is the Spanish
equivalent of "John Doe"); the accented one is there because the search has to match without
accents."""
from __future__ import annotations

import asyncio
import base64

import pytest

import kotoba.core.visual_memory as vm

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_DATA_URL = "data:image/png;base64," + base64.b64encode(_PNG).decode()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_VISUAL_MEMORY_DIR", str(tmp_path / "vmem"))
    return vm


def test_add_persists_entry_and_image(store):
    """Adding writes the image to disk, hands it back as a data URL, and persists the index row."""
    e = store.add("Fulano Pérez", "His profile photo; looks stern", "person", _PNG, "png", source="screenshot-3.png")
    assert e and e["about"] == "Fulano Pérez" and e["kind"] == "person"
    assert (store.images_dir() / e["file"]).is_file()
    assert store.image_data_url(e).startswith("data:image/")
    assert len(store.all_entries()) == 1


def test_search_accent_insensitive_and_by_entity(store):
    """Search matches a name typed without its accents, matches keywords in the note, and answers
    an empty list — never an error — for a query nothing was saved under."""
    store.add("Fulano Pérez", "stern profile photo", "person", _PNG)
    store.add("Jordan", "the user, smiling", "self", _PNG)
    store.add_data_url("Aurora headphones", "a product the user likes", "product", _DATA_URL)
    hits = store.search("fulano perez")
    assert hits and hits[0]["about"] == "Fulano Pérez"
    assert any(h["about"] == "Aurora headphones" for h in store.search("product"))
    assert store.search("zzzznothing") == []


def test_prompt_block_summarizes_entities(store):
    assert store.prompt_block() == ""
    store.add("Fulano Pérez", "photo", "person", _PNG)
    store.add("Fulano Pérez", "another photo", "person", _PNG)
    store.add("Jordan", "the user", "self", _PNG)
    block = store.prompt_block()
    assert "VISUAL MEMORY" in block and "Fulano Pérez (2)" in block and "Jordan (1)" in block
    assert "recall_image" in block


def test_delete_removes_entry_and_file(store):
    e = store.add("X", "n", "other", _PNG)
    assert store.delete(e["id"]) is True
    assert store.all_entries() == [] and not (store.images_dir() / e["file"]).exists()


def test_remember_image_tool_from_files(store, tmp_path, monkeypatch):
    """`remember_image` pulls the bytes of a Files capture by name and copies them into durable
    visual memory. A `camera` source is not that path: it answers with a plain not-yet message and
    saves nothing."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    (tmp_path / "files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "files" / "screenshot-3.png").write_bytes(_PNG)
    import kotoba.tools.builtin.remember_image as ri

    class _Ctx:
        session_id = "s1"

    out = asyncio.run(ri.execute(
        {"source": "screenshot-3.png", "about": "Fulano Pérez", "note": "his photo", "kind": "person"}, _Ctx()))
    assert "visual memory" in out.lower() and "Fulano Pérez" in out
    assert any(e["about"] == "Fulano Pérez" for e in store.all_entries())

    out2 = asyncio.run(ri.execute({"source": "camera", "about": "Jordan"}, _Ctx()))
    assert "camera" in out2.lower()


def test_remember_image_resolves_capture_in_subdir(store, tmp_path, monkeypatch):
    """A capture is found by basename even though it lives in a subdirectory.

    Screenshots are filed under screenshots/<source>/, and the lookup used to search the library
    root only, so it could NEVER find one — `remember_image` failed on exactly the captures worth
    keeping. The basename is all the model has, because that is what the visual-memory listing
    shows it."""
    from kotoba.core import file_library
    root = tmp_path / "files"
    (root / "screenshots" / "browser").mkdir(parents=True)
    (root / "screenshots" / "browser" / "screenshot-7-abc.png").write_bytes(_PNG)
    monkeypatch.setattr(file_library, "library_dir", lambda: root)
    import kotoba.tools.builtin.remember_image as ri

    class _Ctx:
        session_id = "s2"

    out = asyncio.run(ri.execute(
        {"source": "screenshot-7-abc.png", "about": "koi pond", "kind": "place"}, _Ctx()))
    assert "visual memory" in out.lower() and "koi pond" in out
    assert any(e["about"] == "koi pond" for e in store.all_entries())


def test_dethumbnail_strips_fb_blur_transform():
    """A CDN thumbnail URL carries the downscale-and-blur transform in its `stp=` parameter.

    Dropping just that parameter, and keeping every other one, is what turns the link into the
    original image. A URL with no such transform is left alone and answers None."""
    import kotoba.tools.builtin.remember_image as ri
    blurred = "https://scontent.fbcdn.net/v/t39/653_n.jpg?stp=dst-jpg_fb50_s320x320&_nc_cat=1&oh=x"
    full = ri._dethumbnail(blurred)
    assert full is not None and "stp=" not in full and "_nc_cat=1" in full
    assert ri._dethumbnail("https://x.com/a.jpg?foo=1") is None


def test_remember_image_from_url_saves_original(store, monkeypatch):
    """The preferred path while WORKING: a direct image URL is fetched for its ORIGINAL bytes,
    rather than keeping a screenshot of the viewport it was seen in.

    Mid-call the same path is refused instead."""
    import kotoba.tools.builtin.remember_image as ri

    async def fake_fetch(url):
        assert url.startswith("https://")
        return _PNG, "png"

    monkeypatch.setattr(ri, "_fetch_image", fake_fetch)

    class _Ctx:
        session_id = "s2"
        mode = "work"

    out = asyncio.run(ri.execute(
        {"source": "https://cdn.example/fulano.jpg", "about": "Fulano Pérez", "note": "profile photo",
         "kind": "person"}, _Ctx()))
    assert "visual memory" in out.lower()
    e = [x for x in store.all_entries() if x["about"] == "Fulano Pérez"]
    assert e and e[0]["source"].startswith("https://")


def test_recall_image_tool_returns_images(store):
    """`recall_image` hands back the images themselves as data URLs, and answers None — not an
    error — when nothing was ever saved for the query."""
    store.add("Fulano Pérez", "stern profile photo", "person", _PNG)
    import kotoba.tools.builtin.recall_image as rc

    class _Ctx:
        session_id = "s1"

    res = asyncio.run(rc.execute({"query": "Fulano Pérez"}, _Ctx()))
    assert res is not None and res.images and res.images[0].startswith("data:image/")
    assert "Fulano Pérez" in res.text
    assert asyncio.run(rc.execute({"query": "nobody"}, _Ctx())) is None
