"""A viewer cookie could delete the library, and the library is the user's working directory.

The cookie exists for one job: a page opened from the panel arrives with a token, we swap it for a
cookie and redirect, and the page's relative assets then authenticate with the cookie. The check
behind it was a path substring with no method check, and a delete route matches that same substring.
So a cookie minted to LOOK at a file also listed the library, read any file in it, and unlinked any
file in it, with no bearer and no token anywhere. In the default setup those files are not copies of
anything.

The cookie is now scoped to what it always claimed: GET/HEAD under the raw-file path."""
from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

import kotoba.server as main

PW = "pw-for-tests"


@pytest.fixture
def gated(tmp_path, monkeypatch):
    d = tmp_path / "files"
    d.mkdir()
    (d / "page.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (d / "style.css").write_text("body{}", encoding="utf-8")
    (d / "secret-notes.txt").write_text("private user file", encoding="utf-8")
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(d))
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", PW)
    main._FAILED_AUTH.clear()
    cookie = hmac.new(PW.encode(), b"kotoba-files-viewer", hashlib.sha256).hexdigest()
    with TestClient(main.app) as c:
        c.cookies.set("kf", cookie)
        yield c, d


def test_the_cookie_still_opens_the_page_and_its_assets(gated):
    c, _d = gated
    assert c.get("/api/files/raw/page.html").status_code == 200
    assert c.get("/api/files/raw/style.css").status_code == 200


def test_the_cookie_cannot_delete_a_file(gated):
    c, d = gated
    r = c.request("DELETE", "/api/files/secret-notes.txt")
    assert r.status_code == 401, r.text
    assert (d / "secret-notes.txt").exists(), "the file the viewer cookie was never meant to reach"


def test_the_cookie_cannot_list_or_read_outside_the_viewer(gated):
    c, _d = gated
    assert c.get("/api/files").status_code == 401
    assert c.get("/api/files/open", params={"path": "secret-notes.txt"}).status_code == 401


def test_the_cookie_cannot_write_the_index(gated):
    c, _d = gated
    assert c.post("/api/files/seen", json={"path": "page.html"}).status_code == 401


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_no_unsafe_method_is_ever_cookie_authenticated(gated, method):
    c, _d = gated
    assert c.request(method, "/api/files/raw/page.html").status_code == 401


def test_a_new_files_subroute_cannot_inherit_the_cookie():
    """The check was a substring, so it depended on every /api/files* route being one the viewer
    may use. A prefix test does not."""
    from starlette.datastructures import Headers
    from starlette.requests import Request

    scope = {
        "type": "http", "method": "GET", "path": "/api/files-export/all",
        "headers": Headers({"cookie": "kf=" + hmac.new(
            PW.encode(), b"kotoba-files-viewer", hashlib.sha256).hexdigest()}).raw,
        "query_string": b"",
    }
    req = Request(scope)
    assert main._files_cookie_ok(req, "/whatever/api/files/raw/x") is False
    assert main._files_cookie_ok(req, "/api/files-export/all") is False


def test_the_cookie_is_issued_scoped_to_the_viewer(tmp_path, monkeypatch):
    d = tmp_path / "files"
    d.mkdir()
    (d / "page.html").write_text("<h1>hi</h1>", encoding="utf-8")
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(d))
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", PW)
    main._FAILED_AUTH.clear()
    with TestClient(main.app) as c:
        r = c.get("/api/files/raw/page.html", params={"token": PW}, follow_redirects=False)
        assert r.status_code == 302
        assert "Path=/api/files/raw" in r.headers.get("set-cookie", "")
