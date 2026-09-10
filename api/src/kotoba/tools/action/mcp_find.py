"""mcp_find — discover and install a NEW MCP server from the OFFICIAL registry, with approval.

The open-ended counterpart to mcp_install: search the registry, vet the results, show the top candidate
for on-screen approval (installing runs third-party code), collect secrets, connect, persist. The
approval goes through ask_approval, which carries the CHANNEL — asked directly it got the voice clock.

Names discovered here are never trusted: a registry name reduced to a short local one is SUFFIXED if it
lands on a privileged one, because `browser` is always-active and the server half of the {{secret:…}}
allowlist. And `core.mcp.client` must be imported INSIDE the functions — at module level it pulls
tools.registry, whose discover() lands back here half-built and silently loses every action tool."""
from __future__ import annotations

import hashlib
import re

from kotoba.core import ephemeral_secrets, interaction
# `config` and `pending` look uncalled and are not: both are reached as attributes of THIS module, so
# removing either takes the seam with it. core.mcp.client is absent by design — see the module docstring.
from kotoba.core.mcp import auth_flow, config, pending, registry_search  # noqa: F401
from kotoba.core.text_security import scrub

SCHEMA = {
    "type": "function",
    "name": "mcp_find",
    "description": (
        "Discover and install a NEW capability you have no tool for, by searching the official MCP "
        "registry. Pass ONE concise keyword for the service — the app/product name (e.g. 'Spotify', "
        "'Postgres', 'Stripe'), NOT a sentence like 'control Spotify MCP server' (a long query "
        "fails). I find a real server, show it to the user for approval, install and test it, then its "
        "tools are available next iteration. Use ONLY when no existing tool or known server fits — for "
        "browser/filesystem/github/google calendar/memory/notion/linear/slack use mcp_install instead. "
        "If it returns nothing, try ONE different keyword, then stop and tell the user — don't loop."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "ONE concise service/capability keyword, e.g. 'Spotify' or 'Postgres'."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "mcp"
RISK = "exec"          # connecting runs third-party code — request_approval is the gate
TIMEOUT = 180
# Waits on a human, so the loop must NOT compute-cancel it at TIMEOUT; interaction bounds that wait.
INTERACTIVE = True

ANNOUNCE = "Let me look for one in the MCP registry..."
HEARTBEAT = ["Searching the registry...", "Checking what's available..."]
COMPLETE = "Done looking — I'll tell you how that went."
FAIL = "I couldn't get that one set up. Want me to try different words?"
EXPRESSIONS = {"focus": "thinking", "done": "excited", "fail": "embarrassed"}


# Mirror of registry_search._GENERIC_LABELS, kept local so this module stays import-light.
_GENERIC_TAILS = {"mcp", "server", "mcp-server", "api", "app", "service", "remote"}
_GENERIC_NS_LABELS = {"com", "io", "ai", "app", "org", "net", "dev", "co", "sh", "xyz", "cloud", "inc",
                      "www", "github", "gitlab", "modelcontextprotocol"}


def _reserved_names() -> set[str]:
    """Server names that carry privilege, so a DISCOVERED third-party server must never claim one.

    `browser` is the sharp case: it is always-active (no activate_tools needed) and it is the server half
    of the {{secret:…}} allowlist, so a package published as `browser-mcp` or `mcp-server-browser` — both
    of which _local_name reduces to `browser` — would be handed the user's one-time password. The curated
    KNOWN_SERVERS names are reserved for the same reason: they are trusted by name elsewhere."""
    from kotoba.core.mcp.client import _SECRET_ALLOWLIST
    from kotoba.core.mcp.known import KNOWN_SERVERS
    from kotoba.core import mcp_active

    out = set(KNOWN_SERVERS) | mcp_active.always_active()
    out |= {k.split("__", 1)[0] for k in _SECRET_ALLOWLIST if "__" in k}
    return out


def _local_name(registry_name: str) -> str:
    """Derive a short local server name from a registry name. A meaningful tail is kept
    ('io.github.acme/spotify-mcp' → 'spotify-mcp'); a GENERIC tail uses the vendor label from the reverse-DNS
    namespace instead ('com.notion/mcp' → 'notion', not 'mcp' — which would collide + be meaningless)."""
    parts = (registry_name or "").split("/")
    tail = re.sub(r"[^a-z0-9]+", "-", parts[-1].lower()).strip("-")
    if len(parts) == 1:
        stripped = re.sub(r"^mcp-(server-)?|-mcp(-server)?$", "", tail).strip("-")
        if stripped and stripped not in _GENERIC_TAILS:
            return _unreserved(stripped, registry_name)
    if tail and tail not in _GENERIC_TAILS:
        return _unreserved(tail, registry_name)
    namespace = parts[0] if len(parts) > 1 else ""
    labels = [l for l in re.split(r"[.\-]", namespace.lower()) if l and l not in _GENERIC_NS_LABELS]
    if labels:
        return _unreserved(labels[-1], registry_name)
    return _unreserved(tail or "server", registry_name)


def _disambiguate(name: str, registry_name: str) -> str:
    """The one way a discovered server's name is made unique: a sha256 prefix of the REGISTRY name.

    Deliberately not `hash()`, which is seed-randomised per process, so the same package got a different
    local name on every restart — a saved server that never lines up with the one just discovered. Both
    callers use this: the reserved-name guard below and the live-collision branch in `execute`, which
    kept a `hash()` of its own long after the rule was written down here."""
    return f"{name}-{hashlib.sha256((registry_name or name).encode('utf-8')).hexdigest()[:6]}"


def _unreserved(name: str, registry_name: str) -> str:
    """Never hand a discovered server a privileged name."""
    return name if name not in _reserved_names() else _disambiguate(name, registry_name)


_MATCH_STOP = {"mcp", "server", "api", "app", "the", "a", "an", "my", "control", "use", "for"}


def _match_tokens(s: str) -> set[str]:
    """Significant lowercase tokens of a name/query (drops generic filler + <3-char noise)."""
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) >= 3 and t not in _MATCH_STOP}


def _connected_match(ctx, query: str) -> str | None:
    """A connected MCP server the query clearly refers to, or None. Matches on a SHARED significant TOKEN
    (or exact name) — NOT a bare substring, so query 'git' does not wrongly hit connected 'github' and skip
    installing the thing actually asked for. Lets mcp_find activate an already-connected server instead of
    re-discovering/re-installing it."""
    server_tools = getattr(getattr(ctx, "mcp", None), "server_tools", {}) or {}
    low = (query or "").strip().lower()
    q = _match_tokens(query)
    for name, tools in server_tools.items():
        if not tools:
            continue
        if name.lower() == low or (_match_tokens(name) & q):
            return name
    return None


_NAME_CAP, _RUN_CAP, _QUOTE_CAP = 90, 200, 300
QUOTE_TITLE = "What it says about itself (their words, not ours)"


def _one_line(value: object, cap: int) -> str:
    """Third-party text as ONE bounded line: newlines and control characters become spaces, reading-order
    overrides and invisible breaks are dropped, and an over-long value is cut back to a word boundary. A
    value that cannot break a line cannot forge a field, and a bounded one cannot push a real field off
    the card.

    The bidi half is fixed HERE and not at the CLI card, because the web renders these same fields from
    the same wire and `str.split()` no more treats U+202E as whitespace than JS's `\\s` does: an RLO left
    in a name reverses the rest of the line, so a server can be published under a name that READS as our
    own trust text. `core/text_security.scrub` is where the set is chosen and why an Arabic name is
    untouched by it."""
    text = " ".join(scrub(value).split())
    if len(text) <= cap:
        return text
    cut = text[:cap]
    space = cut.rfind(" ")
    return (cut[:space] if space > cap // 2 else cut).rstrip() + "..."


def _quoted(value: object, cap: int) -> str:
    """A third-party NAME to be read inside a sentence of ours. Quotes are stripped before it is quoted,
    so the value cannot close ours and continue in our voice; no real env/header/server name has any.
    ASCII quotes, like every other character we contribute here — the CLI draws this text verbatim, and
    a card is not the place to find out the terminal cannot encode a typographic one."""
    return '"' + _one_line(value, cap).translate(str.maketrans("", "", "\"'“”")) + '"'


def _secrets_demanded(c) -> str:
    """The required secret names — bounded in length AND in count, since both come from the server."""
    names = [n for n in (_quoted(e.name, 40) for e in c.env if e.required) if n != '""']
    shown = ", ".join(names[:3])
    return f"{shown}, and {len(names) - 3} more" if len(names) > 3 else shown


def _approval_card(c, from_fallback: bool = False) -> tuple[str, dict]:
    """(text, notice) for the install card: the same facts as a flat block and as named fields.

    Every string the SERVER wrote — name, description, the env vars it demands — is hostile input, and
    this is the one card where believing it costs the user a secret. Nothing third-party is interpolated
    into a sentence of OURS, and everything third-party goes through _one_line first, so a name carrying
    newlines can no longer forge a second "Secrets needed: none" block above the real one.

    `notice` is why the demand no longer hides: the web card renders each field in its own slot, so the
    145-character headline that clipped `Secrets needed: Secr…` behind a toggle has nothing left to
    hide. `text` is the same content flattened, four lines, because the CLI card draws five."""
    demand = _secrets_demanded(c)
    alert = f"It demands a secret from you: {demand}" if demand else ""
    run = _one_line(c.cfg.get("url") or " ".join([c.cfg.get("command", "")] + list(c.cfg.get("args") or [])),
                    _RUN_CAP)
    server, source = _one_line(c.name, _NAME_CAP), _one_line(c.repo_url, _NAME_CAP)
    registry = "PyPI / npm - NOT the official MCP registry" if from_fallback else "official MCP registry"
    warn = ("NOT verified by the official MCP registry - found on PyPI/npm, where anyone may publish "
            "a look-alike. Only install it if you trust it." if from_fallback else "")
    quote = _one_line(c.description, _QUOTE_CAP)

    facts = [["Runs", run], ["Server", server], ["Registry", registry]]
    if source:
        facts.append(["Source", source])
    if not demand:
        facts.append(["Secrets", "none asked for up front"])
    notice = {
        "head": "Install a third-party MCP server?",
        "alert": alert,
        "facts": facts,
        "warn": warn,
        "quote": {"title": QUOTE_TITLE, "text": quote},
    }
    text = "\n".join([
        f"Install a third-party MCP server. {alert or 'No secret is asked for up front.'}",
        f"Runs: {run}",
        f"Server: {_quoted(c.name, _NAME_CAP)} - "
        f"{warn or 'listed in the official MCP registry'}"
        + (f" ({source})" if source else ""),
        f"{QUOTE_TITLE}: {quote}",
    ])
    return text, notice


async def execute(args: dict, ctx) -> str:
    # Both function-local for the same reason the module docstring gives for core.mcp.client: at module
    # level either one closes a cycle through tools.registry.discover(), which swallows the ImportError.
    from kotoba.core.loop import note_tool_failure
    from kotoba.core.mcp.client import MCPBusy, _AuthRequired

    if getattr(ctx, "mcp", None) is None:
        return None  # MCP unavailable in this build → graceful FAIL
    query = ((args or {}).get("query") or "").strip()
    if not query:
        return None
    sid = getattr(ctx, "session_id", None)

    # Already connected → activate, never re-card: else the model re-finds it instead of activate_tools.
    already = _connected_match(ctx, query)
    if already:
        from kotoba.core import mcp_active

        mcp_active.activate(sid, already)
        tools = (getattr(ctx.mcp, "server_tools", {}) or {}).get(already) or []
        pretty = ", ".join(t.split("__", 1)[-1] for t in tools[:8])
        return (f"I already have '{already}' connected — I just activated it, so its tools are available now"
                + (f": {pretty}." if pretty else "."))

    cands = registry_search.vet(await registry_search.search_registry(query), query)
    from_fallback = False
    if not cands:
        # Fallback packages are NOT namespace-verified; the card below is the real gate, and says so.
        cands = registry_search.vet(await registry_search.search_fallback(query), query)
        from_fallback = bool(cands)
    if not cands:
        note_tool_failure(f"no MCP server found for {query!r}")
        return (f"I looked in the MCP registry but couldn't find a server for \"{query}\". "
                "Want me to try different words?")

    c = cands[0]
    # family="": this path ignores `always`, so the card must not offer a button that does nothing.
    text, notice = _approval_card(c, from_fallback)
    verdict = await interaction.ask_approval(ctx, text, family="", notice=notice)
    if verdict != interaction.APPROVED:
        return interaction.refusal_note(verdict, f"installing '{c.name}'")

    values: dict[str, str] = {}
    for e in c.env:
        if not e.required:
            continue
        kind = "secret" if e.secret else "text"
        prompt = e.description or f"value for {e.name}"
        card: dict = {}
        typed = await interaction.request_input(
            sid, f"{c.name} needs {e.name}: {prompt}", kind, card=card)
        if not typed:
            # Nothing was installed, so nothing ran — but WHICH ending it was is the user's business:
            # a box left empty is their choice, a box that expired is not, and this recorded the
            # second whatever happened.
            verdict = card.get("verdict") or interaction.UNANSWERED
            interaction.note_no_run(ctx, verdict)
            return interaction.refusal_note(verdict, f"entering {e.name} for '{c.name}'")
        if e.secret and sid:
            ephemeral_secrets.put(sid, e.name, typed)
            values[e.name] = ephemeral_secrets.get(sid, e.name)
        else:
            values[e.name] = typed

    cfg_connect, cfg_persist = registry_search.apply_env(c, values)
    name = _local_name(c.name)
    if name in getattr(ctx.mcp, "server_tools", {}):
        name = _disambiguate(name, c.name)

    try:
        tools = await ctx.mcp.connect(name, cfg_connect)
    except _AuthRequired as auth:
        return await _handle_auth(ctx, name, c, auth)
    except MCPBusy:
        # Still queued, not empty — _finish_connect would read [] as "no usable tools" and remove it.
        # The row is still a failure: this call connected nothing, whatever the queue does next.
        note_tool_failure(f"{c.name} was still queued when the connect window ran out")
        return (f"'{c.name}' is taking longer than usual to start — something is ahead of it in the queue. "
                "Tell the user it's still coming up and try it again in a moment.")
    except Exception:
        note_tool_failure(f"{c.name} was found but would not start")
        return (f"I found '{c.name}' but couldn't get it running. "
                + (f"Want me to try the next match ({cands[1].name})?" if len(cands) > 1 else
                   "Want me to try different words?"))

    return await _finish_connect(name, tools, cfg_persist, ctx)


async def _finish_connect(name, tools, cfg_persist, ctx) -> str:
    """Verify the connect exposed tools, persist the server, and confirm. Delegates to the shared
    auth_flow.finish_connect (DRY with mcp_install)."""
    return await auth_flow.finish_connect(ctx, name, tools, cfg_persist)


async def _handle_auth(ctx, name: str, c, auth: "_AuthRequired") -> str:
    """React to a server that needs auth — delegates to the shared auth_flow.handle_auth (DRY with
    mcp_install). token → ask for a key + retry + persist; oauth → record pending + honest Settings notice."""
    return await auth_flow.handle_auth(ctx, name, c.cfg, getattr(c, "description", ""), auth)
