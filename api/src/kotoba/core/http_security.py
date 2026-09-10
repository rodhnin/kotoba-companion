"""One rule, in one place, for what a browser may do with the bytes this API hands it.

Both file routes used to attach the sandbox CSP themselves on `text/html`, so an `.svg` or `.xhtml` —
scriptable the moment you navigate to them, which the Files panel's "New tab" does — went out bare:
no sandbox, no `connect-src 'none'`, and a script in a model-written file could post anywhere.

DENY BY DEFAULT: a response is a DOCUMENT unless its media type is on the inert list, and every
document leaves with the sandbox CSP. Omitting `allow-same-origin` puts it on an opaque origin — no
cookie, no same-origin read — and `connect-src 'none'` closes fetch/XHR/beacon; `nosniff` goes on
everything. Residue: such a document can still navigate ITSELF away, carrying nothing with it."""
from __future__ import annotations

SANDBOX_CSP = (
    "sandbox allow-scripts; "
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; connect-src 'none'; form-action 'none'; base-uri 'none'"
)

_INERT_PREFIXES = ("audio/", "font/", "video/")
_INERT_TYPES = frozenset({
    "application/javascript", "application/json", "application/pdf", "application/wasm",
    "application/x-javascript", "text/css", "text/event-stream", "text/javascript",
})
_CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect"})

_CSP = b"content-security-policy"
_NOSNIFF = b"x-content-type-options"
_CONTENT_TYPE = b"content-type"


def is_document(media_type: str) -> bool:
    """Could a browser treat a response of this media type as a document that runs script?"""
    media = (media_type or "").split(";", 1)[0].strip().lower()
    if media.startswith("image/"):
        return media.startswith("image/svg")   # the one image type that is also a document
    if media.startswith(_INERT_PREFIXES) or media in _INERT_TYPES:
        return False
    return True


def request_path(scope) -> str:
    """The path as ROUTING sees it: `uvicorn --root-path /prefix` prepends the proxy prefix to
    scope["path"] while Starlette strips it before matching, so raw scope["path"] names a different
    route than the one that will actually run."""
    path = scope.get("path", "") or ""
    root = (scope.get("root_path") or "").rstrip("/")
    if root and path.startswith(root):
        path = path[len(root):] or "/"
    return path


def harden(headers: list[tuple[bytes, bytes]], *, csp: bool = True) -> list[tuple[bytes, bytes]]:
    """Add nosniff, and the sandbox CSP when the response is a document. Never overwrites either header."""
    present = {name.lower() for name, _ in headers}
    media = next((v.decode("latin-1", "replace") for k, v in headers if k.lower() == _CONTENT_TYPE), "")
    if _NOSNIFF not in present:
        headers.append((_NOSNIFF, b"nosniff"))
    if csp and _CSP not in present and is_document(media):
        headers.append((_CSP, SANDBOX_CSP.encode("latin-1")))
    return headers


class DocumentSecurityMiddleware:
    """Stamp the document headers on every HTTP response, whatever route produced it.

    Pure ASGI and header-only: it rewrites the `http.response.start` message and forwards every body
    message untouched, so the SSE streams (/api/events, /v1/chat/completions) are unaffected — a
    BaseHTTPMiddleware would buffer them."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        allow_csp = request_path(scope) not in _CSP_EXEMPT_PATHS

        async def _send(message):
            if message.get("type") == "http.response.start":
                message["headers"] = harden(list(message.get("headers") or []), csp=allow_csp)
            await send(message)

        await self.app(scope, receive, _send)
