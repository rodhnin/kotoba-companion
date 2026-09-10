"""/api/files/raw token→cookie redirect must use a RELATIVE Location. Behind the same-origin Next
proxy (local default) an absolute Location would send the browser to the backend's own origin, where
the freshly-set `kf` cookie is never sent back → 401 on every asset (the broken-file-viewer bug)."""
from __future__ import annotations

import pytest

from kotoba.core import file_library


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    file_library.clear()
    yield tmp_path / "files"
    file_library.clear()


def test_raw_redirect_location_is_relative(lib, monkeypatch):
    from fastapi.testclient import TestClient

    import kotoba.server as main

    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "pw")
    file_library.save_text("reports/page.html", "<h1>hi</h1>")
    with TestClient(main.app) as c:
        r = c.get("/api/files/raw/reports/page.html", params={"token": "pw"}, follow_redirects=False)
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc == "/api/files/raw/reports/page.html"   # no scheme/host, token stripped
        assert "kf=" in r.headers.get("set-cookie", "")
        assert "Path=/api/files" in r.headers.get("set-cookie", "")

        followed = c.get(loc)                               # cookie-authed, same origin
        assert followed.status_code == 200 and "hi" in followed.text


def test_raw_redirect_keeps_other_query_params(lib, monkeypatch):
    from fastapi.testclient import TestClient

    import kotoba.server as main

    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "pw")
    file_library.save_text("page.html", "<h1>q</h1>")
    with TestClient(main.app) as c:
        r = c.get("/api/files/raw/page.html", params={"token": "pw", "v": "2"}, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/api/files/raw/page.html?v=2"
