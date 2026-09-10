"""view_capture must find captures saved in SUBDIRS (screenshots/browser/, screenshots/blender/, …), not
only the library root. Regression for the live bug where `view_capture ok=False` repeated 4× because the
shot lived in screenshots/browser/ — she couldn't see it and confabulated the image's contents."""
from __future__ import annotations

import base64

import pytest


@pytest.fixture
def lib(tmp_path, monkeypatch):
    from kotoba.core import file_library

    root = tmp_path / "files"
    (root / "screenshots" / "browser").mkdir(parents=True)
    monkeypatch.setattr(file_library, "library_dir", lambda: root)
    # a tiny valid PNG (1x1) so media_type_for → image/png
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    (root / "screenshots" / "browser" / "screenshot-2-29fa01.png").write_bytes(png)
    (root / "root-shot.png").write_bytes(png)
    return root


def test_finds_capture_in_subdir_by_full_relpath(lib):
    from kotoba.tools.builtin.view_capture import _resolve_in_library

    hit = _resolve_in_library("screenshots/browser/screenshot-2-29fa01.png")
    assert hit is not None and hit.name == "screenshot-2-29fa01.png"


def test_finds_capture_in_subdir_by_basename(lib):
    from kotoba.tools.builtin.view_capture import _resolve_in_library

    # the model often passes just the basename (its VISUAL MEMORY list shows names) — must still resolve
    hit = _resolve_in_library("screenshot-2-29fa01.png")
    assert hit is not None and hit.name == "screenshot-2-29fa01.png"


def test_finds_capture_in_root(lib):
    from kotoba.tools.builtin.view_capture import _resolve_in_library

    assert _resolve_in_library("root-shot.png") is not None


def test_load_returns_image_data_url(lib):
    from kotoba.tools.builtin.view_capture import _load

    data_url, name = _load("screenshot-2-29fa01.png")
    assert data_url and data_url.startswith("data:image/") and name == "screenshot-2-29fa01.png"


def test_rejects_path_escape(lib):
    from kotoba.tools.builtin.view_capture import _resolve_in_library

    for bad in ("../../../etc/passwd", "/etc/passwd", "../secret"):
        assert _resolve_in_library(bad) is None


def test_missing_returns_none(lib):
    from kotoba.tools.builtin.view_capture import _resolve_in_library, _load

    assert _resolve_in_library("does-not-exist.png") is None
    assert _load("does-not-exist.png") == (None, None)
