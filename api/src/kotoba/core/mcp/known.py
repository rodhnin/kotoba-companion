"""Curated registry of known MCP servers — so the user can install one by NAME, by voice.

"install the Google Calendar MCP" looks up a vetted spec here instead of running an arbitrary binary
the model made up: an allowlist, not a search. Aliases map common phrasings to a canonical key, and a
server needing secrets or OAuth declares `needs` so the install flow can ask rather than guess.

`oauth:` means the MCP-protocol browser sign-in and is only answerable for a server with a `url`. A
command server whose vendor has its own local OAuth declares the `env:` it really reads plus a `setup`
sentence, because "set this variable" is not the whole of what a person has to do — one shipped as
`oauth:google` on a command server, whose Sign in button could only ever answer 400."""
from __future__ import annotations
from kotoba.paths import home_dir

KNOWN_SERVERS: dict[str, dict] = {
    "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "{path}"],
        "params": {"path": "A folder the server may read/write (e.g. ~/.kotoba/work)."},
        "description": "Read/write files in a folder you choose.",
    },
    "memory": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "description": "A simple key/value knowledge graph the model can store things in.",
    },
    "google-calendar": {
        "command": "npx",
        "args": ["-y", "@cocal/google-calendar-mcp"],
        "needs": ["env:GOOGLE_OAUTH_CREDENTIALS"],
        "setup": ("Google Calendar signs you in itself rather than through Kotoba. Create an OAuth 2.0 "
                  "Desktop-app client in a Google Cloud project with the Calendar API enabled, point "
                  "GOOGLE_OAUTH_CREDENTIALS at the gcp-oauth.keys.json it gives you, then run "
                  "`npx @cocal/google-calendar-mcp auth` once on this machine to approve it in a "
                  "browser. Reconnect after that."),
        "description": "Create and read Google Calendar events.",
    },
    "github": {
        # Official HOSTED server (the npx server-github package is DEPRECATED); Bearer PAT injected by build_cfg.
        "url": "https://api.githubcopilot.com/mcp/",
        "needs": ["env:GITHUB_PERSONAL_ACCESS_TOKEN"],
        "description": "Read/write GitHub repos, issues, and PRs (official hosted GitHub MCP).",
    },
    # Remote OAuth vendors: connect preflights 401 → PENDING entry finished via Settings "Sign in".
    "notion": {
        "url": "https://mcp.notion.com/mcp",
        "needs": ["oauth:notion"],
        "description": "Read/search/create Notion pages and databases (official hosted Notion MCP, OAuth).",
    },
    "linear": {
        "url": "https://mcp.linear.app/mcp",
        "needs": ["oauth:linear"],
        "description": "Read/create Linear issues, projects, and comments (official Linear MCP, OAuth).",
    },
    "slack": {
        "url": "https://slack.com/api/mcp",
        "needs": ["oauth:slack"],
        "description": "Read/post Slack messages and search channels (official Slack MCP, OAuth).",
    },
    "browser": {
        # Pinned so `npx -y` fetches one known version, not whatever is newest mid-session (the image ships
        # no copy to reuse — the Dockerfile leaves this server out); --no-sandbox is build_cfg's call.
        "command": "npx",
        "args": ["-y", "@playwright/mcp@0.0.75", "--headless"],
        "description": "Drive a real headless browser — navigate, click, type, read pages (accessibility tree), screenshot.",
    },
}

_ALIASES = {
    "files": "filesystem",
    "file system": "filesystem",
    "filesystem": "filesystem",
    "google": "google-calendar",
    "google calendar": "google-calendar",
    "calendar": "google-calendar",
    "gcal": "google-calendar",
    "github": "github",
    "git hub": "github",
    "notion": "notion",
    "notion mcp": "notion",
    "linear": "linear",
    "slack": "slack",
    "memory": "memory",
    "browser": "browser",
    "playwright": "browser",
    "navegador": "browser",
    "web browser": "browser",
}


def resolve_known(name: str) -> tuple[str, dict] | None:
    """Map a spoken/typed name to (canonical_name, spec) if it's a known server, else None."""
    key = (name or "").strip().lower()
    canonical = _ALIASES.get(key, key)
    spec = KNOWN_SERVERS.get(canonical)
    return (canonical, spec) if spec else None


# Read only through resolve_env_need() — every reader MUST see the same list (its docstring says why).
ENV_ALIASES = {
    "GITHUB_PERSONAL_ACCESS_TOKEN": ("GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_MCP_TOKEN", "GITHUB_TOKEN"),
}


def setup_note(name: str) -> str:
    """What a person must really do before a known server can connect — empty when there is nothing to
    say beyond the variable `needs` already names.

    `needs` can name a variable and nothing else. It cannot say that the value is a path to a file
    downloaded from somebody else's console, nor that a one-off browser approval has to run beside it.
    The generic refusal ("needs an API token. Set X in the backend, then reconnect") is true of github
    and, apart from the variable name, false of google-calendar in every word."""
    found = resolve_known(name)
    return str((found[1].get("setup") or "")) if found else ""


def resolve_env_need(env_var: str) -> str:
    """Value of a declared `env:` need, accepting known aliases. Empty string if none is set. Shared by
    build_cfg (header injection) and the two connect prechecks so they never disagree."""
    import os

    for v in ENV_ALIASES.get(env_var, (env_var,)):
        val = os.getenv(v, "").strip()
        if val:
            return val
    return ""


def build_cfg(name: str) -> tuple[str, dict] | None:
    """Resolve a known server NAME to (canonical_name, connect_cfg) with `{path}` placeholders filled.
    Single source of truth for the connect cfg, shared by mcp_install (voice), /api/mcp/connect
    (Settings), and the work loop's up-front browser connect. Returns None for an unknown name."""
    import os
    from pathlib import Path

    found = resolve_known(name)
    if not found:
        return None
    canonical, spec = found
    cfg = {k: v for k, v in spec.items() if k in ("command", "args", "url", "env", "headers")}
    # `{path}` must be a dir that ACTUALLY EXISTS — server-filesystem refuses to start otherwise.
    default_path = str(Path(
        os.getenv("KOTOBA_WORKSPACE_DIR") or os.getenv("KOTOBA_FILES_DIR")
        or (home_dir() / "files")
    ).expanduser())
    if cfg.get("args") and any("{path}" in str(a) for a in cfg["args"]):
        try:
            Path(default_path).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    if cfg.get("args"):
        cfg["args"] = [a.replace("{path}", default_path) for a in cfg["args"]]
    if canonical == "github":
        # No PAT set → header left off on purpose: connect 401s → _AuthRequired("token") → masked token modal.
        pat = resolve_env_need("GITHUB_PERSONAL_ACCESS_TOKEN")
        if pat:
            cfg["headers"] = {**(cfg.get("headers") or {}), "Authorization": f"Bearer {pat}"}
    if canonical == "google-calendar":
        # The server reads these itself; the stdio child gets only the SDK's safe env subset
        # (HOME/PATH/…), so an unforwarded one is a variable the person set and the process never saw.
        # Only when really set: an empty value would point it at a file named after the variable.
        for var in ("GOOGLE_OAUTH_CREDENTIALS", "GOOGLE_CALENDAR_MCP_TOKEN_PATH"):
            val = os.getenv(var, "").strip()
            if val:
                cfg["env"] = {**(cfg.get("env") or {}), var: val}
    if canonical == "browser":
        # Prefer the user's REAL browser via CDP: a logged-in, non-headless browser passes anti-bot walls.
        cdp = os.getenv("KOTOBA_BROWSER_CDP", "").strip()
        if cdp:
            ver = next((a for a in cfg["args"] if a.startswith("@playwright/mcp")), "@playwright/mcp@0.0.75")
            cfg["args"] = ["-y", ver, "--cdp-endpoint", cdp]
        else:
            exe = os.getenv("KOTOBA_BROWSER_EXECUTABLE", "").strip()
            if exe:
                cfg["args"] = cfg["args"] + ["--executable-path", exe]
            # Only where Chromium cannot build a sandbox at all (core/pdf.no_sandbox_needed — "root" was the
            # wrong test): this browser visits unvetted pages, so the renderer sandbox earns its keep here.
            from kotoba.core.pdf import no_sandbox_needed

            if no_sandbox_needed():
                cfg["args"] = cfg["args"] + ["--no-sandbox"]
        # @playwright/mcp defaults its artifact dir to ./.playwright-mcp under cwd — that dumps snapshots into the repo.
        out_dir = os.getenv("KOTOBA_BROWSER_OUTPUT_DIR", "").strip() or str(
            home_dir() / "playwright-output"
        )
        cfg["args"] = cfg["args"] + ["--output-dir", out_dir]
    return canonical, cfg
