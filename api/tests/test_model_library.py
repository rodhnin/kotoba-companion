"""The models directory is handed to a browser, so two refusals decide what leaves it.

A Live2D model is somebody's unpacked download sitting in a folder the app serves under real paths —
Cubism resolves textures, motions and expressions RELATIVE to the entry file, so the whole directory
has to be reachable. That makes two questions load-bearing: can a request name a path outside the
directory, and can it name a file a browser would run on this origin. Both are answered here against
real files on disk, escapes included.
"""
from __future__ import annotations

import pytest
from conftest import make_symlink

from kotoba.core import model_library


@pytest.fixture
def models(tmp_path, monkeypatch):
    root = tmp_path / "models"
    (root / "mao_pro" / "runtime").mkdir(parents=True)
    (root / "mao_pro" / "runtime" / "mao_pro.model3.json").write_text("{}", encoding="utf-8")
    (root / "mao_pro" / "runtime" / "texture_00.png").write_bytes(b"\x89PNG")
    (root / "mao_pro" / "runtime" / "mao_pro.moc3").write_bytes(b"MOC3")
    monkeypatch.setenv("KOTOBA_MODELS_DIR", str(root))
    (tmp_path / "secret.txt").write_text("not a model", encoding="utf-8")
    # Servable by TYPE and outside the directory, so only the jail can refuse it.
    (tmp_path / "outside.png").write_bytes(b"\x89PNG")
    return root


def test_the_files_a_model_is_made_of_are_served(models):
    for rel in ("mao_pro/runtime/mao_pro.model3.json", "mao_pro/runtime/texture_00.png",
                "mao_pro/runtime/mao_pro.moc3"):
        assert model_library.resolve(rel) is not None, rel


@pytest.mark.parametrize("escape", [
    "../outside.png",
    "mao_pro/../../outside.png",
    "mao_pro/runtime/../../../outside.png",
    "./../outside.png",
    "../secret.txt",
    "mao_pro/../../secret.txt",
])
def test_no_path_leaves_the_models_directory(models, escape):
    assert model_library.resolve(escape) is None, escape


def test_an_absolute_path_reaches_nothing_outside(models, tmp_path):
    assert model_library.resolve(str(tmp_path / "outside.png")) is None
    assert model_library.resolve("/etc/passwd") is None


def test_a_symlink_out_of_the_directory_is_refused(models, tmp_path):
    link = models / "mao_pro" / "runtime" / "escape.png"
    make_symlink(link, tmp_path / "outside.png")
    assert model_library.resolve("mao_pro/runtime/escape.png") is None


@pytest.mark.parametrize("name", ["evil.html", "evil.js", "evil.svg", "evil.xhtml", "licence.txt"])
def test_nothing_a_browser_would_run_is_served(models, name):
    (models / "mao_pro" / name).write_bytes(b"<script>fetch('/api/settings')</script>")
    assert model_library.resolve(f"mao_pro/{name}") is None, name


def test_a_media_type_is_stated_and_never_guessed(models):
    assert model_library.media_type_for(models / "a.model3.json") == "application/json"
    assert model_library.media_type_for(models / "a.png") == "image/png"
    assert model_library.media_type_for(models / "a.moc3") == "application/octet-stream"


def test_an_installed_model_is_found_by_its_entry_file(models):
    assert model_library.installed() == [{"dir": "mao_pro", "entry": "runtime/mao_pro.model3.json"}]


def test_a_folder_with_no_entry_file_is_not_a_model(models):
    (models / "notes").mkdir()
    (models / "notes" / "readme.md").write_text("hi", encoding="utf-8")
    assert [m["dir"] for m in model_library.installed()] == ["mao_pro"]


def test_nothing_installed_is_an_empty_list_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_MODELS_DIR", str(tmp_path / "never-created"))
    assert model_library.installed() == []


def test_the_configured_model_is_the_one_selected(models):
    (models / "free1").mkdir()
    (models / "free1" / "free1.model3.json").write_text("{}", encoding="utf-8")
    got = model_library.selection("free1/free1.model3.json", model_library.installed())
    assert got == {"dir": "free1", "entry": "free1.model3.json"}


def test_a_configured_model_nobody_installed_does_not_leave_her_faceless(models):
    """The shipped default names a model the repo may not carry, so this is the FRESH-INSTALL path,
    not an edge case: one model on disk and a default pointing elsewhere must still draw a face."""
    got = model_library.selection("something-else/x.model3.json", model_library.installed())
    assert got == {"dir": "mao_pro", "entry": "runtime/mao_pro.model3.json"}


def test_with_nothing_installed_there_is_no_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_MODELS_DIR", str(tmp_path / "empty"))
    assert model_library.selection("mao_pro/runtime/mao_pro.model3.json", []) is None
