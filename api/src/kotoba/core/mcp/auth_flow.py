"""Shared reaction to an MCP server that needs auth during connect (`_AuthRequired`), used by BOTH
install paths so a token server and an oauth server are handled identically whichever found them.

- oauth → record PENDING (secret-free) plus an honest "finish it in Settings → Sign in" notice: a
  browser sign-in cannot be done from a tool call.
- token → ask for an API key (masked box), retry the connect with a Bearer header, persist the token
  in the backend-only credential store and an `auth_key` POINTER in the saved cfg, never the token.
  A persistence failure is carried in the reply — "connected for THIS session only" — never papered
  over, and the pointer is written only when the token really landed."""
from __future__ import annotations

import logging

from kotoba.core import interaction
from kotoba.core.mcp import config, pending

log = logging.getLogger("kotoba.mcp")


async def _drop_stored_token(ctx, name: str) -> None:
    """The credential must not outlive the server it belongs to. By the time a connect comes back with no
    tools the token this call asked for is already in the store, and "I removed it" is then a
    half-truth: the server is gone and the secret is still there, under a name nothing mentions."""
    db = getattr(ctx, "db", None)
    if db is None:
        return
    for key in (f"mcp:{name}", f"mcp_oauth:{name}"):
        try:
            await db.delete_key(key)
        except Exception:
            log.warning("MCP: could not drop the stored credential for %r", name, exc_info=True)


def _with_bearer(cfg: dict, token: str) -> dict:
    """A cfg + an `Authorization: Bearer` header for connecting (token NOT persisted in this copy)."""
    out = {k: v for k, v in (cfg or {}).items()}
    out["headers"] = {**(out.get("headers") or {}), "Authorization": f"Bearer {token}"}
    return out


async def finish_connect(ctx, name: str, tools, persist_cfg: dict, unsaved: str = "",
                         stored_token: bool = False) -> str:
    """Verify the connect exposed tools, persist the server, activate it for the session, confirm. Shared by
    the plain + auth paths. `persist_cfg` is the cfg to save (secrets already stripped / pointer added).

    `stored_token` says this call put a credential in the store, and ONLY then is it taken back out: a
    key that was already there belongs to whatever saved it.

    `unsaved` names something a caller already failed to persist ("the token"); a save_server failure here
    joins it. Either way the reply carries the persistence outcome: a bare "Connected" over a failed save
    claimed a persistence that did not happen — the session works, but after a restart the server is gone
    or lands back in pending asking for its token."""
    if not tools:
        try:
            await ctx.mcp.disconnect(name)
        except Exception:
            pass
        if stored_token:
            await _drop_stored_token(ctx, name)
        from kotoba.core.loop import note_tool_failure

        note_tool_failure(f"{name} connected but exposed no usable tools")
        return f"I connected '{name}' but it didn't offer any usable tools, so I removed it."
    try:
        config.save_server(name, persist_cfg)
    except Exception:
        unsaved = "the connection"
        log.warning("MCP: failed to persist server %r — it will not reconnect after restart", name, exc_info=True)
    try:
        pending.clear(name)
    except Exception:
        pass
    try:
        from kotoba.core import mcp_active

        mcp_active.activate(getattr(ctx, "session_id", None), name)  # installed on demand → usable now
    except Exception:
        pass
    pretty = ", ".join(t.split("__", 1)[-1] for t in tools[:8])
    reply = f"Connected '{name}' — I can now: {pretty}."
    if unsaved:
        reply += (f" One heads-up to pass on: I couldn't save {unsaved}, so it's connected for THIS "
                  "session only — after a restart it will need to be connected again.")
    return reply


async def handle_auth(ctx, name: str, clean_cfg: dict, description: str, auth) -> str:
    """React to `_AuthRequired`. oauth → pending + Settings notice; token → ask + retry + persist; a
    skipped token → pending so the user can finish it from Settings later.

    `clean_cfg` is NOT secret-free, whatever the name says: mcp_install hands over `known.build_cfg`'s
    output, which for github carries the real PAT in `headers`. Nothing here has to strip it, because
    every path that WRITES it does — `pending.record` and `config.save_server` (via finish_connect) both
    call `strip_secrets` themselves, for exactly this reason. Do not add a caller that persists it
    directly."""
    sid = getattr(ctx, "session_id", None)
    description = description or ""

    if auth.kind == "oauth":
        pending.record(name, clean_cfg, auth.reason, "oauth", description)
        return (f"'{name}' needs a browser sign-in I can't do on my own — I've added it to Settings under "
                "'Needs connection' so you can finish it there with the 'Sign in' button.")

    # token: ask the user for an API key/token (masked) and retry ONCE.
    card: dict = {}
    token = await interaction.request_input(
        sid, f"{name} needs an API key/token to connect — paste it (or skip)", "secret", card=card
    )
    if not token:
        # Skipped, expired, or never drawn — the record is the same, but which one it was is not, and
        # a caller that cannot tell them apart is how a timeout gets reported as somebody's decision.
        log.info("no token for %s (%s)", name, card.get("verdict") or interaction.UNANSWERED)
        pending.record(name, clean_cfg, auth.reason, "token", description)
        return (f"No problem — I left '{name}' in Settings under 'Needs connection'; add the token there "
                "whenever you want and I'll connect it.")
    try:
        tools = await ctx.mcp.connect(name, _with_bearer(clean_cfg, token))
    except Exception:
        from kotoba.core.loop import note_tool_failure

        pending.record(name, clean_cfg, auth.reason, "token", description)
        note_tool_failure(f"the token given for {name} did not connect it")
        return f"That token didn't get '{name}' connected — I left it in Settings to try again."

    # Persist the token in the backend-only credential store; the saved cfg keeps a POINTER, never the
    # token. The pointer is written ONLY when the token really landed: a dead pointer is worse than none —
    # it also blocks engine._connect_saved's re-resolve-from-env for known servers, and either way boot
    # ends in pending. finish_connect tells the user when it didn't land.
    token_saved = False
    try:
        if getattr(ctx, "db", None) is not None:
            await ctx.db.save_key(f"mcp:{name}", token)
            token_saved = True
    except Exception:
        log.warning("MCP: failed to store the token for %r — it must be re-entered after a restart",
                    name, exc_info=True)
    persist = {k: v for k, v in (clean_cfg or {}).items()}
    if token_saved:
        persist["auth_key"] = f"mcp:{name}"
    return await finish_connect(ctx, name, tools, persist,
                                unsaved="" if token_saved else "the token",
                                stored_token=token_saved)
