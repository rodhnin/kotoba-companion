"""Discover MCP servers from the OFFICIAL registry and turn the metadata into Kotoba MCP cfgs.

Everything a third party wrote is hostile input. A package identifier becomes an argv element for
npx/uvx, so anything outside a package-name shape is rejected: a URL makes npm install a remote tarball,
and `--index-url=http://…` is a flag, not a name. Third-party JSON must never reference OUR environment
either — `${OPENAI_API_KEY}` in a url or header is expanded at connect time and shipped to whatever host
the entry names, so ${VAR} is refused before it can be persisted.

The PyPI/npm fallback is the weakest link: those indexes verify no namespace. Only names containing
'mcp' are surfaced, the approval card is the real gate, and KOTOBA_MCP_DISCOVERY_FALLBACK=0 kills it."""
from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field

import httpx

from kotoba.core.mcp.inject_scan import scan_description

log = logging.getLogger("kotoba.mcp")

REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
# Normal latency ~0.5s, but the preview-stage registry has transient slow windows — give it room.
_TIMEOUT = 20.0
_OFFICIAL_META = "io.modelcontextprotocol.registry/official"


@dataclass
class EnvVar:
    name: str
    description: str = ""
    required: bool = False
    secret: bool = False


@dataclass
class Candidate:
    name: str
    description: str
    repo_url: str
    version: str
    kind: str               # "npm" | "pypi" | "remote"
    cfg: dict               # Kotoba MCP cfg WITHOUT secrets (command/args OR url/headers)
    env: list[EnvVar] = field(default_factory=list)
    active: bool = True


def _env_from(items) -> list[EnvVar]:
    out: list[EnvVar] = []
    for e in items or []:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        out.append(EnvVar(
            name=str(e["name"]),
            description=str(e.get("description") or ""),
            required=bool(e.get("isRequired")),
            secret=bool(e.get("isSecret")),
        ))
    return out


# Both patterns are boundary defenses — read the module docstring before loosening either.
_PKG_NAME_RE = re.compile(r"^@?[A-Za-z0-9][A-Za-z0-9._@/+-]{0,200}$")
_ENV_REF_RE = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*")


def _clean_package_token(value: object) -> str | None:
    s = str(value or "").strip()
    if not s or _ENV_REF_RE.search(s) or not _PKG_NAME_RE.match(s):
        return None
    return s


def _has_env_ref(value) -> bool:
    if isinstance(value, str):
        return bool(_ENV_REF_RE.search(value))
    if isinstance(value, dict):
        return any(_has_env_ref(k) or _has_env_ref(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_env_ref(v) for v in value)
    return False


def _parse_package(pkg: dict) -> tuple[str, dict, list[EnvVar]] | None:
    """One packages[] entry → (kind, cfg, env). npm → npx; pypi → uvx. Other registry types → None."""
    if not isinstance(pkg, dict):
        return None
    rtype = (pkg.get("registryType") or "").lower()
    identifier = _clean_package_token(pkg.get("identifier"))
    if identifier is None:
        return None
    version = _clean_package_token(pkg.get("version")) if pkg.get("version") else None
    spec = f"{identifier}@{version}" if version else identifier
    env = _env_from(pkg.get("environmentVariables"))
    if any(_has_env_ref(e.name) for e in env):
        return None
    if rtype == "npm":
        return "npm", {"command": "npx", "args": ["-y", spec]}, env
    if rtype == "pypi":
        return "pypi", {"command": "uvx", "args": [identifier]}, env
    return None


def _parse_remote(remote: dict) -> tuple[str, dict, list[EnvVar]] | None:
    """One remotes[] entry (streamable-http | sse) → ('remote', cfg{url, headers?}, env). Header values
    carry a `{token}` template for secrets; we keep the template in cfg and record an EnvVar so the secret
    is collected and substituted at connect time (never baked into the persisted cfg)."""
    if not isinstance(remote, dict):
        return None
    if (remote.get("type") or "").lower() not in ("streamable-http", "sse"):
        return None
    url = str(remote.get("url") or "")
    # https only: the freshly typed secret rides in a header on the very first connect.
    if not url or not url.lower().startswith("https://") or _has_env_ref(url):
        return None
    cfg: dict = {"url": url}
    env: list[EnvVar] = []
    headers: dict = {}
    for h in remote.get("headers") or []:
        if not isinstance(h, dict) or not h.get("name"):
            continue
        name, value = str(h["name"]), str(h.get("value") or "")
        if _has_env_ref(name) or _has_env_ref(value):
            return None
        headers[name] = value
        if h.get("isSecret") or "{" in value:
            # Only a header carrying a {template} has somewhere to PUT the secret — without one the typed value is dropped.
            if "{" not in value:
                continue
            env.append(EnvVar(name=name, description=str(h.get("description") or ""),
                              required=bool(h.get("isRequired", True)), secret=True))
    if headers:
        cfg["headers"] = headers
    return "remote", cfg, env


def _server_to_candidate(item: dict) -> Candidate | None:
    """Registry list item → Candidate, or None if nothing installable. Prefers a package (local stdio) over
    a remote when both exist (simpler + no third-party proxy)."""
    srv = (item or {}).get("server") or {}
    name = srv.get("name")
    if not name:
        return None
    meta = ((item.get("_meta") or {}).get(_OFFICIAL_META)) or {}
    active = (meta.get("status") == "active") and bool(meta.get("isLatest", True))
    repo = (srv.get("repository") or {}).get("url") or ""
    desc = srv.get("description") or ""
    version = srv.get("version") or ""

    for pkg in srv.get("packages") or []:
        parsed = _parse_package(pkg)
        if parsed:
            kind, cfg, env = parsed
            return Candidate(name=name, description=desc, repo_url=repo, version=version,
                             kind=kind, cfg=cfg, env=env, active=active)
    for remote in srv.get("remotes") or []:
        parsed = _parse_remote(remote)
        if parsed:
            kind, cfg, env = parsed
            return Candidate(name=name, description=desc, repo_url=repo, version=version,
                             kind=kind, cfg=cfg, env=env, active=active)
    return None


# A long multi-word query makes the registry endpoint slow enough to ReadTimeout.
_STOP = {
    "mcp", "server", "servers", "control", "controls", "controlling", "please", "app", "application",
    "tool", "tools", "find", "search", "install", "installer", "get", "use", "using", "client",
    "the", "a", "an", "for", "to", "me", "my", "of", "with", "that", "this", "some", "any", "thing",
}


def _normalize_query(q: str) -> str:
    """Reduce a spoken request to the capability keyword(s) the registry searches best on. Strips generic
    filler ('control', 'mcp', 'server', …). If everything is filler, keep the original words."""
    words = re.findall(r"[a-z0-9]+", (q or "").lower())
    kept = [w for w in words if w not in _STOP]
    return " ".join(kept) if kept else " ".join(words)


async def _search_once(q: str, limit: int) -> list[Candidate]:
    """One GET + parse. Any network/parse error → [] (so a slow query degrades to 'not found', not a crash)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{REGISTRY_BASE}/v0/servers", params={"search": q, "limit": limit})
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        log.warning("MCP registry search failed for %r", q, exc_info=True)
        return []
    out: list[Candidate] = []
    for item in (data or {}).get("servers") or []:
        try:
            c = _server_to_candidate(item)
        except Exception:
            c = None
        if c:
            out.append(c)
    return out


async def search_registry(query: str, limit: int = 5) -> list[Candidate]:
    """Find installable MCP servers for a capability. Normalizes the query to a keyword first; if a
    multi-word query finds nothing (or timed out), retries ONCE with the single most distinctive token —
    the registry's search is fast + accurate on a keyword but chokes on a full sentence."""
    q = _normalize_query(query)
    if not q:
        return []
    cands = await _search_once(q, limit)
    if not cands:
        tokens = q.split()
        if len(tokens) > 1:
            key = max(tokens, key=len)
            cands = await _search_once(key, limit)
    return cands


PYPI_JSON = "https://pypi.org/pypi/{name}/json"
NPM_SEARCH = "https://registry.npmjs.org/-/v1/search"
_FALLBACK_TIMEOUT = 10.0


def fallback_enabled() -> bool:
    import os
    return os.getenv("KOTOBA_MCP_DISCOVERY_FALLBACK", "1").strip().lower() not in ("0", "false", "no")


def _looks_like_mcp(name: str) -> bool:
    return "mcp" in (name or "").lower()


def _pypi_probe_names(q: str) -> list[str]:
    """Candidate PyPI package names by MCP naming convention (PyPI has no search API). Order = most likely
    first. e.g. q='blender' → ['blender-mcp','mcp-blender','mcp-server-blender','blender-mcp-server']."""
    key = max(q.split(), key=len) if q.split() else q
    key = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")
    if not key:
        return []
    return [f"{key}-mcp", f"mcp-{key}", f"mcp-server-{key}", f"{key}-mcp-server"]


async def search_pypi(query: str, limit: int = 4) -> list[Candidate]:
    """Probe PyPI for an MCP server by naming convention (no search API exists). Each hit → a stdio Candidate
    run with `uvx <pkg>`. Only convention names are probed, so we never install an arbitrary guessed package."""
    q = _normalize_query(query)
    if not q:
        return []
    out: list[Candidate] = []
    try:
        async with httpx.AsyncClient(timeout=_FALLBACK_TIMEOUT) as client:
            for name in _pypi_probe_names(q):
                if len(out) >= limit:
                    break
                try:
                    r = await client.get(PYPI_JSON.format(name=name))
                    if r.status_code != 200:
                        continue
                    info = (r.json() or {}).get("info") or {}
                except Exception:
                    continue
                pkg = info.get("name") or name
                if not _looks_like_mcp(pkg):
                    continue
                repo = f"https://pypi.org/project/{pkg}/"
                out.append(Candidate(name=pkg, description=info.get("summary") or "",
                                     repo_url=repo, version=info.get("version") or "",
                                     kind="pypi", cfg={"command": "uvx", "args": [pkg]}))
    except Exception:
        log.warning("PyPI MCP probe failed for %r", query, exc_info=True)
    return out


async def search_npm(query: str, limit: int = 4) -> list[Candidate]:
    """Search the npm registry for MCP servers. Each hit → a stdio Candidate run with `npx -y <pkg>`. Only
    packages whose NAME contains 'mcp' are kept (the convention), so a generic 'blender' npm lib isn't run."""
    q = _normalize_query(query)
    if not q:
        return []
    out: list[Candidate] = []
    try:
        async with httpx.AsyncClient(timeout=_FALLBACK_TIMEOUT) as client:
            r = await client.get(NPM_SEARCH, params={"text": f"{q} mcp", "size": limit * 3})
            r.raise_for_status()
            objs = (r.json() or {}).get("objects") or []
    except Exception:
        log.warning("npm MCP search failed for %r", query, exc_info=True)
        return []
    for o in objs:
        if len(out) >= limit:
            break
        pkg = ((o or {}).get("package") or {})
        name = pkg.get("name") or ""
        if not name or not _looks_like_mcp(name):
            continue
        repo = ((pkg.get("links") or {}).get("repository")) or ((pkg.get("links") or {}).get("npm")) or f"https://www.npmjs.com/package/{name}"
        out.append(Candidate(name=name, description=pkg.get("description") or "",
                            repo_url=repo, version=pkg.get("version") or "",
                            kind="npm", cfg={"command": "npx", "args": ["-y", name]}))
    return out


async def search_fallback(query: str, limit: int = 4) -> list[Candidate]:
    """PyPI + npm discovery for when the official registry has nothing. PyPI first (the canonical home for
    many Python MCP servers, e.g. blender-mcp, run via uvx), then npm; de-duped by package name."""
    if not fallback_enabled():
        return []
    pypi = await search_pypi(query, limit)
    npm = await search_npm(query, limit)
    out: list[Candidate] = []
    seen: set[str] = set()
    for c in pypi + npm:
        if c.name.lower() in seen:
            continue
        seen.add(c.name.lower())
        out.append(c)
    return out


# Stripped from a reverse-DNS namespace / hostname so the remaining label IS the vendor (notion, smithery).
_GENERIC_LABELS = {
    "com", "io", "ai", "app", "org", "net", "dev", "co", "uk", "us", "sh", "xyz", "cloud", "inc",
    "www", "mcp", "server", "servers", "api", "hosted", "remote", "modelcontextprotocol",
    "github", "gitlab", "githubusercontent", "vercel", "fly", "herokuapp",
}


def _owner_labels(c: Candidate) -> set[str]:
    """The vendor/owner identity tokens of a candidate: the labels of its reverse-DNS NAMESPACE (the part
    before '/', e.g. 'com.notion' → notion; 'ai.smithery' → smithery; 'io.github.acme' → acme) plus the
    labels of its remote URL host ('mcp.notion.com' → notion). We deliberately do NOT use the server-name
    TAIL ('ai.smithery/notion' → would falsely match 'notion'): the registry verifies NAMESPACE ownership
    (reverse-DNS / domain), so the namespace+host is the trustworthy first-party signal — not a product name a
    proxy chose. Generic labels (TLDs, 'github', 'mcp', …) are dropped so only the real vendor label remains."""
    from urllib.parse import urlparse

    labels: set[str] = set()
    namespace = (c.name or "").split("/", 1)[0]
    for part in namespace.replace("-", ".").split("."):
        if part and part.lower() not in _GENERIC_LABELS:
            labels.add(part.lower())
    url = (c.cfg or {}).get("url")
    if url:
        host = (urlparse(url).hostname or "").lower()
        for part in host.split("."):
            if part and part not in _GENERIC_LABELS:
                labels.add(part)
    return labels


def _first_party_rank(c: Candidate, query_tokens: set[str]) -> int:
    """0 if this candidate is the FIRST-PARTY server for the queried vendor (a query token matches one of its
    owner labels), else 1. With no query tokens → always 1 (neutral, no reordering)."""
    if not query_tokens:
        return 1
    return 0 if (query_tokens & _owner_labels(c)) else 1


def vet(cands: list[Candidate], query: str | None = None) -> list[Candidate]:
    """Keep only injection-clean candidates, then order by (1) FIRST-PARTY for the query, (2) active.
    A stable sort preserves the registry's relevance order within each bucket.

    First-party first because the registry verifies namespace ownership by reverse-DNS: asked for
    'Notion' we elevate the candidate whose namespace IS notion over a proxy that merely embeds the
    word, or the proxy ranks first and an OAuth vendor never reaches its real sign-in flow.

    We still do NOT prefer local over remote — that once promoted an unrelated 'indian-railways' npm
    over the official Railway server. With no query the first-party key is neutral."""
    clean = [c for c in cands if scan_description(f"{c.name} {c.description}") is None]
    tokens = set(_normalize_query(query).split()) if query else set()
    return sorted(clean, key=lambda c: (_first_party_rank(c, tokens), 0 if c.active else 1))


def apply_env(c: Candidate, values: dict[str, str]) -> tuple[dict, dict]:
    """Produce (cfg_connect, cfg_persist). cfg_connect carries REAL secret values (for connect() only);
    cfg_persist has secrets stripped (for save_server). `values` maps EnvVar.name → real value."""
    connect = copy.deepcopy(c.cfg)
    persist = copy.deepcopy(c.cfg)
    secret_names = {e.name for e in c.env if e.secret}
    if c.kind == "remote":
        # persist keeps the {token} TEMPLATE — a real secret is never saved.
        for hname, tmpl in (connect.get("headers") or {}).items():
            if hname in values:
                # LITERAL substitution: a replacement string would interpret escapes and mangle a password with a backslash.
                connect["headers"][hname] = re.sub(
                    r"\{[^}]+\}", lambda _m, _v=values[hname]: _v, tmpl
                )
        return connect, persist
    if values:
        connect["env"] = dict(values)
        persist_env = {k: v for k, v in values.items() if k not in secret_names}
        if persist_env:
            persist["env"] = persist_env
    return connect, persist
