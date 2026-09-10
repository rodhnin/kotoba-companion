"""`mcp_find`: search a registry, ask before installing, connect, prove it works, then persist.

The LOCAL NAME a discovered server gets is the subject of several tests here, because that name is
not cosmetic: it becomes the `mcp:<name>` keystore key and it is what the `{{secret:…}}` allowlist
and `always_active()` are keyed on. The other half is the auth retry for a
remote server, whose token must reach the credential store without ever reaching the saved config or
the spoken reply.
"""
from __future__ import annotations

import asyncio

import kotoba.core.mcp.registry_search as rs
import kotoba.tools.action.mcp_find as mf


class _MCP:
    def __init__(self, tools=None, raise_on_connect=False):
        self.server_tools = {}
        self._tools = tools or []
        self._raise = raise_on_connect
        self.connected = []
        self.disconnected = []

    async def connect(self, name, cfg):
        if self._raise:
            raise RuntimeError("spawn failed")
        self.connected.append((name, cfg))
        self.server_tools[name] = self._tools
        return self._tools

    async def disconnect(self, name):
        self.disconnected.append(name)
        self.server_tools.pop(name, None)


class _DB:
    def __init__(self):
        self.keys = {}

    async def save_key(self, name, value):
        self.keys[name] = value


class _Ctx:
    def __init__(self, mcp, db=None):
        self.mcp = mcp
        self.session_id = "s1"
        self.db = db or _DB()


class _MCPAuth:
    """Raises _AuthRequired on the FIRST connect (needs auth), then succeeds on the retry (with token)."""

    def __init__(self, kind, retry_tools=None):
        self.server_tools = {}
        self.calls = []
        self.kind = kind
        self._retry_tools = retry_tools or ["notion__read", "notion__search"]

    async def connect(self, name, cfg):
        from kotoba.core.mcp.client import _AuthRequired
        self.calls.append((name, cfg))
        if len(self.calls) == 1:
            raise _AuthRequired(name, "needs authentication", self.kind, cfg)
        self.server_tools[name] = self._retry_tools
        return self._retry_tools

    async def disconnect(self, name):
        self.server_tools.pop(name, None)


def _remote_cand():
    return rs.Candidate("ai.x/notion", "Read Notion pages.", "https://gh/n", "1.0",
                        "remote", {"url": "https://notion/mcp"})


def _cand(env=None):
    return rs.Candidate("io.github.acme/spotify-mcp", "Control Spotify.", "https://gh/x", "1.0",
                        "npm", {"command": "npx", "args": ["-y", "spotify-mcp@1.0"]}, env=env or [])


def _async(value):
    """A fresh already-resolved coroutine, for monkeypatched async functions."""
    async def _c(*a, **k):
        return value
    return _c()


def test_local_name_uses_vendor_when_tail_is_generic():
    """A reverse-DNS registry name like `com.railway/mcp` has a generic tail: the vendor lives in the
    namespace. Naming that server `mcp` collides with every other package spelled the same way, so the
    name comes from the namespace instead. A tail that means something is kept as it is."""
    assert mf._local_name("com.railway/mcp") == "railway"
    assert mf._local_name("ai.acme/server") == "acme"
    assert mf._local_name("io.github.acme/spotify-mcp") == "spotify-mcp"
    assert mf._local_name("ai.smithery/smithery-notion") == "smithery-notion"


def test_a_discovered_server_cannot_claim_a_privileged_name():
    """A local server name is not cosmetic: it becomes the `mcp:<name>` keystore key and it is what the
    {{secret:…}} allowlist and always_active() are keyed on. So a DISCOVERED server named `browser` would
    be offered every work turn and handed the user's one-time password, and one named after a curated
    server would inherit that server's stored token. Suffix instead — deterministically, so the same
    package keeps the same name across restarts.

    Curated server names are reserved for the same keystore reason, the suffix is stable rather than
    per-process, and an ordinary vendor name is left untouched."""
    for hostile in ("browser-mcp", "mcp-server-browser", "mcp-browser"):
        got = mf._local_name(hostile)
        assert got != "browser", hostile
        assert got.startswith("browser-") and len(got) > len("browser-"), got
    assert mf._local_name("com.notion/mcp").startswith("notion-")
    assert mf._local_name("browser-mcp") == mf._local_name("browser-mcp")
    assert mf._local_name("com.railway/mcp") == "railway"


def test_bare_package_names_strip_the_mcp_affix():
    """Bare PyPI/npm names (fallback discovery) lose the MCP convention affix and keep the bare vendor.
    A reserved vendor — `notion` is one of the curated servers — still gets suffixed, which is the point
    of the test above."""
    assert mf._local_name("blender-mcp") == "blender"
    assert mf._local_name("mcp-server-blender") == "blender"
    assert mf._local_name("mcp-notion").startswith("notion-")


def test_happy_path_search_approve_connect_test_save(monkeypatch):
    """The whole path in one call: search, ask, connect, prove the server answers, persist.

    The config is saved only after the connection produced real tools, and the confirmation quotes
    those tool names back."""
    saved = {}
    monkeypatch.setattr(mf.registry_search, "search_registry", lambda q, limit=5: _async([_cand()]))
    monkeypatch.setattr(mf.registry_search, "vet", lambda cs, *a, **k: cs)
    monkeypatch.setattr(mf.interaction, "request_approval", lambda sid, action, **k: _async((True, False)))
    monkeypatch.setattr(mf.config, "save_server", lambda name, cfg: saved.update({name: cfg}))

    mcp = _MCP(tools=["spotify-mcp__play", "spotify-mcp__pause"])
    out = asyncio.run(mf.execute({"query": "control spotify"}, _Ctx(mcp)))

    assert mcp.connected and mcp.connected[0][0] == "spotify-mcp"
    assert "spotify-mcp" in saved
    assert "play" in out and "pause" in out
    assert "connected" in out.lower()


def test_approval_denied_no_connect_no_save(monkeypatch):
    """A refused card installs nothing and saves nothing, and the refusal is reported as one.

    An asker with no verdict of its own returns `(False, False)`, which reads as "they pressed No"."""
    saved = {}
    monkeypatch.setattr(mf.registry_search, "search_registry", lambda q, limit=5: _async([_cand()]))
    monkeypatch.setattr(mf.registry_search, "vet", lambda cs, *a, **k: cs)
    monkeypatch.setattr(mf.interaction, "request_approval", lambda sid, action, **k: _async((False, False)))
    monkeypatch.setattr(mf.config, "save_server", lambda name, cfg: saved.update({name: cfg}))
    mcp = _MCP(tools=["x__a"])
    out = asyncio.run(mf.execute({"query": "q"}, _Ctx(mcp)))
    assert not mcp.connected and not saved
    assert "said NO" in out and "did not happen" in out


def test_no_candidates(monkeypatch):
    monkeypatch.setattr(mf.registry_search, "search_registry", lambda q, limit=5: _async([]))
    monkeypatch.setattr(mf.registry_search, "search_fallback", lambda q, limit=4: _async([]))
    monkeypatch.setattr(mf.registry_search, "vet", lambda cs, *a, **k: cs)
    mcp = _MCP()
    out = asyncio.run(mf.execute({"query": "nope"}, _Ctx(mcp)))
    assert not mcp.connected and "couldn't find" in out.lower()


def test_already_connected_activates_without_reinstalling(monkeypatch):
    """A server that is already connected is ACTIVATED, not installed a second time.

    In a new work session a connected server starts deferred, so the model called `mcp_find` for it
    again and got a second install card for something already installed. The connected server is
    detected first now: no re-search, no approval card."""
    searched, approvals = [], []
    monkeypatch.setattr(mf.registry_search, "search_registry",
                        lambda q, limit=5: (searched.append(q), _async([]))[1])
    monkeypatch.setattr(mf.registry_search, "search_fallback",
                        lambda q, limit=4: (searched.append(("fb", q)), _async([]))[1])
    monkeypatch.setattr(mf.interaction, "request_approval",
                        lambda sid, a, **k: (approvals.append(a), _async((True, False)))[1])

    mcp = _MCP()
    mcp.server_tools = {"blender": ["blender__get_scene_info", "blender__execute_blender_code"]}
    out = asyncio.run(mf.execute({"query": "blender"}, _Ctx(mcp)))

    assert searched == [] and approvals == []
    assert "blender" in out.lower() and ("already" in out.lower() or "activ" in out.lower())


def test_connected_match_no_substring_false_positive(monkeypatch):
    """Token match, not substring: `git` must not short-circuit to a connected `github`.

    A substring hit would skip installing the thing that was actually asked for. An exact name, and a
    name inside a phrase, still match."""
    mcp = _MCP()
    mcp.server_tools = {"github": ["github__x"]}
    assert mf._connected_match(_Ctx(mcp), "git") is None
    assert mf._connected_match(_Ctx(mcp), "github") == "github"
    assert mf._connected_match(_Ctx(mcp), "open a github issue") == "github"
    assert mf._connected_match(_Ctx(mcp), "notion") is None


def test_falls_back_to_pypi_when_official_registry_empty(monkeypatch):
    """When the official registry has nothing, discovery falls back to PyPI/npm.

    Some servers are published only there. The package is installed through `uvx`/`npx` and lands under
    the bare vendor name with the MCP affix stripped, then is persisted and confirmed."""
    saved = {}
    monkeypatch.setattr(mf.registry_search, "search_registry", lambda q, limit=5: _async([]))
    pkg = rs.Candidate("blender-mcp", "Blender integration through MCP.", "https://pypi.org/project/blender-mcp/",
                       "1.6.4", "pypi", {"command": "uvx", "args": ["blender-mcp"]})
    monkeypatch.setattr(mf.registry_search, "search_fallback", lambda q, limit=4: _async([pkg]))
    monkeypatch.setattr(mf.interaction, "request_approval", lambda sid, action, **k: _async((True, False)))
    monkeypatch.setattr(mf.config, "save_server", lambda name, cfg: saved.update({name: cfg}))

    mcp = _MCP(tools=["blender-mcp__get_scene_info", "blender-mcp__create_object"])
    out = asyncio.run(mf.execute({"query": "blender"}, _Ctx(mcp)))

    assert mcp.connected and mcp.connected[0][0] == "blender"
    assert mcp.connected[0][1] == {"command": "uvx", "args": ["blender-mcp"]}
    assert "blender" in saved
    assert "connected" in out.lower()


def test_connect_failure_offers_next(monkeypatch):
    """A spawn that fails offers the next match instead of reporting a success that did not happen."""
    monkeypatch.setattr(mf.registry_search, "search_registry",
                        lambda q, limit=5: _async([_cand(), rs.Candidate(
                            "x/second", "d", "", "1", "npm", {"command": "npx", "args": []})]))
    monkeypatch.setattr(mf.registry_search, "vet", lambda cs, *a, **k: cs)
    monkeypatch.setattr(mf.interaction, "request_approval", lambda sid, action, **k: _async((True, False)))
    mcp = _MCP(raise_on_connect=True)
    out = asyncio.run(mf.execute({"query": "q"}, _Ctx(mcp)))
    assert "x/second" in out


def test_zero_tools_disconnects_and_no_save(monkeypatch):
    """A server that connects but exposes no tools is disconnected again and never persisted."""
    saved = {}
    monkeypatch.setattr(mf.registry_search, "search_registry", lambda q, limit=5: _async([_cand()]))
    monkeypatch.setattr(mf.registry_search, "vet", lambda cs, *a, **k: cs)
    monkeypatch.setattr(mf.interaction, "request_approval", lambda sid, action, **k: _async((True, False)))
    monkeypatch.setattr(mf.config, "save_server", lambda name, cfg: saved.update({name: cfg}))
    mcp = _MCP(tools=[])
    out = asyncio.run(mf.execute({"query": "q"}, _Ctx(mcp)))
    assert mcp.disconnected and not saved and "didn't offer" in out.lower()


def test_secret_collected_via_masked_box_not_in_persist(monkeypatch):
    """A required secret is typed into a masked box: it reaches the connection, never the saved config."""
    saved = {}
    cand = _cand(env=[rs.EnvVar("API_TOKEN", required=True, secret=True)])
    monkeypatch.setattr(mf.registry_search, "search_registry", lambda q, limit=5: _async([cand]))
    monkeypatch.setattr(mf.registry_search, "vet", lambda cs, *a, **k: cs)
    monkeypatch.setattr(mf.interaction, "request_approval", lambda sid, action, **k: _async((True, False)))

    async def _typed(sid, prompt, kind, **kw):
        return "supersecret"
    monkeypatch.setattr(mf.interaction, "request_input", _typed)
    monkeypatch.setattr(mf.config, "save_server", lambda name, cfg: saved.update({name: cfg}))
    mcp = _MCP(tools=["spotify-mcp__play"])
    asyncio.run(mf.execute({"query": "q"}, _Ctx(mcp)))
    name, cfg = mcp.connected[0]
    assert cfg["env"]["API_TOKEN"] == "supersecret"
    assert "supersecret" not in str(saved)


def test_mcp_find_registered_work_only():
    """`mcp_find` is a work-mode tool and is never offered in the casual voice turn."""
    import kotoba.tools as tools  # imported for its side effect: discover()
    from kotoba.tools.registry import schemas_for

    def names(mode):
        return {(t.get("name") or t.get("type")) for t in schemas_for(mode, None, None)}

    assert "mcp_find" in names("work")
    assert "mcp_find" not in names("companion")


def test_work_subsystem_mentions_mcp_find():
    from kotoba.core.work_runner import _WORK_SUBSYSTEM
    assert "mcp_find" in _WORK_SUBSYSTEM


def test_auth_token_asked_retried_saved_and_pending_cleared(monkeypatch):
    """A remote server that 401s raises `_AuthRequired(token)`: an API key is asked for, the connection
    is retried with a Bearer header, the token is persisted in the credential store, and the server is
    saved with an `auth_key` POINTER rather than the token itself. Pending is cleared, and the token
    appears neither in the saved config nor in the spoken result.

    `notion` is a curated name, so the DISCOVERED server gets a suffix — see the reserved-name test."""
    saved, cleared, recorded = {}, [], []
    monkeypatch.setattr(mf.registry_search, "search_registry", lambda q, limit=5: _async([_remote_cand()]))
    monkeypatch.setattr(mf.registry_search, "vet", lambda cs, *a, **k: cs)
    monkeypatch.setattr(mf.interaction, "request_approval", lambda sid, a, **k: _async((True, False)))

    async def _typed(sid, prompt, kind, **kw):
        return "tok-123"
    monkeypatch.setattr(mf.interaction, "request_input", _typed)
    monkeypatch.setattr(mf.config, "save_server", lambda n, c: saved.update({n: c}))
    monkeypatch.setattr(mf.pending, "record", lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(mf.pending, "clear", lambda n: cleared.append(n))

    mcp = _MCPAuth("token")
    ctx = _Ctx(mcp)
    out = asyncio.run(mf.execute({"query": "Notion"}, ctx))

    assert len(mcp.calls) == 2, "initial call plus the retry"
    _, retry_cfg = mcp.calls[1]
    assert retry_cfg["headers"]["Authorization"] == "Bearer tok-123"
    server = mcp.calls[1][0]
    assert server.startswith("notion-"), server
    keystore_key = f"mcp:{server}"
    assert ctx.db.keys.get(keystore_key) == "tok-123"
    assert server in saved and saved[server].get("auth_key") == keystore_key
    assert "tok-123" not in str(saved)
    assert cleared == [server] and not recorded
    assert "tok-123" not in out and "read" in out


def test_auth_token_skipped_records_pending(monkeypatch):
    """Skipping the token records the server as pending, for Settings to finish, and says so. Nothing
    connects."""
    recorded = []
    monkeypatch.setattr(mf.registry_search, "search_registry", lambda q, limit=5: _async([_remote_cand()]))
    monkeypatch.setattr(mf.registry_search, "vet", lambda cs, *a, **k: cs)
    monkeypatch.setattr(mf.interaction, "request_approval", lambda sid, a, **k: _async((True, False)))

    async def _empty(sid, prompt, kind, **kw):
        return ""
    monkeypatch.setattr(mf.interaction, "request_input", _empty)
    monkeypatch.setattr(mf.pending, "record", lambda name, cfg, reason, kind, desc="": recorded.append((name, kind)))

    out = asyncio.run(mf.execute({"query": "Notion"}, _Ctx(_MCPAuth("token"))))
    assert any(n.startswith("notion") and k == "token" for n, k in recorded), recorded
    assert "settings" in out.lower()


def test_auth_oauth_records_pending_without_asking_token(monkeypatch):
    """An OAuth-redirect server can't be fixed with a pasted token: record pending(oauth) and explain —
    do NOT ask for a token."""
    recorded, asked = [], []
    monkeypatch.setattr(mf.registry_search, "search_registry", lambda q, limit=5: _async([_remote_cand()]))
    monkeypatch.setattr(mf.registry_search, "vet", lambda cs, *a, **k: cs)
    monkeypatch.setattr(mf.interaction, "request_approval", lambda sid, a, **k: _async((True, False)))

    async def _typed(sid, prompt, kind, **kw):
        asked.append(prompt)
        return "should-not-be-asked"
    monkeypatch.setattr(mf.interaction, "request_input", _typed)
    monkeypatch.setattr(mf.pending, "record", lambda name, cfg, reason, kind, desc="": recorded.append((name, kind)))

    out = asyncio.run(mf.execute({"query": "Railway"}, _Ctx(_MCPAuth("oauth"))))
    assert any(n.startswith("notion") and k == "oauth" for n, k in recorded), recorded
    assert asked == []
    assert "settings" in out.lower() or "sign-in" in out.lower() or "sign in" in out.lower()
