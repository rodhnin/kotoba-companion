"""A recalled keepsake must reach the SCREEN, not only the vision model.

`recall_image` once returned the right image, she described it and said "here it is" — and the DOM held
no image at all, since the only route back to the browser was the reply text. The fix is two halves:
`recall_image` now emits a `recalled_image` frame carrying the keepsake's ID, never its bytes (that
queue also carries approval cards), and an endpoint serves the bytes behind the same gate as every
other route. Its whole security argument is that the caller names an ID, never a path — the filename is
read from her index and re-jailed before anything opens. A miss stays a miss: no frame, nothing shown,
and she keeps saying so honestly.
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

import kotoba.core.visual_memory as vm
import kotoba.server as main
from kotoba.core import events

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    # conftest already redirects the store per test; pin it again so this file never depends on that.
    monkeypatch.setenv("KOTOBA_VISUAL_MEMORY_DIR", str(tmp_path / "vmem"))
    with TestClient(main.app) as c:
        yield c


def test_serves_the_exact_keepsake_bytes(client):
    e = vm.add("the user", "their chibi", "self", _PNG, "png", source="attachment:chibi.png")
    r = client.get(f"/api/visual-memory/{e['id']}")
    assert r.status_code == 200
    assert r.content == _PNG
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers.get("x-content-type-options") == "nosniff"


def test_unknown_id_is_404_not_a_hint(client):
    vm.add("the user", "their chibi", "self", _PNG)
    assert client.get("/api/visual-memory/deadbeef99").status_code == 404


def test_gated_like_every_other_api_route(client, monkeypatch):
    e = vm.add("the user", "their chibi", "self", _PNG)
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    url = f"/api/visual-memory/{e['id']}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer letmein"}).status_code == 200
    # An <img> can't set a header — the token rides the query, as it does for the Files viewer.
    assert client.get(url, params={"token": "letmein"}).status_code == 200
    monkeypatch.setenv("KOTOBA_API_KEY", "el_secret")
    assert client.get(url, params={"token": "el_secret"}).status_code == 401


@pytest.mark.parametrize("attempt", [
    "../../../etc/passwd",
    "..%2F..%2Fetc%2Fpasswd",
    "....//....//etc/passwd",
    "/etc/passwd",
    "index.json",
    "a" * 200,
])
def test_never_reads_a_path_the_caller_chose(client, attempt):
    vm.add("the user", "their chibi", "self", _PNG)
    r = client.get(f"/api/visual-memory/{attempt}")
    assert r.status_code == 404, r.status_code
    assert b"root:" not in r.content and b'"about"' not in r.content


def test_a_doctored_index_cannot_point_outside_the_store(client, tmp_path):
    # `file` is written from model-supplied data, so the jail check runs on the way OUT too.
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"not yours")
    e = vm.add("the user", "their chibi", "self", _PNG)
    entries = json.loads((vm.memory_dir() / "index.json").read_text(encoding="utf-8"))
    entries[0]["file"] = f"../../{secret.name}"
    (vm.memory_dir() / "index.json").write_text(json.dumps(entries))
    r = client.get(f"/api/visual-memory/{e['id']}")
    assert r.status_code == 404 and b"not yours" not in r.content


def test_a_stored_svg_is_not_served_as_a_document(client):
    # The attachment picker takes any image/*, and this URL carries the gate token in its query.
    e = vm.add("logo", "a mark", "other", b"<svg onload=alert(1)></svg>", "svg")
    r = client.get(f"/api/visual-memory/{e['id']}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/octet-stream")


def test_recall_emits_one_frame_per_image_she_sees():
    class _Ctx:
        session_id = "vm-sess"
        run_id = "r-1"

    import kotoba.tools.builtin.recall_image as rc

    async def go():
        kept = [vm.add("Fulano Pérez", f"photo {i}", "person", _PNG) for i in range(4)]
        q = events.register("vm-sess")
        res = await rc.execute({"query": "Fulano Pérez"}, _Ctx())
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("vm-sess")
        return kept, res, frames

    kept, res, frames = asyncio.run(go())
    imgs = [f for f in frames if f.get("kind") == "recalled_image"]
    assert len(imgs) == len(res.images) == 3, "the screen shows what the model was given, no more"
    assert all(f["id"] in {k["id"] for k in kept} for f in imgs)
    assert all(f["about"] == "Fulano Pérez" and f["run_id"] == "r-1" for f in imgs)
    assert not any("data:" in json.dumps(f) for f in imgs), "the bytes must not ride the events queue"


def test_the_emitted_id_is_the_one_the_endpoint_serves(client):
    class _Ctx:
        session_id = "vm-sess2"

    import kotoba.tools.builtin.recall_image as rc

    async def go():
        vm.add("the user", "their chibi", "self", _PNG)
        q = events.register("vm-sess2")
        await rc.execute({"query": "the user"}, _Ctx())
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("vm-sess2")
        return frames

    frames = asyncio.run(go())
    ids = [f["id"] for f in frames if f.get("kind") == "recalled_image"]
    assert ids
    assert client.get(f"/api/visual-memory/{ids[0]}").content == _PNG


def test_a_miss_shows_nothing_and_stays_honest():
    class _Ctx:
        session_id = "vm-sess3"

    import kotoba.tools.builtin.recall_image as rc

    async def go():
        vm.add("Fulano Pérez", "his photo", "person", _PNG)
        q = events.register("vm-sess3")
        res = await rc.execute({"query": "zzzznothing"}, _Ctx())
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("vm-sess3")
        return res, frames

    res, frames = asyncio.run(go())
    assert res is None                     # → the tool's FAIL line, which is the honest one
    assert frames == []


def test_notes_only_recall_shows_nothing(monkeypatch):
    """The image is gone from disk but the note survives: she may still answer from the note, and the
    screen must not promise a picture that the endpoint would 404 on."""
    class _Ctx:
        session_id = "vm-sess4"

    import kotoba.tools.builtin.recall_image as rc

    async def go():
        e = vm.add("the user", "their chibi", "self", _PNG)
        (vm.images_dir() / e["file"]).unlink()
        q = events.register("vm-sess4")
        res = await rc.execute({"query": "the user"}, _Ctx())
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("vm-sess4")
        return res, frames

    res, frames = asyncio.run(go())
    assert isinstance(res, str) and "their chibi" in res
    assert frames == []
