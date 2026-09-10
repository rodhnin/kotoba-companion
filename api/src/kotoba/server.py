"""Kotoba FastAPI backend: the custom LLM endpoint for ElevenLabs and the SSE emotion channel. Run with
`uvicorn kotoba.server:app --reload --port 8000`, or `kotoba serve`.

Three credentials meet here and must never be conflated. `/api/*` is gated by the web password (Bearer
header, `?token=`, or a derived read-only cookie); `/v1/chat/completions` carries its own bearer, handed
to ElevenLabs and never a key to a surface that runs host commands; `_is_public_path` holds the whole
list that needs neither. The two derived cookies — `kf` for the file viewer, `km` for a model's files —
are HMACs of the password over different messages, so one minted to draw a face cannot read files.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# BEFORE the core.* imports (hence the noqa E402s): several read os.getenv at MODULE level.
load_dotenv()

from typing import Annotated  # noqa: E402

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request, WebSocket  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

from kotoba import __version__
from kotoba.core import engine as core_engine  # noqa: E402
from kotoba.core import frontend  # noqa: E402
from kotoba.core import gate  # noqa: E402
from kotoba.core import stream as sse  # noqa: E402
from kotoba.core import transport  # noqa: E402
from kotoba.core import turns
from kotoba.core.context import (  # noqa: E402
    NoUserMessage,
    is_trigger_sentinel,
    latest_user_message,
    load_context,
    text_of,
)
from kotoba.core.events import event_queues, register, unregister
from kotoba.core.http_security import DocumentSecurityMiddleware, request_path
from kotoba.core.loop import agentic_loop
from kotoba.core.memory import extract_and_save_memory
from kotoba.core.session_state import consume_text_turn, is_muted, mark_text_turn, set_muted
from kotoba.models.schemas import ChatRequest

logging.basicConfig()   # a level without a handler is not delivery
logging.getLogger("kotoba").setLevel(os.getenv("KOTOBA_LOG_LEVEL", "INFO").upper())


def _quieten_the_proactor(loop) -> None:
    """Windows' proactor loop shuts a socket down AFTER the peer has already dropped it, and asyncio
    reports the reset as an unhandled callback error — six lines of interpreter internals for a browser
    that simply went away. Nothing is lost and no `except` at any call site of ours can reach it.

    Only that one shape is dropped; everything else goes to whoever was handling it before."""
    previous = loop.get_exception_handler()

    def handle(the_loop, context) -> None:
        if isinstance(context.get("exception"), ConnectionResetError) \
                and "_call_connection_lost" in str(context.get("message", "")):
            return
        if previous is None:
            the_loop.default_exception_handler(context)
        else:
            previous(the_loop, context)

    loop.set_exception_handler(handle)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    _quieten_the_proactor(asyncio.get_running_loop())
    engine = await core_engine.start(tickers=True)
    app.state.engine = engine
    app.state.db = engine.db
    app.state.soul_patterns = engine.soul_patterns
    app.state.mcp = engine.mcp
    yield
    await core_engine.stop(engine)


# Three things mint a session id and nothing else ever should: the browser's `crypto.randomUUID()`,
# `uuid4().hex` here and in the terminal, and `discord:` plus a digest. It is echoed back to us and then
# written into the log, so a newline inside one forges a line in the operator's own record of what she
# did. Refused at the door rather than scrubbed at each of the twenty-five places that write it.
SessionId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]


app = FastAPI(title="Kotoba", version=__version__, lifespan=lifespan)


FILES_COOKIE = "kf"
FILES_RAW_PREFIX = "/api/files/raw"
MODELS_COOKIE = "km"
MODELS_RAW_PREFIX = "/api/models/raw"

# Strong refs for fire-and-forget tasks: a bare create_task can be garbage-collected while running.
_bg_tasks: set[asyncio.Task] = set()


def _request_token(request: Request) -> str | None:
    """The caller's token from `Authorization: Bearer <t>` or the `?token=` query param. The query form is
    for browser surfaces that can't set headers (the SSE EventSource, /report.pdf)."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("token")


def _files_cookie_token() -> str:
    """What the `kf` cookie carries: a value DERIVED from the web password, never the password itself.

    The cookie used to be the password verbatim, with SameSite=None so the cross-site preview iframe
    could send it — a 12-hour bearer for the entire control surface, handed to the browser on every file
    open. Derived, and accepted only where _files_cookie_ok says, it unlocks the viewer and nothing else."""
    return _derived_cookie(b"kotoba-files-viewer")


def _files_cookie_ok(request: Request, path: str) -> bool:
    """What the viewer cookie authenticates: READING a page and its relative assets under
    /api/files/raw, and nothing else.

    It used to be a substring test over "/api/files" with no method check, and `DELETE
    /api/files/{path:path}` is a route that substring matches — so the cookie minted to open a file
    could erase the library, which in the default setup is the user's own working directory. Its scope
    is now the prefix its docstring always claimed, and safe methods only: a viewer needs no verb that
    changes anything."""
    c = request.cookies.get(FILES_COOKIE)
    if not c or request.method not in ("GET", "HEAD"):
        return False
    if path != FILES_RAW_PREFIX and not path.startswith(FILES_RAW_PREFIX + "/"):
        return False
    return _secret_eq(c, _files_cookie_token())


def _derived_cookie(message: bytes) -> str:
    """A value derived from the web password for one read-only surface, never the password itself.
    Being an HMAC it needs no server state and survives a restart; the MESSAGE is what keeps two such
    cookies apart, so neither can be replayed where the other is accepted."""
    import hmac

    return hmac.new(_web_password().encode("utf-8"), message, hashlib.sha256).hexdigest()


def _models_cookie_token() -> str:
    return _derived_cookie(b"kotoba-models-viewer")


def _models_cookie_ok(request: Request, path: str) -> bool:
    """What the model cookie authenticates: READING the files a Live2D model is made of, and nothing
    else. pixi fetches a model's textures, motions and expressions itself, relative to the entry file
    and with no header it can set — so the credential has to travel by cookie. It is deliberately not
    the file viewer's cookie: a credential minted to draw a face must not also read the user's files."""
    c = request.cookies.get(MODELS_COOKIE)
    if not c or request.method not in ("GET", "HEAD"):
        return False
    if path != MODELS_RAW_PREFIX and not path.startswith(MODELS_RAW_PREFIX + "/"):
        return False
    return _secret_eq(c, _models_cookie_token())


def _mint_viewer_cookie(request: Request, name: str, value: str, scope_path: str):
    """Swap the `?token=` a browser CAN attach on a first hit for a cookie scoped to that one prefix,
    then redirect to the same URL without it. None when there is no valid token to swap.

    The Location is RELATIVE: an absolute one bounces the browser off the Next proxy and loses the
    cookie."""
    from fastapi.responses import RedirectResponse

    qtoken = request.query_params.get("token")
    if not qtoken or not _api_token_ok(qtoken):
        return None
    clean = request.url.remove_query_params("token")
    resp = RedirectResponse(url=clean.path + (f"?{clean.query}" if clean.query else ""), status_code=302)
    is_https = request.url.scheme == "https"
    resp.set_cookie(name, value, max_age=43200, httponly=True,
                    samesite="none" if is_https else "lax", secure=is_https, path=scope_path)
    return resp


def _web_password() -> str:
    """One shared secret, two names: KOTOBA_WEB_PASSWORD (canonical, backend) and KOTOBA_GATE_PASSWORD
    (the frontend gate's var — accepted here so ONE value set anywhere gates both sides)."""
    return os.getenv("KOTOBA_WEB_PASSWORD") or os.getenv("KOTOBA_GATE_PASSWORD") or ""


def _secret_eq(a: str, b: str) -> bool:
    """Constant-time compare on BYTES: hmac.compare_digest raises TypeError on non-ASCII str, so an
    accented password (or a request sending one) would 500 the whole API instead of returning 401."""
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _api_token_ok(token: str | None) -> bool:
    """Valid only against the browser password. KOTOBA_API_KEY is deliberately NOT accepted: it is the /v1
    bearer (verify_auth), handed to ElevenLabs, and must never double as a key to the whole control surface.
    Constant-time compare (hmac.compare_digest) so the password can't be timing-probed."""
    if not token:
        return False
    web = _web_password()
    return bool(web) and _secret_eq(token, web)


_OAUTH_CALLBACK = "/api/mcp/oauth/callback"


def _is_public_path(path: str) -> bool:
    """Routes reachable without the web password. Everything else is gated — DENY BY DEFAULT.

    No rule matches by suffix. An `endswith` for the OAuth callback also met the catch-all file
    routes, making anything stored at `api/mcp/oauth/callback` readable and deletable with no credential
    — and the workdir IS the library by default, so a prompt-injected "save this here" mints an
    exfiltration channel.

    An allowlist of what to PROTECT failed open twice: FastAPI's `/docs` and `/openapi.json` handed an
    unauthenticated caller the route map of an API that runs host commands, and behind a proxy keeping a
    path prefix uvicorn prepends it while Starlette strips it, so every route ran ungated."""
    if path == "/health":
        return True
    if path == "/v1" or path.startswith("/v1/"):
        return True
    # The login and the token hand-off: a browser reaches them with no credential BECAUSE they are
    # where a credential comes from. Neither is open — `/gate` costs the password and answers into a
    # lockout, `/gate/token` costs a signed session — and both are matched whole, never by prefix.
    if path in ("/gate", "/gate/token"):
        return True
    if path == _OAUTH_CALLBACK:
        return True
    # A browser asks for this before it has anything, on the login page itself, and unasked. Gated, it
    # answered 401 AND spent one of the twenty auth failures a minute: twenty reloads and the RIGHT
    # password came back 429 — the tab locking its own operator out. It is the same bytes as the
    # already-public /icon.svg, so there is nothing here a credential was protecting.
    if path == "/favicon.ico":
        return True
    # Derived from the build on disk, never from a prefix: a rule kept in step with a route table by
    # hand is the shape that failed open above, twice.
    return frontend.is_public(path)


class _RedactQueryToken(logging.Filter):
    """Keep `?token=` out of the access log. The SSE stream and the file viewer can only authenticate
    through the query string, and uvicorn logs the query verbatim — so the web password was written in
    clear text on every page load, into a file that gets pasted into bug reports and shipped to log
    aggregators. The path still tells you what was requested. Installed on the error loggers too, not
    just access: that is where the WebSocket handshake line is logged, and the voice socket can only
    authenticate through ?token=."""

    _RE = __import__("re").compile(r"(token=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args and isinstance(record.args, tuple):
            record.args = tuple(
                self._RE.sub(r"\1<redacted>", a) if isinstance(a, str) else a for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = self._RE.sub(r"\1<redacted>", record.msg)
        return True


for _name in ("uvicorn.access", "uvicorn.error", "hypercorn.access", "hypercorn.error"):
    logging.getLogger(_name).addFilter(_RedactQueryToken())


_FAILED_AUTH: dict[str, tuple[int, float]] = {}
_MAX_FAILS = 20
_LOCKOUT_S = 60.0


def _auth_blocked(client: str) -> bool:
    import time

    fails, until = _FAILED_AUTH.get(client, (0, 0.0))
    return fails >= _MAX_FAILS and time.monotonic() < until


def _note_auth_failure(client: str) -> None:
    import time

    now = time.monotonic()
    fails, until = _FAILED_AUTH.get(client, (0, 0.0))
    if now >= until:
        fails = 0
    _FAILED_AUTH[client] = (fails + 1, now + _LOCKOUT_S)
    if len(_FAILED_AUTH) > 512:
        _FAILED_AUTH.clear()


def _note_auth_success(client: str) -> None:
    _FAILED_AUTH.pop(client, None)


#: Stamped by the static layer ALONE, never by a middleware. A middleware would reach the file
#: routes too, and `/api/files/raw/*` is framed by the viewer: X-Frame-Options there blanks it, with
#: nothing on screen or in the console to say why. Those routes carry the sandbox CSP instead.
_PAGE_HEADERS = {
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
}


def _framed(response):
    for name, value in _PAGE_HEADERS.items():
        response.headers[name] = value
    return response


def _gate_page_ok(request: Request, path: str) -> bool:
    """What a signed session authenticates: reading the app's own pages, and nothing else. Scoped the
    way the two viewer cookies are, because it is sent automatically on same-site requests — accepting
    it on /api/* would swap a header-carried token for an ambient one on a surface that runs commands."""
    if request.method not in ("GET", "HEAD"):
        return False
    if frontend.classify(path) is not frontend.Access.GATED:
        return False
    return gate.verify_session(request.cookies.get(gate.COOKIE))


def _login_redirect(scope, path: str):
    """Send an anonymous browser to the gate rather than answering 401 at it, carrying the proxy
    prefix: routing strips it, but the address the browser follows next must not lose it."""
    from urllib.parse import quote

    from fastapi.responses import RedirectResponse

    prefix = (scope.get("root_path") or "").rstrip("/")
    query = (scope.get("query_string") or b"").decode("latin-1")
    target = prefix + path + (f"?{query}" if query else "")
    resp = RedirectResponse(f"{prefix}/login?next={quote(target, safe='')}", status_code=302)
    resp.headers["Cache-Control"] = "no-store"
    return _framed(resp)


class APIAuthMiddleware:
    """Pure ASGI gate for /api/* — rejects unauthenticated requests with 401 BEFORE the route runs, and
    otherwise passes the request straight through (no response buffering, so the SSE stream on /api/events
    is untouched — a BaseHTTPMiddleware would break it). Enforced only once KOTOBA_WEB_PASSWORD is set; the
    browser sends the gate password as its token (Bearer header or ?token=). Public: CORS preflight
    (OPTIONS) and whatever `_is_public_path` allows. /v1 keeps its own bearer (verify_auth). A valid credential is
    checked BEFORE the failed-auth lockout: a loopback install shares 127.0.0.1, so refusing a VALID
    token during a cooldown would lock the real frontend out of its own backend."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from starlette.requests import Request as _Req

            request = _Req(scope, receive)
            path = request_path(scope)
            if request.method != "OPTIONS" and not _is_public_path(path) and _web_password():
                client = (scope.get("client") or ("?",))[0]
                if (_api_token_ok(_request_token(request)) or _files_cookie_ok(request, path)
                        or _models_cookie_ok(request, path) or _gate_page_ok(request, path)):
                    _note_auth_success(client)
                elif (request.method in ("GET", "HEAD")
                        and frontend.classify(path) is frontend.Access.GATED):
                    # A page, asked for by a browser holding nothing: bounce it to the gate, and do
                    # NOT count it. The lockout is shared with the login, so counting reloads of /app
                    # would lock somebody out of the very screen they are being sent to.
                    await _login_redirect(scope, path)(scope, receive, send)
                    return
                else:
                    blocked = _auth_blocked(client)
                    _note_auth_failure(client)
                    await JSONResponse(
                        {"detail": "too many attempts" if blocked else "unauthorized"},
                        status_code=429 if blocked else 401,
                    )(scope, receive, send)
                    return
        await self.app(scope, receive, send)


app.add_middleware(APIAuthMiddleware)


LOCAL_DEV_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


def cors_origins() -> list[str]:
    """Never "*": this API can execute commands on the host, so a random website must not be able to
    drive it from a victim's browser. CORS_ORIGINS ADDS deployed origins to the localhost defaults
    rather than replacing them — the local frontend works with zero config, and a pinned production
    origin can never silently break it. `_ws_origin_allowed` reads the same list, because a WebSocket
    handshake is exempt from CORS and would otherwise have no allowlist at all."""
    extra = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    return list(dict.fromkeys([*LOCAL_DEV_ORIGINS, *extra]))


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last = outermost, so it also stamps the gate's own 401s. No route decides this.
app.add_middleware(DocumentSecurityMiddleware)


async def verify_auth(authorization: str | None = Header(default=None)) -> None:
    """Bearer check for /v1/chat/completions. Accepts KOTOBA_API_KEY (the ElevenLabs bearer) OR
    KOTOBA_WEB_PASSWORD (so the docs-recommended setup — leave API key unset, set the web password —
    still protects this endpoint). When NEITHER is configured the endpoint is open (local-install default)."""
    api_key = os.getenv("KOTOBA_API_KEY") or ""
    web_pw = _web_password()
    if not api_key and not web_pw:
        return
    token = ""
    raw = authorization or ""
    if raw.lower().startswith("bearer "):
        token = raw[7:].strip()
    if api_key and _secret_eq(token, api_key):
        return
    if web_pw and _secret_eq(token, web_pw):
        return
    raise HTTPException(status_code=401, detail="invalid or missing API key")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Her own icon, for the pages that declare none — the raw file previews, chiefly.

    A browser asks for this whatever the page says, and a 404 made it drop the connection mid-shutdown,
    which on Windows surfaced as six lines of asyncio internals in the operator's console. Answering is
    both the fix and the nicer tab. No build, no icon: 204, which also ends the asking."""
    from fastapi.responses import FileResponse, Response

    icon = frontend.resolve("/icon.svg")
    if icon is None:
        return Response(status_code=204)
    return FileResponse(icon, media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400"})


def _gate_cookie_args(request: Request) -> dict:
    forwarded = request.headers.get("x-forwarded-proto") or request.headers.get("x-forwarded-scheme")
    return {"httponly": True, "samesite": "lax", "path": "/",
            "secure": gate.request_is_secure(request.url.scheme, forwarded)}


@app.post("/gate")
async def open_gate(request: Request):
    """Trade the password for a signed session cookie. Public by necessity — it is where a browser
    with no credential gets one — so it carries the guessing defences itself: the shared lockout the
    middleware applies elsewhere, plus a fixed delay that makes a wrong answer cost the same as a
    right one."""
    client = (request.scope.get("client") or ("?",))[0]
    if _auth_blocked(client):
        return JSONResponse({"ok": False, "error": "too many attempts"}, status_code=429)
    try:
        attempt = (await request.json()).get("password")
    except Exception:
        return JSONResponse({"ok": False, "error": "bad request"}, status_code=400)
    if not gate.enabled():
        return JSONResponse({"ok": True, "open": True})
    if not gate.password_matches(attempt if isinstance(attempt, str) else ""):
        await asyncio.sleep(0.6)
        _note_auth_failure(client)
        return JSONResponse({"ok": False, "error": "wrong"}, status_code=401)
    _note_auth_success(client)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(gate.COOKIE, gate.sign_session(), max_age=gate.TTL_SECONDS,
                    **_gate_cookie_args(request))
    return resp


@app.delete("/gate")
async def close_gate(request: Request):
    resp = JSONResponse({"ok": True})
    resp.set_cookie(gate.COOKIE, "", max_age=0, **_gate_cookie_args(request))
    return resp


@app.get("/gate/token")
async def gate_token(request: Request):
    """Hand the API token to a browser that already passed the gate — how a refresh or a second tab
    re-acquires it without the password ever reaching the bundle. The body IS the password, so it is
    never cached anywhere, and an open gate answers with the empty token the open backend expects."""
    if not gate.enabled():
        return JSONResponse({"token": ""})
    if not gate.verify_session(request.cookies.get(gate.COOKIE)):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({"token": gate.password()},
                        headers={"Cache-Control": "no-store, private"})


@app.post("/v1/chat/completions", dependencies=[Depends(verify_auth)])
async def chat_completions(request: ChatRequest, http: Request):
    db = http.app.state.db
    soul_patterns = http.app.state.soul_patterns

    resolved = request.resolve_session_id()
    session_id = resolved or uuid.uuid4().hex
    _log = logging.getLogger("kotoba")
    if _log.isEnabledFor(logging.DEBUG):
        def _part_types(msgs):
            for m in reversed(msgs or []):
                c = m.get("content")
                if isinstance(c, list):
                    return [p.get("type") if isinstance(p, dict) else type(p).__name__ for p in c]
            return None
        _log.debug(
            "[chat] session_id_resolved=%r using=%r extra_keys=%s n_messages=%d roles=%s part_types=%s",
            resolved, session_id, request.extra_keys(), len(request.messages),
            [m.get("role") for m in request.messages][-6:], _part_types(request.messages),
        )
    await db.ensure_session(session_id)

    from kotoba.core import interaction as _interaction
    _interaction.note_turn(session_id)

    typed_turn = consume_text_turn(session_id)
    from kotoba.core import work_state as _ws
    pending_work = _ws.has_pending_announcement(session_id)
    from kotoba.core import pending_reminder as _pr
    pending_rem = _pr.has_pending(session_id)
    if is_muted(session_id) and not typed_turn and not pending_work and not pending_rem:
        async def skip_gen():
            yield sse.text_role()
            yield sse.text_tool_call("skip_turn")
            yield sse.text_final_tool()
            yield sse.text_done()

        return StreamingResponse(
            skip_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        user_msg = latest_user_message(request.messages)
    except NoUserMessage as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user_text = text_of(user_msg.get("content", ""))
    is_trigger = is_trigger_sentinel(user_text)
    if not is_trigger:
        await db.insert_turn(session_id, "user", user_text)

    _mcp = getattr(http.app.state, "mcp", None)
    connected_mcp = (
        [{"name": n, "tools": t} for n, t in getattr(_mcp, "server_tools", {}).items() if t]
        if _mcp is not None else None
    )

    input_items = await load_context(
        request, db, session_id, consume_attachments=typed_turn, connected_mcp=connected_mcp,
        persisted=not is_trigger,
    )

    stream_queue: asyncio.Queue = asyncio.Queue()
    holder: dict = {"text": ""}

    async def produce() -> None:
        try:
            # No channel= here: a typed message still arrives relayed by the EL agent, so EL owns the turn
            # clock (`typed_turn` is a mute override, NOT a channel signal); a sentinel must not re-launch the job it voices.
            _excl = frozenset({"start_work", "delegate"}) if is_trigger else None
            with transport.el_call_turn():
                holder["text"] = await agentic_loop(
                    input_items, session_id, db, stream_queue, soul_patterns,
                    mcp=getattr(http.app.state, "mcp", None), mode="companion",
                    exclude_tools=_excl,
                )
        except asyncio.CancelledError:
            logging.getLogger("kotoba").warning(
                "turn CANCELLED mid-flight for session %s (barge-in / disconnect / EL turn_timeout)", session_id
            )
            raise
        except Exception:
            logging.getLogger("kotoba").exception("agentic_loop failed for session %s", session_id)
            await stream_queue.put(sse.crash_apology())
        finally:
            await stream_queue.put(sse.DONE_SENTINEL)

    async with turns.lock(session_id):
        await turns.supersede(session_id)
        producer = asyncio.create_task(produce())
        turns.register(session_id, producer)

    leak_filter = sse.ToolCallLeakFilter()
    code_filter = sse.CodeFenceFilter()
    url_filter = sse.UrlFilter()
    tag_filter = sse.AudioTagFilter()
    phrase_filter = sse.ForbiddenPhraseFilter()

    async def event_generator():
        try:
            yield sse.text_role()
            any_text = False
            while True:
                try:
                    item = await asyncio.wait_for(stream_queue.get(), timeout=4.0)
                except asyncio.TimeoutError:
                    # EL cuts a turn after ~7s without AUDIO; the buffer word must never count as any_text.
                    yield sse.text_chunk("... ")
                    continue
                if item is sse.DONE_SENTINEL:
                    break
                if item is sse.FLUSH_SENTINEL:
                    held = sse.flush_spoken(leak_filter, code_filter, url_filter, tag_filter,
                                            phrase_filter, final=False)
                    if held:
                        any_text = True
                        yield sse.text_chunk(held)
                    continue
                if item:
                    safe = phrase_filter.feed(tag_filter.feed(url_filter.feed(
                        code_filter.feed(leak_filter.feed(item)))))
                    if safe:
                        any_text = True
                        yield sse.text_chunk(safe)
            tail = sse.flush_spoken(leak_filter, code_filter, url_filter, tag_filter,
                                    phrase_filter, final=True)
            if tail:
                any_text = True
                yield sse.text_chunk(tail)
            if not any_text and holder["text"].strip():
                fallback = sse.filtered_reply_fallback()
                holder["text"] = fallback.strip()
                yield sse.text_chunk(fallback)
                yield sse.text_final()
                yield sse.text_done()
            elif not any_text:
                yield sse.text_tool_call("skip_turn")
                yield sse.text_final_tool()
                yield sse.text_done()
            else:
                yield sse.text_final()
                yield sse.text_done()
        finally:
            if not producer.done():
                producer.cancel()
            try:
                await producer
            except BaseException:
                pass
            turns.clear(session_id, producer)
            if holder["text"]:
                await db.insert_turn(
                    session_id, "assistant",
                    sse.collapse_repeats(sse.strip_tool_call_leaks(holder["text"])),
                )
                from kotoba.core import pending_reminder, work_state
                snap = work_state.get(session_id)
                if snap["status"] in ("done", "failed") and not snap["announced"]:
                    work_state.mark_announced(session_id)
                if pending_rem:
                    pending_reminder.clear(session_id)
            if not is_trigger:
                _mem = asyncio.create_task(extract_and_save_memory(user_text, db))
                _bg_tasks.add(_mem)
                _mem.add_done_callback(_bg_tasks.discard)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/events/{session_id}")
async def events(session_id: SessionId):
    queue = register(session_id)
    from kotoba.core import session_sandbox

    session_sandbox.note_connect(session_id)

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if data.get("type") == "task":
                        yield sse.task_event({k: v for k, v in data.items() if k != "type"})
                    else:
                        yield sse.emotion_event(data["emotion"])
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            removed = event_queues.get(session_id) is queue
            unregister(session_id, queue)
            if removed:
                session_sandbox.note_disconnect(session_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        # no-transform: the Next proxy gzips this, buffering every frame until the stream closes.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def _ws_origin_allowed(origin: str | None, host: str | None = None) -> bool:
    """A WebSocket handshake is EXEMPT from CORS, so the allowlist that protects every HTTP route does
    not protect this one. Without this check any website the user visits could open a socket to the
    loopback backend and drive the agentic loop with host tools — and on the default install, where no
    password is set, it would not even need a credential.

    A browser always sends Origin, so a MISSING one means the caller is not a page: the CLI, a script,
    a test. Those are allowed. The list is still needed because in dev the frontend dials the backend
    on another port — Next does not forward upgrades — but when this server serves the page too, the
    page's origin IS this server, and a browser sets Host from the address it dialled, so a foreign
    page cannot forge the match."""
    if origin is None:
        return True
    if origin in cors_origins():
        return True
    # Same-origin, and ONLY on loopback. Matching Origin against Host alone would accept a rebound
    # domain: the attacker's page keeps its own origin while the name resolves here, the two agree,
    # and a default install has no password to ask for. Serving anywhere else is a deliberate
    # deployment, and CORS_ORIGINS is where that address is named.
    from urllib.parse import urlsplit

    if not host or urlsplit(origin).netloc != host:
        return False
    return urlsplit(f"//{host}").hostname in ("127.0.0.1", "localhost", "::1")


@app.websocket("/api/voice/{session_id}")
async def voice_ws(websocket: WebSocket, session_id: SessionId):
    """Local voice mode: full-duplex browser ⇄ backend WS (mic PCM in, transcripts + TTS audio out) —
    the backend talks OUTBOUND to ElevenLabs, so no tunnel/public URL is needed. The message contract
    and the orchestration live in the voice session, not here; the EL-agent path is untouched."""
    if not _ws_origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=4403, reason="origin not allowed")
        return
    if _web_password():  # the ASGI gate only covers scope "http", and a browser WS cannot set headers
        auth = websocket.headers.get("authorization") or ""
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else websocket.query_params.get("token")
        if not _api_token_ok(token):
            await websocket.accept()
            await websocket.close(code=4401, reason="unauthorized")
            return
    from kotoba.core.voice.session import VoiceSession

    await VoiceSession(
        websocket, session_id,
        db=websocket.app.state.db,
        soul_patterns=websocket.app.state.soul_patterns,
        mcp=getattr(websocket.app.state, "mcp", None),
    ).run()


@app.post("/api/session/{session_id}/mute")
async def set_session_mute(session_id: SessionId, body: dict) -> dict:
    """Frontend reports mic mute state so Kotoba stays silent while muted (no proactive check-ins)."""
    set_muted(session_id, bool(body.get("muted")))
    return {"session_id": session_id, "muted": is_muted(session_id)}


@app.post("/api/session/{session_id}/leave")
async def leave_session(session_id: SessionId) -> dict:
    """The user explicitly HUNG UP — abort everything for this session. Distinct from the call merely
    DROPPING, which keeps work alive on purpose to announce on reconnect.

    Cancels the in-flight voice turn, the background work-runner task, and the pending-announcement flag
    so a finished work does not resurrect and speak on the next call. Idempotent and best-effort.

    Mute goes with it. It is per-CALL state in a process-global set with no TTL, so leaving it behind
    meant mute, leave, call again came back with the server muted and the client unmuted: every spoken
    turn dropped, nothing shown, deaf for the rest of the page."""
    from kotoba.core import deferred_exec, task_list, work_state

    set_muted(session_id, False)
    aborted_work = False
    try:
        task = work_state.pop_task(session_id)
        if task is not None and not task.done():
            task.cancel()
            aborted_work = True
        work_state.clear(session_id)
        task_list.clear(session_id)
        deferred_exec.cancel(session_id)
        deferred_exec.forget_session(session_id)
    except Exception:
        logging.getLogger("kotoba").warning("leave: work cancel failed for %s", session_id, exc_info=True)
    try:
        await turns.supersede(session_id)
    except Exception:
        logging.getLogger("kotoba").warning("leave: turn supersede failed for %s", session_id, exc_info=True)
    logging.getLogger("kotoba").info("session %s left — aborted work=%s", session_id, aborted_work)
    return {"session_id": session_id, "aborted_work": aborted_work}


@app.get("/api/session/{session_id}/tasks")
async def session_tasks(session_id: SessionId) -> dict:
    """Current task list for this session — lets a UI renderer that connects mid-turn rehydrate without
    waiting for the next mutation event. Returns {"list": null} when no list exists."""
    from kotoba.core import task_list

    return {"list": task_list.frame(session_id)}


@app.get("/api/session/{session_id}/work")
async def session_work(session_id: SessionId) -> dict:
    """Background-work status for the frontend — drives announce-on-reconnect. A work that outlives the
    ElevenLabs call (it dies at transport.EL_MAX_DURATION_SECONDS) lands its work_done frame on a dead
    session. On RECONNECT the frontend polls this; if `pending`, it announces once and the flag flips."""
    from kotoba.core import work_state

    s = work_state.get(session_id)
    return {
        "status": s["status"],
        "pending": work_state.has_pending_announcement(session_id),
        "ok": s["status"] == "done",
        "summary": (s.get("summary") or "") if s["status"] == "done" else "",
    }


@app.post("/api/session/{session_id}/text-turn")
async def flag_text_turn(session_id: SessionId) -> dict:
    """Frontend calls this right before sending a TYPED message so the imminent turn is answered even if
    the session is muted (mute silences the mic/silence-turns, not an explicit typed message)."""
    mark_text_turn(session_id)
    return {"ok": True}


@app.post("/api/session/{session_id}/attachment")
async def add_attachment(session_id: SessionId, body: dict) -> dict:
    """Receive an image or PDF the user attached as a base64 data URL and stash it for the NEXT turn.
    ElevenLabs' uploadFile cannot reach a custom LLM, so attachments ride our own context instead.

    Two caps, and both of them ANSWER. The size one always did; the per-message one did not — a fifth
    file was dropped and this endpoint said `{"ok": true}` over it, so the person was told a document had
    attached and then heard an answer written as if it never existed. Nothing was stored, so it is a
    non-2xx carrying `detail` rather than a 200 carrying a falsy field. It is 409 rather than 413 because
    the fix is to send this message, not to send a smaller file."""
    from kotoba.core import attachments

    kind = str(body.get("kind") or "")
    data_url = body.get("data_url")
    name = str(body.get("name") or "file")
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        raise HTTPException(status_code=400, detail="data_url required (base64 data URL)")
    if len(data_url) > 14_000_000:
        raise HTTPException(status_code=413, detail="attachment too large")

    if kind == "image":
        part = {"type": "input_image", "image_url": data_url}
    elif kind == "pdf":
        part = {"type": "input_file", "filename": name, "file_data": data_url}
    else:
        raise HTTPException(status_code=400, detail="kind must be 'image' or 'pdf'")

    if not attachments.add(session_id, part, name=name):
        raise HTTPException(
            status_code=409,
            detail=(f"I can only carry {attachments.MAX_PER_SESSION} files in one message — send this "
                    "one and I'll take the next lot after it."),
        )
    return {"ok": True}


@app.get("/api/session/{session_id}/report")
async def get_session_report(session_id: SessionId):
    """Serve the latest report HTML for the ReportPanel viewer (rendered in a sandboxed iframe).

    The sandbox CSP + nosniff come from DocumentSecurityMiddleware, like every other document here. The
    framing rule is the one thing only this route knows: the panel reads it with fetch and renders it
    through srcDoc, so nothing ever frames this URL and DENY costs nothing."""
    from fastapi.responses import HTMLResponse

    from kotoba.core.reports import get_report

    html = get_report(session_id)
    if not html:
        raise HTTPException(status_code=404, detail="no report")
    return HTMLResponse(content=html, headers={"X-Frame-Options": "DENY"})


@app.get("/api/session/{session_id}/report.pdf")
async def get_session_report_pdf(session_id: SessionId):
    """Render the report to a real PDF server-side (colors guaranteed, no print-dialog checkbox)."""
    from fastapi.responses import Response

    from kotoba.core.pdf import html_to_pdf
    from kotoba.core.reports import get_report

    html = get_report(session_id)
    if not html:
        raise HTTPException(status_code=404, detail="no report")
    pdf = await html_to_pdf(html)
    if pdf is None:
        raise HTTPException(status_code=503, detail="pdf rendering unavailable")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="kotoba-report.pdf"'},
    )


@app.get("/api/files")
async def list_files() -> dict:
    """List Kotoba's persistent file LIBRARY on disk (~/.kotoba/files). Session-independent, so the
    Files panel ALWAYS shows everything she's made — across turns, reconnects, restarts and page reloads.
    Metadata only (name, path, type, size, dates, new/edited tag); the frontend filters/sorts."""
    from kotoba.core import file_library

    return {"files": file_library.list_all()}


@app.get("/api/files/open")
async def open_library_file(path: str):
    """Serve a real file from the on-disk library by jailed `path` (no traversal). Used by the in-app
    viewer's fetch (Bearer auth). For opening a page WITH its relative assets (a HTML's css/js), use
    /api/files/raw/{path} instead — it serves under real paths so relative links resolve."""
    from fastapi.responses import FileResponse

    from kotoba.core import file_library

    resolved = file_library.resolve(path)
    if resolved is None or not resolved.is_file():
        raise HTTPException(status_code=404, detail="file not available")
    return FileResponse(str(resolved), media_type=file_library.media_type_for(resolved))


@app.get("/api/visual-memory/{image_id}")
async def get_keepsake_image(image_id: str):
    """Serve ONE durable keepsake by id, so an image she RECALLS can be shown to the user and not only
    to the vision model — recall_image emits the id on the events channel and the transcript renders an
    <img> at this URL, gated by the same ?token= the Files viewer uses (an <img> can't set a header).

    Addressed by id and never by a path: the caller can't name a file, only a keepsake that exists in her
    index (core.visual_memory.servable_image does the lookup and the jail check). nosniff + a raster-only
    media type keep a stored SVG from being opened as a document on this origin."""
    from fastapi.responses import FileResponse

    from kotoba.core import visual_memory

    found = await asyncio.to_thread(visual_memory.servable_image, image_id)
    if found is None:
        raise HTTPException(status_code=404, detail="image not available")
    path, media = found
    return FileResponse(str(path), media_type=media, headers={"X-Content-Type-Options": "nosniff"})


@app.post("/api/files/seen")
async def mark_file_seen(body: dict) -> dict:
    """Mark a library file as opened so the Files panel drops its new/edited tag (persisted)."""
    from kotoba.core import file_library

    path = str(body.get("path") or "")
    return {"ok": file_library.mark_seen(path)}


@app.delete("/api/files/{path:path}")
async def delete_file(path: str, request: Request) -> dict:
    """Delete a library file from the Files panel (jailed — no traversal). Emits files_changed so any
    open panel re-reads the disk. Idempotent: ok=False if nothing was there."""
    from kotoba.core import file_library
    from kotoba.core.events import emit_task

    removed = await asyncio.to_thread(file_library.delete, path)
    if removed:
        sid = request.query_params.get("session_id") or ""
        try:
            await emit_task(sid, "files_changed")
        except Exception:
            pass
    return {"ok": removed}


@app.get("/api/files/raw/{path:path}")
async def open_raw_file(path: str, request: Request):
    """Serve a library file under a REAL path so a HTML's relative `styles.css`/`script.js` resolve to
    sibling /api/files/raw/ URLs (the in-app viewer / new-tab open). Auth: the first hit carries `?token=`
    (the only thing the browser can attach on a window.open); we set a short-lived `kf` cookie and REDIRECT
    to the same URL WITHOUT the token — so the address bar + history never keep the token, and the page's
    relative asset requests authenticate via the cookie. `path` is jailed (no traversal)."""
    from fastapi.responses import FileResponse

    from kotoba.core import file_library

    minted = _mint_viewer_cookie(request, FILES_COOKIE, _files_cookie_token(), FILES_RAW_PREFIX)
    if minted is not None:
        return minted

    resolved = file_library.resolve(path)
    if resolved is None or not resolved.is_file():
        raise HTTPException(status_code=404, detail="file not available")
    return FileResponse(str(resolved), media_type=file_library.media_type_for(resolved))


@app.get("/api/models/raw/{path:path}")
async def open_model_file(path: str, request: Request):
    """Serve one file of an installed Live2D model under its REAL path, so the entry file's own
    relative texture/motion/expression references resolve to sibling URLs. Read-only: nothing here
    writes, unpacks or downloads, and `core.model_library` refuses both a path that escapes the models
    directory and a file type a browser would run on this origin."""
    from fastapi.responses import FileResponse

    from kotoba.core import model_library

    minted = _mint_viewer_cookie(request, MODELS_COOKIE, _models_cookie_token(), MODELS_RAW_PREFIX)
    if minted is not None:
        return minted

    resolved = model_library.resolve(path)
    if resolved is None:
        raise HTTPException(status_code=404, detail="model file not available")
    return FileResponse(str(resolved), media_type=model_library.media_type_for(resolved))


@app.post("/api/session/{session_id}/input")
async def submit_session_input(session_id: SessionId, body: dict, http: Request) -> dict:
    """User's answer to a need_input card (typed text / key / approval). Resolves the waiting Task.
    A 'key' is persisted backend-only and NEVER echoed back. The name is namespaced server-side with
    the same cred: prefix as request_credential — the client can never address system keys (llm:*/mcp:*).
    `request_id` (echoed from the need_input frame) picks the exact card when a session has more than
    one open; without it the newest wins."""
    from kotoba.core.interaction import resolve
    from kotoba.tools.action.request_credential import _CRED_PREFIX

    kind = body.get("kind")
    if kind == "key":
        raw_name = str(body.get("name") or "").strip()
        if raw_name and body.get("value") is not None:
            if ":" in raw_name:
                raise HTTPException(status_code=400, detail="credential name must not contain ':'")
            await http.app.state.db.save_key(_CRED_PREFIX + raw_name, str(body["value"]))
    rid = body.get("request_id")
    delivered = resolve(session_id, body, str(rid) if rid else None)
    return {"ok": delivered}


@app.get("/api/avatar")
async def get_avatar(http: Request) -> dict:
    """Which Live2D models are installed and which one she wears — asked by the frontend at startup.

    This replaces NEXT_PUBLIC_LIVE2D_MODEL, which `next build` inlines into the bundle: a person on a
    prebuilt image could never change model, and no setup screen could ever choose one. `models_dir`
    is here so the "nothing installed" message can name the folder to put a model in."""
    from kotoba.core import model_library

    soul = await http.app.state.db.fetch_soul_config()
    models = await asyncio.to_thread(model_library.installed)
    configured = str(soul.get("avatar_model") or "")
    chosen = model_library.selection(configured, models)
    # Only the worn one is measured: it is the only one anything states a size for, and the walk is
    # per request.
    if chosen:
        chosen = {**chosen, **await asyncio.to_thread(model_library.measure, chosen["dir"])}
    return {
        "configured": configured,
        "installed": models,
        "selected": chosen,
        "models_dir": str(model_library.models_dir()),
    }


async def _wear_installed_model(db, want: str) -> dict:
    """Store the model she wears, having found it ON DISK. The one validator: an install finishes here
    too, so a freshly unpacked model and a hand-dropped one are accepted by the same check."""
    from kotoba.core import model_library

    want = (want or "").strip().lstrip("/")
    models = await asyncio.to_thread(model_library.installed)
    found = next((m for m in models if m["dir"] == want or f"{m['dir']}/{m['entry']}" == want), None)
    if found is None:
        raise HTTPException(status_code=400, detail="no such model installed")
    chosen = f"{found['dir']}/{found['entry']}"
    await db.update_soul_config(avatar_model=chosen)
    return {"ok": True, "model": chosen}


@app.post("/api/settings/avatar")
async def set_avatar_model(body: dict, http: Request) -> dict:
    """Choose the installed model she wears, by folder name or by `<dir>/<entry>`. Only something
    actually on disk is accepted: the value becomes a URL the browser fetches, and a stored one that
    404s is the phantom control all over again."""
    return await _wear_installed_model(http.app.state.db, str(body.get("model") or ""))


@app.get("/api/models/default")
async def default_model_offer() -> dict:
    """What the download would fetch and whose licence it is — a setup screen has to show both before
    it offers the button, since the reason nothing ships is that we may not redistribute it."""
    from kotoba.core import model_install

    return {**model_install.DEFAULT_MODEL, "installed_dir": model_install.default_installed_dir()}


@app.post("/api/models/install/default")
async def install_default_model(body: dict, http: Request) -> dict:
    """Fetch Live2D's free sample onto THIS machine and install it. The licence is theirs, so the
    acceptance is explicit and this is the only address involved — nothing here takes a URL."""
    from kotoba.core import model_install

    if not bool(body.get("accept_license")):
        raise HTTPException(status_code=400, detail="the model's licence has to be accepted first: "
                            + model_install.DEFAULT_MODEL["licence"])
    return await _installed_and_worn(http, model_install.install_default())


@app.post("/api/models/install/upload")
async def install_uploaded_model(request: Request) -> dict:
    """Install a `.zip` the person supplies. The body IS the archive — a model is tens of megabytes, so
    it streams to disk as it arrives and no part of it is ever held in memory."""
    from kotoba.core import model_install

    return await _installed_and_worn(
        request, model_install.install_stream(request.stream(), request.headers.get("content-length")))


async def _installed_and_worn(request: Request, install) -> dict:
    """Run one install and leave her wearing it: an installed model nobody selected looks like nothing
    happened. Refusals arrive as words a person can act on, never as a stack trace."""
    from kotoba.core.model_install import InstallError

    try:
        got = await install
    except InstallError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e
    worn = await _wear_installed_model(request.app.state.db, got["dir"])
    return {**got, **worn}


@app.get("/api/settings")
async def get_settings(http: Request) -> dict:
    from kotoba.core.settings import build_settings

    return await build_settings(http.app.state.db, getattr(http.app.state, "mcp", None))


@app.post("/api/settings/soul")
async def update_soul(body: dict, http: Request) -> dict:
    """Write her name / language / voice id, then answer with what the row actually HOLDS.

    `db.update_soul_config` silently refuses a value that does not look like a name and coerces a junk
    language to `auto` (the LLM name-extractor emits `42`), so a caller that trusted its own input would
    tell the person she answers to a name she never took. The read-back is the same one `kotoba setup`
    does before it says `I'll answer to it`."""
    fields = {k: str(body[k]) for k in ("name", "language", "voice_id") if k in body and body[k] is not None}
    if fields:
        await http.app.state.db.update_soul_config(**fields)
    soul = await http.app.state.db.fetch_soul_config()
    return {"ok": True, "name": soul.get("name") or "", "language": soul.get("language") or ""}


@app.post("/api/settings/user-name")
async def set_user_name(body: dict, http: Request) -> dict:
    """The name she calls the PERSON, written where the rest of the product already reads it:
    `user_profile['name']`, the destination `kotoba setup` uses and not a new one. An empty value is a
    no-op rather than an erasure — first run offers a skip, and a skip must not delete a name she
    already knows."""
    name = str(body.get("name", "") or "").strip()[:60]
    if name:
        await http.app.state.db.upsert_user_profile("name", name)
    return {"ok": True, "name": name}


@app.post("/api/settings/toolset")
async def toggle_toolset(body: dict) -> dict:
    from kotoba.core.app_settings import set_toolset_enabled

    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="toolset name required")
    set_toolset_enabled(name, bool(body.get("enabled")))
    return {"ok": True}


@app.post("/api/settings/runtime")
async def set_runtime_setting(body: dict) -> dict:
    """Set one runtime-settable knob (model, reasoning_effort, sandbox, work limits, …). Validated against
    the allowlist in core.app_settings; persists to ~/.kotoba/settings.yaml and applies on the next request
    (every call site reads its value at request time). 400 on an unknown key or invalid value."""
    from kotoba.core.app_settings import set_runtime

    key = str(body.get("key", "")).strip()
    if not key or "value" not in body:
        raise HTTPException(status_code=400, detail="key and value required")
    try:
        value = set_runtime(key, body["value"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"{key}: {e}")
    return {"ok": True, "key": key, "value": value}


@app.post("/api/settings/llm-key")
async def set_llm_key(body: dict, http: Request) -> dict:
    """Save or clear the in-app API key for an LLM provider, after asking the provider whether it works.
    Encrypted at rest, decrypted only in memory, never logged or returned to the model. An empty value
    clears it and falls back to the environment variable.

    Verify-then-store. Saving first left a key that never answered in the keystore, read as a configured
    machine. Reordering the two calls could not fix it: the live-configuration test validates whatever is
    ALREADY ACTIVE, so after the save it would test this key and before it the previous one. The probe
    builds a client from the candidate alone and stores nothing.

    A refused key is a 400, not a 200 carrying `ok: false`: nothing was written at all."""
    from kotoba.core import first_run, keystore, llm, providers

    provider = str(body.get("provider", "")).strip().lower()
    if provider not in providers.PROVIDERS:
        raise HTTPException(status_code=400, detail=f"unknown provider: {provider}")
    raw = str(body.get("key", "") or "").strip()
    name = f"llm:{provider}:api_key"
    detail = ""
    if raw:
        if llm.looks_placeholder(raw):
            raise HTTPException(
                status_code=400,
                detail="that is the placeholder from the example file rather than a key")
        ok, detail = await first_run.verify_key(provider, raw)
        if not ok:
            raise HTTPException(status_code=400, detail=detail)
        try:
            await http.app.state.db.save_key(name, raw)
        except keystore.KeystoreUnavailable as e:
            raise HTTPException(status_code=500, detail=f"cannot store the key encrypted: {e}")
        llm.set_provider_key(provider, raw)
    else:
        await http.app.state.db.delete_key(name)
        llm.set_provider_key(provider, None)
    stored = not llm.looks_placeholder(await http.app.state.db.get_key(name) or "")
    return {"ok": True, "provider": provider, "has_key": stored, "detail": detail}


@app.post("/api/settings/llm-test")
async def test_llm_key(body: dict) -> dict:
    """Validate the ACTIVE provider, base_url and key with a small round trip before the user relies on
    it. It tests what is currently configured, which is why it could never gate a key being stored: a
    candidate is not active yet.

    "Never echoes the key" was a claim and not a mechanism. The detail here is the PROVIDER's own
    exception text, shown in the Settings panel — an endpoint that quotes the request back puts the live
    key on screen. The redaction runs BEFORE the length cut, because a cut landing inside the key would
    leave a prefix that no longer matches."""
    from kotoba.core import llm, providers

    client = llm.get_client()
    if client is None:
        return {"ok": False, "detail": "No API key set for the active provider."}
    model = llm.model_name("companion")
    try:
        await client.responses.create(model=model, input="ping", max_output_tokens=16, **llm.model_call_kwargs())
        return {"ok": True, "detail": f"{providers.get_spec().label} · {model} responded."}
    except Exception as e:
        key = getattr(client, "api_key", "") or ""
        detail = f"{type(e).__name__}: {e}"
        return {"ok": False, "detail": (detail.replace(key, "…") if key else detail)[:160]}


@app.post("/api/settings/voice-key")
async def set_voice_key(body: dict, http: Request) -> dict:
    """Save the ElevenLabs key — after asking ElevenLabs whether it is real, and only if it is.

    Verify-then-store is the order and not store-then-test: a saved key that only fails on the first
    thing the person actually asks her cannot be told apart from a broken install. Both halves live in
    `core.voice_key` and `kotoba setup` calls the same two functions, so a terminal install and a browser
    install accept and refuse exactly the same keys — including the refusals that are NOT refusals (a
    narrow-scoped key, or one no network could check, is kept). Follows the `llm-key` conventions: the
    key is never echoed back, never logged, and never reaches the model."""
    from kotoba.core import keystore, llm, voice_key

    raw = str(body.get("key", "") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="key required")
    if llm.looks_placeholder(raw):
        return {"ok": False, "detail": "That's the placeholder from the example file rather than a key.",
                "has_key": voice_key.configured()}
    ok, detail = await voice_key.verify(raw)
    if ok:
        try:
            await voice_key.save(http.app.state.db, raw)
        except keystore.KeystoreUnavailable as e:
            raise HTTPException(status_code=500, detail=f"cannot store the key encrypted: {e}")
    return {"ok": ok, "detail": detail, "has_key": voice_key.configured()}


@app.get("/api/setup/status")
async def setup_status(http: Request) -> dict:
    """Has this install ever been configured, and what has it got so far.

    `needed` is `core.first_run.needed` — the very predicate `kotoba setup` runs — so the terminal and
    the browser can never disagree about whether somebody has been through first run. Booleans and
    already-public settings only: no key is described beyond whether one exists.

    `catalogue` covers every provider at once, not the chosen one: the brain is picked on the screen
    before this, and a second round trip to learn what it serves would ask for what is already here.
    The three names travel for the same reason."""
    from kotoba.core import first_run, llm, providers, voice_key

    db = http.app.state.db
    soul = await db.fetch_soul_config()
    profile = await db.fetch_user_profile()
    voice_where = await voice_key.where(db)
    return {
        "needed": await first_run.needed(db),
        "provider": providers.active_provider_id(),
        "model": llm.model_name("companion"),
        "has_voice_key": bool(voice_where),
        # WHERE it lives, not only that it exists. The step offers to replace a key held in the app and
        # names the variable for one held in the environment; a single boolean said the wrong one.
        "voice": {"saved": voice_where == "app",
                  "env": voice_key.VOICE_KEY_ENV if voice_where == "env" else ""},
        # Per provider, because the brain step can change which one the key step is about. Read the
        # same two facts the Settings panel reads — one surface said "saved, type to replace" and the
        # other showed an empty box, so reconfiguring looked like it was about to wipe a working key.
        # A stored PLACEHOLDER is not a key: `first_run.needed` filters it, so reporting it as saved
        # said "one is saved, Enter keeps it" on the one step first run cannot skip — where Enter then
        # does nothing and there is no way forward. `looks_placeholder` is the single rule for both.
        "keys": {
            s.id: {"saved": not llm.looks_placeholder(await db.get_key(f"llm:{s.id}:api_key") or ""),
                   "env": s.key_env if not llm.looks_placeholder(os.getenv(s.key_env, "")) else ""}
            for s in providers.PROVIDERS.values()
        },
        "user_name": str(profile.get("name") or ""),
        "companion_name": str(soul.get("name") or ""),
        "language": str(soul.get("language") or ""),
        "catalogue": providers.catalogue(),
    }


@app.post("/api/setup/provider")
async def setup_provider(body: dict) -> dict:
    """Choose the brain: persist the provider AND pin `model` to one that provider serves.

    Deliberately not `/api/settings/runtime` with `key=provider`, which is the raw setting and only the
    raw setting: `model` is one global value rather than one per provider, so choosing xAI while it still
    reads gpt-5.6-luna makes the very next `llm-test` come back 404 and report a perfectly good xai- key
    as a bad one. `core.first_run.select_provider` is what the terminal wizard does."""
    from kotoba.core import first_run

    try:
        provider, model = first_run.select_provider(str(body.get("provider", "")).strip().lower())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "provider": provider, "model": model}


@app.post("/api/setup/model")
async def setup_model(body: dict) -> dict:
    """Choose which of the brain's models she thinks with — the step between the brain and the key.

    Not the raw runtime setting, which takes any string: a model belonging to the other company would be
    stored happily and surface as a 404 at the key check, reported as a bad key. One decision serves both
    front doors, so the terminal wizard and this screen accept exactly the same answers.

    `provider` is optional and the screen always sends it, which is what makes the pair atomic. The brain
    is chosen one step earlier by a fire-and-forget POST, so trusting it to have landed would mean a fast
    click validating the new provider's model against the old one."""
    from kotoba.core import first_run, providers

    wanted = str(body.get("provider", "")).strip().lower()
    try:
        if wanted:
            first_run.select_provider(wanted)
        model = first_run.select_model(providers.active_provider_id(), str(body.get("model", "")).strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "provider": providers.active_provider_id(), "model": model}


@app.post("/api/mcp/connect")
async def mcp_connect(body: dict, http: Request) -> dict:
    """Connect an MCP server from Settings: a known server BY NAME (curated allowlist), OR a PENDING server
    (one Kotoba found but that needs auth) by supplying its `token`."""
    from kotoba.core.mcp import pending
    from kotoba.core.mcp.config import save_server
    from kotoba.core.mcp.known import build_cfg

    name = str(body.get("name", "")).strip()
    token = str(body.get("token", "")).strip()
    mcp = getattr(http.app.state, "mcp", None)
    if mcp is None:
        raise HTTPException(status_code=503, detail="mcp unavailable")
    db = http.app.state.db

    pend = pending.get(name) if token else None
    if pend is not None:
        cfg = dict(pend.get("cfg") or {})
        if cfg.get("url"):
            connect_cfg = {**cfg, "headers": {**(cfg.get("headers") or {}), "Authorization": f"Bearer {token}"}}
            persist = {**cfg, "auth_key": f"mcp:{name}"}
        else:
            env_name = (cfg.get("auth_env") or f"{name}_token").upper()
            connect_cfg = {**cfg, "env": {**(cfg.get("env") or {}), env_name: token}}
            persist = {**cfg, "auth_key": f"mcp:{name}", "auth_env": env_name}
        try:
            tools = await mcp.connect(name, connect_cfg)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"couldn't connect: {type(e).__name__}")
        if not tools:
            await mcp.disconnect(name)
            raise HTTPException(status_code=502, detail="connected but no tools")
        await db.save_key(f"mcp:{name}", token)
        try:
            save_server(name, persist)
        except Exception:
            logging.getLogger("kotoba.mcp").warning(
                "MCP: failed to persist server %r — it will not reconnect after restart", name, exc_info=True
            )
        try:
            pending.clear(name)
        except Exception:
            pass
        return {"ok": True, "name": name, "tools": tools}

    from kotoba.core.mcp.known import resolve_known

    built = build_cfg(name)
    if built is None:
        raise HTTPException(status_code=404, detail="unknown server")
    canonical, cfg = built

    resolved = resolve_known(canonical)
    needs = (resolved[1].get("needs") if resolved else None) or []
    from kotoba.core.mcp.known import resolve_env_need, setup_note
    for need in needs:
        if need.startswith("env:"):
            env_var = need.split(":", 1)[1]
            if not resolve_env_need(env_var):
                # A server that signs itself in gets its own instructions: "set a token" is wrong for
                # Google Calendar, whose setup is a Google Cloud client plus a one-off local approval.
                raise HTTPException(
                    status_code=400,
                    detail=setup_note(canonical)
                    or f"{canonical} needs an API token. Set {env_var} in the backend, then reconnect.",
                )
        elif need.startswith("oauth:"):
            desc = (resolved[1].get("description") if resolved else "") or ""
            pending.record(canonical, {"url": cfg.get("url", "")},
                           "this server needs a browser sign-in", "oauth", desc)
            return {"ok": True, "pending": True, "name": canonical}

    if canonical == "browser":
        from kotoba.core.mcp.browser_launch import ensure_browser

        cdp = os.getenv("KOTOBA_BROWSER_CDP", "").strip()
        if cdp:
            await ensure_browser(cdp)
    try:
        tools = await mcp.connect(canonical, cfg)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"couldn't connect: {type(e).__name__}")
    if not tools:
        await mcp.disconnect(canonical)
        raise HTTPException(status_code=502, detail="connected but no tools")
    try:
        # Strip headers: auth is re-resolved from env at boot; persisting them writes a live PAT to YAML.
        save_server(canonical, {k: v for k, v in cfg.items() if k != "headers"})
    except Exception:
        logging.getLogger("kotoba.mcp").warning(
            "MCP: failed to persist server %r — it will not reconnect after restart", canonical, exc_info=True
        )
    return {"ok": True, "name": canonical, "tools": tools}


def _public_url() -> str:
    """Public base URL the OAuth authorization server redirects back to (used to build the MCP OAuth
    redirect URI). Must be set via KOTOBA_PUBLIC_URL to the deployment's externally-reachable origin when
    using OAuth-based MCP servers; empty otherwise (no default host — never hardcode a specific deploy)."""
    return os.getenv("KOTOBA_PUBLIC_URL", "").rstrip("/")


@app.post("/api/mcp/oauth/start")
async def mcp_oauth_start(body: dict, http: Request) -> dict:
    """Begin a browser sign-in for a PENDING OAuth server. Returns {flow_id, auth_url}; the frontend opens
    auth_url in a new tab, the user approves, then calls /api/mcp/oauth/finish."""
    from kotoba.core.mcp import oauth, pending

    if not oauth.oauth_available():
        raise HTTPException(status_code=503, detail="oauth unavailable")
    name = str(body.get("name", "")).strip()
    pend = pending.get(name)
    if pend is None:
        raise HTTPException(status_code=404, detail="unknown pending server")
    url = (pend.get("cfg") or {}).get("url")
    if not url:
        raise HTTPException(status_code=400, detail="server has no url")
    base = _public_url()
    if not base:
        raise HTTPException(status_code=400, detail="set KOTOBA_PUBLIC_URL to your public origin to use OAuth MCP sign-in")
    redirect_uri = f"{base}/api/mcp/oauth/callback"
    try:
        flow_id, auth_url = await oauth.start_oauth(name, url, redirect_uri)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"couldn't start sign-in: {type(e).__name__}")
    return {"ok": True, "flow_id": flow_id, "auth_url": auth_url}


@app.get("/api/mcp/oauth/callback")
async def mcp_oauth_callback(http: Request):
    """The OAuth authorization server redirects the user's browser here with ?code&state. We wake the
    matching in-flight flow and show a tiny close-this-tab page. NOT behind the API gate (the auth server
    can't send our token) — the opaque `state` is what authorizes it."""
    from fastapi.responses import HTMLResponse

    from kotoba.core.mcp import oauth

    code = http.query_params.get("code") or ""
    state = http.query_params.get("state") or ""
    err = http.query_params.get("error")
    ok = False
    if code and state and not err:
        ok = oauth.resolve_callback(state, code)
    msg = ("✓ Signed in — you can close this tab and return to Kotoba."
           if ok else "Sign-in could not be completed. You can close this tab and try again in Kotoba.")
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Kotoba</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#1a1320;color:#f6e8ff;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}"
        ".card{background:#2a2030;padding:2rem 2.5rem;border-radius:1rem;box-shadow:0 8px 40px #0008;"
        "max-width:22rem;text-align:center}</style></head>"
        f"<body><div class='card'><p>{msg}</p></div>"
        "<script>setTimeout(()=>{try{window.close()}catch(e){}},2500)</script></body></html>"
    )
    return HTMLResponse(html, status_code=200 if ok else 400)


@app.post("/api/mcp/oauth/finish")
async def mcp_oauth_finish(body: dict, http: Request) -> dict:
    """Long-poll: wait for the user to approve + the token exchange, then connect the server with the
    obtained Bearer token (same path as a pasted API key) so it persists + reconnects on boot."""
    from kotoba.core.mcp import oauth, pending
    from kotoba.core.mcp.config import save_server

    name = str(body.get("name", "")).strip()
    flow_id = str(body.get("flow_id", "")).strip()
    if not name or not flow_id:
        raise HTTPException(status_code=400, detail="name and flow_id required")
    mcp = getattr(http.app.state, "mcp", None)
    if mcp is None:
        raise HTTPException(status_code=503, detail="mcp unavailable")
    db = http.app.state.db
    pend = pending.get(name)
    if pend is None:
        raise HTTPException(status_code=404, detail="unknown pending server")
    cfg = dict(pend.get("cfg") or {})
    if not cfg.get("url"):
        raise HTTPException(status_code=400, detail="server has no url")
    try:
        record = await oauth.finish_oauth(flow_id, db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"sign-in failed: {type(e).__name__}")
    token = record["access_token"]
    connect_cfg = {**cfg, "headers": {**(cfg.get("headers") or {}), "Authorization": f"Bearer {token}"}}
    persist = {**cfg, "auth_key": f"mcp:{name}"}

    async def _drop_orphan_keys() -> None:
        logging.getLogger("kotoba.mcp").info("MCP OAuth: rolling back stored access for server=%r (connect failed)", name)
        for key in (f"mcp:{name}", f"mcp_oauth:{name}"):
            try:
                await db.delete_key(key)
            except Exception:
                pass

    try:
        tools = await mcp.connect(name, connect_cfg)
    except Exception as e:
        await _drop_orphan_keys()
        raise HTTPException(status_code=502, detail=f"couldn't connect: {type(e).__name__}")
    if not tools:
        await mcp.disconnect(name)
        await _drop_orphan_keys()
        raise HTTPException(status_code=502, detail="connected but no tools")
    try:
        save_server(name, persist)
    except Exception:
        logging.getLogger("kotoba.mcp").warning(
            "MCP OAuth: failed to persist server %r — it will not reconnect after restart", name, exc_info=True
        )
    try:
        pending.clear(name)
    except Exception:
        pass
    return {"ok": True, "name": name, "tools": tools}


@app.delete("/api/mcp/{name}")
async def mcp_disconnect(name: str, http: Request) -> dict:
    from kotoba.core.mcp import pending
    from kotoba.core.mcp.config import remove_server

    mcp = getattr(http.app.state, "mcp", None)
    if mcp is not None:
        await mcp.disconnect(name)
    for fn in (lambda: remove_server(name), lambda: pending.clear(name)):
        try:
            fn()
        except Exception:
            pass
    for key in (f"mcp:{name}", f"mcp_oauth:{name}"):
        try:
            await http.app.state.db.delete_key(key)
        except Exception:
            pass
    return {"ok": True}


@app.delete("/api/keys/{name}")
async def delete_key(name: str, http: Request) -> dict:
    """Delete a saved key — from the DATABASE and from wherever this process already decrypted it into.

    Both keys are cached in a module global at startup, so dropping the row alone leaves the running
    process still using it: the same shape as the logout that cleared the token in memory and left it
    in sessionStorage. Deleting a key must mean she stops using it now, not after a restart."""
    await http.app.state.db.delete_key(name)
    if name.startswith("llm:") and name.count(":") >= 2:
        from kotoba.core import llm

        llm.set_provider_key(name.split(":")[1], None)
    if name == core_engine.VOICE_KEY_NAME:
        try:
            from kotoba.core.voice import config as voice_config

            voice_config.set_api_key(None)
        except ImportError:
            pass
    return {"ok": True}


@app.delete("/api/cron/{job_id}")
async def delete_cron(job_id: str, http: Request) -> dict:
    await http.app.state.db.deactivate_cronjob(job_id)
    return {"ok": True}


@app.delete("/api/approvals")
async def revoke_approval_by_query(pattern: str, http: Request) -> dict:
    """Revoke a saved 'always allow' so it asks again — the route that can carry EVERY saved grant.

    A family is one token and rides in a path segment happily. An exact grant is a whole command line
    with spaces, slashes and quotes in it, and a percent-encoded `/` is decoded before routing, so the
    path form below can only ever reach half of what Settings lists. The query string has no such
    reserved character, so this is the one the panel uses for both."""
    await http.app.state.db.delete_approved_command(pattern)
    return {"ok": True}


@app.delete("/api/approvals/{pattern}")
async def revoke_approval(pattern: str, http: Request) -> dict:
    """Revoke a previously 'always allowed' command family so it asks for approval again. Kept for
    callers that predate the query form; a pattern containing a slash needs that one."""
    await http.app.state.db.delete_approved_command(pattern)
    return {"ok": True}


@app.delete("/api/memory/topic/{slug}")
async def delete_memory_topic(slug: str) -> dict:
    import asyncio

    from kotoba.core import user_memory

    ok = await asyncio.to_thread(user_memory.delete_topic, slug)
    return {"ok": ok}


#: Read off the build rather than from `mimetypes`, which on Windows answers out of the registry: a
#: machine that maps .js to text/plain would, with our own nosniff, refuse to run every chunk.
_MEDIA = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".txt": "text/plain; charset=utf-8",
          ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp",
          ".json": "application/json", ".woff2": "font/woff2", ".ico": "image/x-icon"}


def _cache_for(rel: str) -> str:
    return "public, max-age=31536000, immutable" if rel.startswith("_next/") else "private, no-cache"


@app.exception_handler(404)
async def serve_frontend(request: Request, exc):
    """The packaged UI, reached only where routing found nothing — so it shadows no route and leaves
    every existing 404 and 405 exactly as it was. A catch-all route would not: it turns every unknown
    path into a partial match and answers 405 to anything but GET. A Mount is worse still, since it
    also matches WebSocket scopes and its html mode wants `index.html` where this build writes
    `app.html`."""
    from fastapi.responses import FileResponse, RedirectResponse

    path = request_path(request.scope)
    if request.method not in ("GET", "HEAD") or frontend.root() is None:
        return JSONResponse({"detail": getattr(exc, "detail", "not found")}, status_code=404)
    prefix = (request.scope.get("root_path") or "").rstrip("/")
    access = frontend.classify(path)

    if path in ("", "/"):
        return _framed(RedirectResponse(f"{prefix}{frontend.HOME}", status_code=302))

    # Checked again here on purpose: the middleware is the chokepoint, and it has failed open before.
    if access is frontend.Access.GATED and gate.enabled():
        if not gate.verify_session(request.cookies.get(gate.COOKIE)):
            return _login_redirect(request.scope, path)

    if access is frontend.Access.DENIED:
        # Not part of the build: hand back whatever the API was already going to say.
        return JSONResponse({"detail": getattr(exc, "detail", "not found")}, status_code=404)

    found = frontend.resolve(path)
    if found is None:
        page = frontend.resolve("/404.html")
        resp = (FileResponse(page, status_code=404, media_type=_MEDIA[".html"]) if page
                else JSONResponse({"detail": "not found"}, status_code=404))
        return _framed(resp)

    if frontend.page_of(found) == "login" and gate.enabled():
        if gate.verify_session(request.cookies.get(gate.COOKIE)):
            dest = frontend.safe_next(request.query_params.get("next"))
            resp = _framed(RedirectResponse(f"{prefix}{dest}", status_code=302))
            resp.headers["Cache-Control"] = "no-store"
            return resp

    rel = found.relative_to(frontend.root()).as_posix()
    resp = _framed(FileResponse(found, media_type=_MEDIA.get(found.suffix, "application/octet-stream")))
    resp.headers["Cache-Control"] = _cache_for(rel)
    return resp
