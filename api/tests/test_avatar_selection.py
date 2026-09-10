"""`avatar_model` stops being a phantom.

The column existed end to end — soul frontmatter, loader, migration, queries — and nothing had ever
read it. The frontend chose its model from `NEXT_PUBLIC_LIVE2D_MODEL`, which Next inlines at `next
build`, so a prebuilt image could not change model and no setup screen could ever pick one. What is
pinned here: the endpoint the frontend asks at startup, that a chosen model SURVIVES A RESTART (the
sync used to treat this column as developer-authored and overwrite it), and that the shipped default
no longer names a model nobody may redistribute.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import kotoba.server as main
from kotoba.db.database import Database
from kotoba.soul import loader


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / "models"
    (root / "mao_pro" / "runtime").mkdir(parents=True)
    (root / "mao_pro" / "runtime" / "mao_pro.model3.json").write_text("{}", encoding="utf-8")
    (root / "free1").mkdir()
    (root / "free1" / "free1.model3.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("KOTOBA_MODELS_DIR", str(root))
    return root


@pytest.fixture
def client(installed):
    with TestClient(main.app) as c:
        yield c


def test_the_endpoint_says_what_is_installed_and_which_one_answers(client):
    d = client.get("/api/avatar").json()
    assert [m["dir"] for m in d["installed"]] == ["free1", "mao_pro"]
    assert d["selected"]["dir"] in ("free1", "mao_pro")
    assert d["models_dir"].endswith("models")


def test_a_fresh_install_with_no_model_asks_for_one_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_MODELS_DIR", str(tmp_path / "nothing-here"))
    with TestClient(main.app) as c:
        d = c.get("/api/avatar").json()
    assert d["installed"] == []
    assert d["selected"] is None
    assert d["models_dir"].endswith("nothing-here"), "the empty case must be able to name the folder"


def test_the_settings_panel_is_told_the_same_thing(client):
    """The panel is where the choice is made, so it needs the list in the payload it already reads."""
    avatar = client.get("/api/settings").json()["avatar"]
    assert [m["dir"] for m in avatar["installed"]] == ["free1", "mao_pro"]
    assert avatar["selected"]["entry"].endswith(".model3.json")
    assert avatar["models_dir"].endswith("models")


def test_choosing_a_model_is_what_the_endpoint_then_reports(client):
    assert client.post("/api/settings/avatar", json={"model": "free1"}).json()["ok"] is True
    assert client.get("/api/avatar").json()["selected"]["dir"] == "free1"
    assert client.post("/api/settings/avatar", json={"model": "mao_pro"}).json()["ok"] is True
    assert client.get("/api/avatar").json()["selected"]["dir"] == "mao_pro"


def test_a_model_nobody_installed_cannot_be_chosen(client):
    """The value drives a URL the browser will fetch; a stored one that 404s is the phantom again."""
    r = client.post("/api/settings/avatar", json={"model": "../../etc"})
    assert r.status_code == 400
    assert client.post("/api/settings/avatar", json={"model": "not_installed"}).status_code == 400


def test_a_chosen_model_survives_a_restart(tmp_path):
    """The startup sync refreshes developer-authored fields from soul/default.md. `avatar_model` was
    in that list, so every restart quietly put the shipped default back — exactly what already had to
    be fixed for the chosen voice."""
    async def go():
        db = Database("sqlite:///" + str(tmp_path / "t.db"))
        await db.connect()
        try:
            await loader.sync_from_file(db, loader.DEFAULT_SOUL_PATH)
            await db.update_soul_config(avatar_model="free1/free1.model3.json")
            await loader.sync_from_file(db, loader.DEFAULT_SOUL_PATH)
            return await db.fetch_soul_config()
        finally:
            await db.close()

    assert _run(go())["avatar_model"] == "free1/free1.model3.json"


def test_the_soul_file_still_seeds_a_database_that_has_no_answer(tmp_path):
    async def go():
        db = Database("sqlite:///" + str(tmp_path / "t2.db"))
        await db.connect()
        try:
            await loader.sync_from_file(db, loader.DEFAULT_SOUL_PATH)
            return await db.fetch_soul_config()
        finally:
            await db.close()

    assert _run(go())["avatar_model"] == "mao_pro/runtime/mao_pro.model3.json"


def test_the_shipped_default_names_a_model_that_may_be_fetched():
    """free1 has no redistribution licence, so an installer can never obtain it for a stranger. The
    default has to name one that can be."""
    assert loader._DEFAULTS["avatar_model"] == "mao_pro/runtime/mao_pro.model3.json"
    assert "free1" not in loader.load_soul_from_file(loader.DEFAULT_SOUL_PATH)["avatar_model"]
