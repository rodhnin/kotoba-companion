"""/api/files/raw and /api/files/open HTML responses must carry
Content-Security-Policy: sandbox (without allow-same-origin) so a script in an agent-written
page cannot access window.parent.sessionStorage (where the gate token lives).

Safe path: non-HTML files (CSS, JS, images) must NOT get a CSP that would break their use as
subresources loaded by the sandboxed HTML page.
"""
from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

import kotoba.server as main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def html_file(tmp_path, monkeypatch):
    """Write a minimal HTML file to a temp file library and point the server at it."""
    lib = tmp_path / "files"
    lib.mkdir()
    f = lib / "test.html"
    f.write_text("<h1>hi</h1>", encoding="utf-8")
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    return "test.html"


def test_raw_html_csp_contains_sandbox(client, html_file):
    r = client.get(f"/api/files/raw/{html_file}")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "sandbox" in csp


def test_raw_html_csp_no_allow_same_origin(client, html_file):
    r = client.get(f"/api/files/raw/{html_file}")
    csp = r.headers.get("content-security-policy", "")
    assert "allow-same-origin" not in csp


def test_raw_html_csp_allows_scripts(client, html_file):
    r = client.get(f"/api/files/raw/{html_file}")
    csp = r.headers.get("content-security-policy", "")
    assert "allow-scripts" in csp


def test_open_html_csp_contains_sandbox(client, tmp_path, monkeypatch):
    lib = tmp_path / "files"
    lib.mkdir()
    (lib / "page.html").write_text("<p>ok</p>", encoding="utf-8")
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    r = client.get("/api/files/open", params={"path": "page.html"})
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "sandbox" in csp
    assert "allow-same-origin" not in csp


def test_non_html_raw_file_has_no_csp(client, tmp_path, monkeypatch):
    """Non-HTML files (CSS, images, JS) must NOT get a sandbox CSP — they're subresources."""
    lib = tmp_path / "files"
    lib.mkdir()
    (lib / "style.css").write_text("body {}", encoding="utf-8")
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    r = client.get("/api/files/raw/style.css")
    assert r.status_code == 200
    assert "sandbox" not in r.headers.get("content-security-policy", "")
