"""Serving a Live2D model to the browser, and the credential that does it.

pixi loads the `.model3.json` and then fetches its textures, motions and expressions ITSELF, relative
to that URL and with no header it can set — the same problem the file viewer has, so the same answer:
a `?token=` on the first hit is swapped for a cookie and redirected away. The cookie is NOT the file
viewer's. One password derives two values with different messages, so neither cookie can be replayed
where the other is accepted: a credential minted to draw a face must not also read the user's files,
and a credential minted to open a file must not enumerate anything else.
"""
from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

import kotoba.server as main

PW = "pw-for-tests"
MODELS_COOKIE = hmac.new(PW.encode(), b"kotoba-models-viewer", hashlib.sha256).hexdigest()
FILES_COOKIE = hmac.new(PW.encode(), b"kotoba-files-viewer", hashlib.sha256).hexdigest()


@pytest.fixture
def models(tmp_path, monkeypatch):
    root = tmp_path / "models"
    runtime = root / "mao_pro" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "mao_pro.model3.json").write_text('{"Version": 3}', encoding="utf-8")
    (runtime / "texture_00.png").write_bytes(b"\x89PNG\r\n")
    (runtime / "mao_pro.moc3").write_bytes(b"MOC3")
    (root / "mao_pro" / "evil.html").write_text("<script>1</script>", encoding="utf-8")
    (tmp_path / "outside.png").write_bytes(b"\x89PNG")
    monkeypatch.setenv("KOTOBA_MODELS_DIR", str(root))
    files = tmp_path / "files"
    files.mkdir()
    (files / "secret-notes.txt").write_text("private user file", encoding="utf-8")
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(files))
    return root


@pytest.fixture
def gated(models, monkeypatch):
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", PW)
    main._FAILED_AUTH.clear()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def ungated(models):
    main._FAILED_AUTH.clear()
    with TestClient(main.app) as c:
        yield c


ENTRY = "/api/models/raw/mao_pro/runtime/mao_pro.model3.json"
TEXTURE = "/api/models/raw/mao_pro/runtime/texture_00.png"


def test_with_no_password_the_model_is_simply_served(ungated):
    r = ungated.get(ENTRY)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert ungated.get(TEXTURE).status_code == 200


def test_a_gated_install_serves_nothing_without_a_credential(gated):
    gated.cookies.clear()
    assert gated.get(ENTRY).status_code == 401
    assert gated.get(TEXTURE).status_code == 401


def test_the_bearer_the_app_already_holds_opens_it(gated):
    gated.cookies.clear()
    assert gated.get(ENTRY, headers={"Authorization": f"Bearer {PW}"}).status_code == 200


def test_the_first_hit_swaps_its_token_for_a_scoped_cookie(gated):
    gated.cookies.clear()
    r = gated.get(ENTRY, params={"token": PW}, follow_redirects=False)
    assert r.status_code == 302
    cookie = r.headers.get("set-cookie", "")
    assert "km=" in cookie
    assert "Path=/api/models/raw" in cookie
    assert "HttpOnly" in cookie
    # Relative, and the token is gone: an absolute Location bounces off the Next proxy and loses the
    # cookie, and a token left in the URL lands in history.
    assert r.headers["location"] == ENTRY


def test_the_cookie_then_carries_the_relative_asset_fetches(gated):
    """The whole reason a cookie exists. pixi asks for `texture_00.png` next, with no header on it."""
    gated.cookies.clear()
    gated.cookies.set("km", MODELS_COOKIE)
    assert gated.get(TEXTURE).status_code == 200
    assert gated.get("/api/models/raw/mao_pro/runtime/mao_pro.moc3").status_code == 200


def test_the_model_cookie_reaches_nothing_but_models(gated):
    gated.cookies.clear()
    gated.cookies.set("km", MODELS_COOKIE)
    assert gated.get("/api/files/raw/secret-notes.txt").status_code == 401
    assert gated.get("/api/files").status_code == 401
    assert gated.get("/api/settings").status_code == 401
    assert gated.get("/api/avatar").status_code == 401


def test_the_files_cookie_does_not_reach_models_either(gated):
    """Neither credential may grow: they are separate HMACs of one password for exactly this reason."""
    gated.cookies.clear()
    gated.cookies.set("kf", FILES_COOKIE)
    assert gated.get(ENTRY).status_code == 401


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_no_unsafe_method_is_ever_cookie_authenticated(gated, method):
    gated.cookies.clear()
    gated.cookies.set("km", MODELS_COOKIE)
    assert gated.request(method, ENTRY).status_code == 401


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_route_answers_no_verb_but_a_read(ungated, method):
    assert ungated.request(method, ENTRY).status_code == 405


def test_a_neighbouring_route_cannot_inherit_the_model_cookie():
    from starlette.datastructures import Headers
    from starlette.requests import Request

    scope = {
        "type": "http", "method": "GET", "path": "/api/models-export/all",
        "headers": Headers({"cookie": "km=" + MODELS_COOKIE}).raw, "query_string": b"",
    }
    req = Request(scope)
    assert main._models_cookie_ok(req, "/api/models-export/all") is False
    assert main._models_cookie_ok(req, "/whatever/api/models/raw/x") is False


@pytest.mark.parametrize("escape", [
    "/api/models/raw/../outside.png",
    "/api/models/raw/mao_pro/../../outside.png",
    "/api/models/raw/mao_pro/runtime/../../../outside.png",
])
def test_no_request_reads_outside_the_models_directory(ungated, escape):
    assert ungated.get(escape, follow_redirects=False).status_code in (307, 404, 422)
    assert ungated.get(escape).status_code in (404, 422)


def test_a_document_dropped_in_the_folder_is_not_served(ungated):
    """It would run on the app's own origin, next to the approval card."""
    assert ungated.get("/api/models/raw/mao_pro/evil.html").status_code == 404


def test_a_model_file_is_stamped_nosniff(ungated):
    r = ungated.get(TEXTURE)
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers["content-type"].startswith("image/png")
