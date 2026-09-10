"""OAuth 2.1 sign-in for remote MCP servers that need a browser login (Notion, GitHub-hosted, …).

API-key servers connect by injecting `Authorization: Bearer <token>`. OAuth servers are detected at
preflight but could not connect, so we run the SDK's OAuth flow (discovery → dynamic registration →
PKCE → callback → token exchange) for a token, then connect through that SAME Bearer path.

Flows are keyed by flow_id, NOT by server, so nothing stops two concurrent sign-ins for one name and
whichever finishes last overwrites `mcp_oauth:<name>`; the only cap is _FLOW_MAX_LIVE overall. We
persist enough to REFRESH ourselves — the SDK auto-refreshes only for the live connection's auth —
as {refresh_token, expires_at, token_endpoint, client_id, client_secret?, scope}."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx

log = logging.getLogger("kotoba.mcp")

try:
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    _OAUTH_AVAILABLE = True
except Exception:  # pragma: no cover - only when the SDK lacks OAuth
    _OAUTH_AVAILABLE = False


def oauth_available() -> bool:
    return _OAUTH_AVAILABLE


# Phase timeouts: producing the authorization URL after start / the user approving in the browser.
_FLOW_AUTH_TIMEOUT = 30.0
_FLOW_FINISH_TIMEOUT = 300.0


class _MemTokenStorage:
    """In-memory TokenStorage for one flow (the SDK persists tokens/registration through this)."""

    def __init__(self) -> None:
        self._tokens = None
        self._client_info = None

    async def get_tokens(self):
        return self._tokens

    async def set_tokens(self, tokens) -> None:
        self._tokens = tokens

    async def get_client_info(self):
        return self._client_info

    async def set_client_info(self, client_info) -> None:
        self._client_info = client_info


# flow_id -> {"auth_url": Future, "code": Future, "result": Future, "name": str, "ts": float}
_flows: dict[str, dict] = {}
_state_to_flow: dict[str, str] = {}  # OAuth `state` -> flow_id, to route the callback


def _new_future() -> asyncio.Future:
    return asyncio.get_running_loop().create_future()


async def _on_redirect(flow_id: str, url: str) -> None:
    """SDK calls this with the authorization URL. Record its `state` (so the HTTP callback can find this
    flow) and hand the URL to whoever is waiting in start_oauth (the browser opens it)."""
    flow = _flows.get(flow_id)
    # Only a LIVE flow may record a state: start_oauth's auth-URL timeout _cleanup()s without cancelling this
    # task, and a dead flow's entry routes nothing yet no sweep would ever reclaim it.
    if flow is None:
        return
    try:
        state = (parse_qs(urlparse(url).query).get("state") or [None])[0]
        if state:
            _state_to_flow[state] = flow_id
    except Exception:
        pass
    if flow and not flow["auth_url"].done():
        flow["auth_url"].set_result(url)


async def _on_callback(flow_id: str) -> tuple[str, str | None]:
    """SDK calls this and blocks until the browser hits our callback endpoint (resolve_callback)."""
    flow = _flows.get(flow_id)
    if not flow:
        raise RuntimeError("oauth flow gone")
    code, state = await flow["code"]
    return code, state


def resolve_callback(state: str, code: str) -> bool:
    """Called by GET /api/mcp/oauth/callback. Wakes the matching flow. True if a flow was waiting."""
    flow_id = _state_to_flow.get(state or "")
    flow = _flows.get(flow_id) if flow_id else None
    if flow and not flow["code"].done():
        flow["code"].set_result((code, state))
        return True
    return False


def _expires_at(tokens) -> float:
    exp = getattr(tokens, "expires_in", None)
    return time.time() + (float(exp) if exp else 3600.0)


async def _run_flow(flow_id: str, server_url: str, redirect_uri: str) -> None:
    """Drive the SDK OAuth flow to completion; on success put the token record on the flow's `result`."""
    flow = _flows[flow_id]
    storage = _MemTokenStorage()
    try:
        provider = OAuthClientProvider(
            server_url=server_url,
            client_metadata=OAuthClientMetadata(
                redirect_uris=[redirect_uri],
                client_name="Kotoba",
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",  # public client + PKCE
            ),
            storage=storage,
            redirect_handler=lambda url: _on_redirect(flow_id, url),
            callback_handler=lambda: _on_callback(flow_id),
            timeout=_FLOW_FINISH_TIMEOUT,
        )
        # One request triggers the whole SDK flow: 401 → discover → register → authorize → callback → exchange → retry.
        async with httpx.AsyncClient(auth=provider, timeout=httpx.Timeout(_FLOW_FINISH_TIMEOUT)) as client:
            try:
                await client.get(server_url)
            except Exception:
                pass  # the retried request may still error (e.g. needs MCP init) — the token is what matters
        tokens = await storage.get_tokens()
        if tokens is None or not getattr(tokens, "access_token", None):
            raise RuntimeError("no access token obtained")
        ctx = provider.context
        meta = getattr(ctx, "oauth_metadata", None)
        cinfo = getattr(ctx, "client_info", None)
        token_endpoint = getattr(meta, "token_endpoint", None) if meta else None
        record = {
            "access_token": tokens.access_token,
            "refresh_token": getattr(tokens, "refresh_token", None),
            "expires_at": _expires_at(tokens),
            "token_endpoint": str(token_endpoint) if token_endpoint else None,  # pydantic AnyUrl → str
            "client_id": getattr(cinfo, "client_id", None) if cinfo else None,
            "client_secret": getattr(cinfo, "client_secret", None) if cinfo else None,
            "scope": getattr(tokens, "scope", None),
        }
        if not flow["result"].done():
            flow["result"].set_result(record)
    except Exception as e:
        log.warning("MCP OAuth flow %s failed: %s", flow_id, e)
        if not flow["result"].done():
            flow["result"].set_exception(e)


# Abandoned sign-ins would linger in _flows forever; the live-flow cap stops a start_oauth loop growing the dicts unbounded.
_FLOW_MAX_AGE = _FLOW_FINISH_TIMEOUT + 60.0
_FLOW_MAX_LIVE = 12


def _gc_flows(now: float) -> None:
    for fid, flow in list(_flows.items()):
        if now - flow.get("ts", now) > _FLOW_MAX_AGE:
            _cleanup(fid)


async def start_oauth(name: str, server_url: str, redirect_uri: str) -> tuple[str, str]:
    """Begin an OAuth sign-in. Returns (flow_id, authorization_url) — the browser opens the URL."""
    if not _OAUTH_AVAILABLE:
        raise RuntimeError("MCP OAuth not available")
    now = time.time()
    _gc_flows(now)
    if len(_flows) >= _FLOW_MAX_LIVE:
        raise RuntimeError("too many sign-ins in progress — finish or wait for one to time out, then retry")
    flow_id = secrets.token_urlsafe(24)
    _flows[flow_id] = {
        "auth_url": _new_future(), "code": _new_future(), "result": _new_future(),
        "name": name, "ts": time.time(),
    }
    asyncio.create_task(_run_flow(flow_id, server_url, redirect_uri))
    try:
        auth_url = await asyncio.wait_for(_flows[flow_id]["auth_url"], timeout=_FLOW_AUTH_TIMEOUT)
    except Exception as e:
        _cleanup(flow_id)
        raise RuntimeError(f"couldn't start sign-in: {e}") from e
    return flow_id, auth_url


async def finish_oauth(flow_id: str, db) -> dict:
    """Wait for the user to approve + the token exchange to complete; persist the refresh record; return
    {access_token, ...}. Raises on timeout/failure. Caller then connects the server with the Bearer."""
    flow = _flows.get(flow_id)
    if flow is None:
        raise RuntimeError("unknown or expired sign-in")
    name = flow["name"]
    try:
        record = await asyncio.wait_for(flow["result"], timeout=_FLOW_FINISH_TIMEOUT)
    finally:
        _cleanup(flow_id)
    # Refresh material goes to the backend-only DB — NEVER the pending yaml.
    await _save_record(db, name, record)
    return record


async def _save_record(db, name: str, record: dict) -> None:
    # Diagnostic only — logs NO sensitive value, just presence flags + expiry (see CWE-532).
    log.info("MCP OAuth: storing access for server=%r refreshable=%s expires_at=%s",
             name, bool(record.get("refresh_token")), record.get("expires_at"))
    await db.save_key(f"mcp:{name}", record["access_token"])  # Bearer for hydrate_auth/connect
    await db.save_key(f"mcp_oauth:{name}", json.dumps({
        "refresh_token": record.get("refresh_token"),
        "expires_at": record.get("expires_at"),
        "token_endpoint": record.get("token_endpoint"),
        "client_id": record.get("client_id"),
        "client_secret": record.get("client_secret"),
        "scope": record.get("scope"),
    }))


async def _load_record(db, name: str) -> dict | None:
    raw = await db.get_key(f"mcp_oauth:{name}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def refresh(db, name: str) -> bool:
    """Refresh one server's access token via the standard refresh_token grant. Returns True on success;
    updates mcp:<name> + mcp_oauth:<name>. False if there's nothing to refresh or it failed."""
    rec = await _load_record(db, name)
    if not rec or not rec.get("refresh_token") or not rec.get("token_endpoint"):
        return False
    data = {
        "grant_type": "refresh_token",
        "refresh_token": rec["refresh_token"],
    }
    if rec.get("client_id"):
        data["client_id"] = rec["client_id"]
    if rec.get("client_secret"):
        data["client_secret"] = rec["client_secret"]
    if rec.get("scope"):
        data["scope"] = rec["scope"]
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(rec["token_endpoint"], data=data,
                                  headers={"Accept": "application/json"})
        if r.status_code >= 400:
            log.warning("MCP OAuth refresh for %r failed: %s", name, r.status_code)
            return False
        tok = r.json()
    except Exception as e:
        log.warning("MCP OAuth refresh for %r errored: %s", name, e)
        return False
    access = tok.get("access_token")
    if not access:
        return False
    expires_in = tok.get("expires_in")
    new_rec = {
        "access_token": access,
        # refresh token MAY rotate; keep the new one if present, else the old.
        "refresh_token": tok.get("refresh_token") or rec.get("refresh_token"),
        "expires_at": time.time() + (float(expires_in) if expires_in else 3600.0),
        "token_endpoint": rec.get("token_endpoint"),
        "client_id": rec.get("client_id"),
        "client_secret": rec.get("client_secret"),
        "scope": tok.get("scope") or rec.get("scope"),
    }
    await _save_record(db, name, new_rec)
    return True


def expiring_soon(rec: dict, within: float = 120.0) -> bool:
    exp = rec.get("expires_at")
    return bool(exp) and (float(exp) - time.time()) < within


async def refresh_loop(db, mcp, interval: float = 60.0) -> None:
    """Lifespan task: keep OAuth MCP access tokens fresh. Every `interval`s, for each `mcp_oauth:*` record
    expiring within ~2 min, refresh it and reconnect the live MCP session with the new Bearer. Best-effort —
    never raises (an error just means we try again next tick; a hard revoke logs and is left for re-auth)."""
    from kotoba.core.mcp.config import hydrate_auth, load_servers

    while True:
        await asyncio.sleep(interval)
        try:
            names = [k["name"].split("mcp_oauth:", 1)[1]
                     for k in await db.list_key_names()
                     if k.get("name", "").startswith("mcp_oauth:")]
        except Exception:
            continue
        for name in names:
            try:
                rec = await _load_record(db, name)
                if not rec or not expiring_soon(rec):
                    continue
                if not await refresh(db, name):
                    continue
                # hydrate_auth wants a SYNC get_key — pre-read the fresh token into a dict it can look up.
                cfg = load_servers().get(name)
                if cfg is not None and mcp is not None and isinstance(cfg, dict) and cfg.get("auth_key"):
                    tok = {cfg["auth_key"]: await db.get_key(cfg["auth_key"])}
                    hydrated = hydrate_auth(cfg, tok.get)
                    try:
                        await mcp.reconnect(name, hydrated)
                    except Exception:
                        pass
            except Exception:
                log.warning("MCP OAuth refresh loop hiccup for %r", name, exc_info=True)


def _cleanup(flow_id: str) -> None:
    flow = _flows.pop(flow_id, None)
    if not flow:
        return
    for st, fid in list(_state_to_flow.items()):
        if fid == flow_id:
            _state_to_flow.pop(st, None)
