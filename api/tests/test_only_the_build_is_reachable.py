"""What a browser may pull out of the packaged frontend, and nothing else.

The fixture tree is built here rather than read from a real build: a suite that passes or fails
depending on whether somebody ran the frontend build is not a suite. Shapes are copied from a real
export, including the chunk whose name genuinely contains two dots.
"""
from __future__ import annotations

import pytest
from conftest import make_symlink

from kotoba.core import frontend
from kotoba.core.frontend import Access

TREE = [
    "app.html", "app.txt", "app/__next._full.txt", "app/__next._tree.txt",
    "setup.html", "setup.txt", "setup/__next._full.txt",
    "login.html", "login.txt", "login/__next._full.txt",
    "404.html", "_not-found.html", "_not-found.txt",
    "icon.svg", "live2dcubismcore.min.js",
    "_next/static/chunks/main.js", "_next/static/chunks/0k90..qj97h~j.js",
    "_next/static/css/a.css", "_next/static/media/f.woff2",
    "worklets/mic-capture.js", "subagents/chibi.png", "scene/room.webp", "art/chibi.webp",
    "index.html", "index.txt", "__next._full.txt", "__next.__PAGE__.txt",
]


@pytest.fixture
def built(tmp_path, monkeypatch):
    for rel in TREE:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(frontend, "_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def unbuilt(monkeypatch):
    monkeypatch.setattr(frontend, "_ROOT", None)


def test_every_file_in_the_build_is_classified_on_purpose(built):
    """Nothing falls through: a new page that lands in neither set is denied, loudly, right here."""
    got = {rel: frontend.classify("/" + rel) for rel in TREE}
    gated = {r for r, a in got.items() if a is Access.GATED}
    denied = {r for r, a in got.items() if a is Access.DENIED}
    assert gated == {"app.html", "app.txt", "app/__next._full.txt", "app/__next._tree.txt",
                     "setup.html", "setup.txt", "setup/__next._full.txt"}
    assert denied == {"index.html", "index.txt", "__next._full.txt", "__next.__PAGE__.txt"}, (
        "the root shell is the page that only ever redirected; serving it shows an error page")
    assert len(got) == len(TREE) and Access.PUBLIC in got.values()


@pytest.mark.parametrize("url", [
    "/app", "/app/", "/setup", "/app.html", "/app.txt", "/app/__next._full.txt",
])
def test_every_spelling_of_a_gated_page_is_gated(built, url):
    assert frontend.classify(url) is Access.GATED


@pytest.mark.parametrize("url", [
    "/APP.HTML", "/App.html", "/app.html.", "/./app", "/app/../app.html", "/app/../../app.html",
])
def test_another_spelling_is_not_a_way_around_the_gate(built, url):
    """A case-insensitive filesystem opens `app.html` for `/APP.HTML`, so a URL-prefix rule would let
    it through ungated. Falling off both lists has to mean refused, never allowed."""
    assert frontend.classify(url) is not Access.PUBLIC


def test_the_answer_follows_the_bytes_reached_and_not_the_url(built):
    """The property a case-insensitive filesystem would otherwise expose, provable on any filesystem:
    a public name that reaches a gated file is gated. Classifying the spelling reads this as public."""
    make_symlink(built / "login" / "sneak.html", built / "app.html")
    assert frontend.classify("/login/sneak.html") is Access.GATED


def test_a_wheel_never_serves_out_of_the_home_directory(tmp_path, monkeypatch):
    """From a wheel `REPO_ROOT` is the home that also holds her file library, so the build-directory
    fallback has to be refused there — a public static server aimed at it would serve her files."""
    monkeypatch.delenv("KOTOBA_FRONTEND_DIR", raising=False)
    built = tmp_path / ".next-export"
    built.mkdir()
    (built / "app.html").write_text("<!doctype html>", encoding="utf-8")
    # The packaged branch is asked FIRST, and this tree now really has one — without aiming it
    # somewhere empty this test would pass on the packaged answer and never reach the fallback.
    monkeypatch.setattr(frontend.paths, "PACKAGE_DIR", tmp_path / "nowhere")
    monkeypatch.setattr(frontend.paths, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(frontend.paths, "_CLONE", tmp_path)
    assert frontend._find_root() == built
    monkeypatch.setattr(frontend.paths, "_CLONE", None)
    assert frontend._find_root() is None


def test_a_chunk_whose_name_contains_two_dots_is_still_served(built):
    """A substring test for `..` refuses a file the app needs, and the app goes blank with a 404
    nobody would think to look for."""
    url = "/_next/static/chunks/0k90..qj97h~j.js"
    assert frontend.classify(url) is Access.PUBLIC
    assert frontend.resolve(url) is not None


@pytest.mark.parametrize("url", [
    "/../../etc/passwd", "/_next/../../etc/passwd", "/..%2f..%2fetc/passwd",
    "/_next/..\\..\\win.ini", "/etc/passwd", "/_next/static/../../../etc/passwd",
])
def test_nothing_outside_the_build_can_be_fetched(built, url):
    assert frontend.resolve(url) is None
    assert frontend.classify(url) is Access.DENIED


def test_a_symlink_pointing_out_of_the_build_is_refused(built, tmp_path):
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("no", encoding="utf-8")
    make_symlink(built / "_next" / "escape.js", secret)
    assert frontend.resolve("/_next/escape.js") is None


@pytest.mark.parametrize("url", ["/api/settings", "/api/files/raw/x.html", "/v1/chat/completions",
                                 "/health", "/gate", "/gate/token", "/docs", "/openapi.json"])
def test_no_path_this_api_serves_is_ever_public_static(built, url):
    """The pre-filter answers these without touching disk, so an API route cannot become a file."""
    assert frontend.classify(url) is Access.DENIED


def test_a_missing_frontend_denies_everything(unbuilt):
    """On a clone that never ran the build the whole feature is inert, and that must stay true."""
    for url in ("/", "/app", "/login", "/_next/static/chunks/main.js", "/api/settings"):
        assert frontend.classify(url) is Access.DENIED
        assert frontend.resolve(url) is None


def test_the_root_is_public_but_has_no_file(built):
    """`/` is answered by a redirect, so it is public with nothing to serve — the export's own
    index.html is the error page that redirect replaces."""
    assert frontend.classify("/") is Access.PUBLIC
    assert frontend.resolve("/") is None
