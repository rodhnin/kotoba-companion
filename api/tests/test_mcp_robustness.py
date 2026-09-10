"""MCP robustness — six defects, one section each.

  1 — a connect timeout releases the subprocess (no orphaned task or process)
  2 — boot does not block /health on dead saved servers
  3 — shutdown/disconnect cannot hang forever
  4 — a GitHub PAT never reaches mcp.yaml
  5 — save_server failures are logged, not silently swallowed
  6 — _ensure_owner reconnects previously-connected servers after a crash
"""
from __future__ import annotations

import asyncio
import logging

import pytest


# ── helpers ────────────────────────────────────────────────────────────────────


def _mgr():
    from kotoba.core.mcp.client import MCPManager
    return MCPManager()


def _mcp_available():
    import kotoba.core.mcp.client as c
    return c._MCP_AVAILABLE


# ── Connect timeout releases subprocess ───────────────────────────────────────


def test_connect_timeout_uses_in_task_cancellation(monkeypatch):
    """asyncio.timeout (not asyncio.wait_for) is used for connect so the CancelledError fires in the
    owner task, letting anyio cancel scopes clean up the stdio subprocess. Three things are checked: the
    timeout fires, it raises to the caller, and the attempt leaves no extra asyncio task behind. The
    witness for the in-task cleanup is the slow connect's own `finally` running — inside the owner task,
    which is what anyio needs; without it the subprocess would leak."""
    import kotoba.core.mcp.client as client
    if not _mcp_available():
        pytest.skip("mcp SDK not installed")

    monkeypatch.setattr(client, "_CONNECT_TIMEOUT", 0.2)

    cleanup_called = {"v": False}

    async def _slow_connect(*a, **k):
        try:
            await asyncio.sleep(60)
        finally:
            cleanup_called["v"] = True

    async def go():
        mgr = _mgr()
        await mgr.start()
        mgr.group.connect_to_server = _slow_connect
        tasks_before = len(asyncio.all_tasks())
        with pytest.raises(Exception):
            await mgr.connect("slow", {"command": "fake"})
        tasks_after = len(asyncio.all_tasks())
        await mgr.aclose()
        return tasks_before, tasks_after, cleanup_called["v"]

    tasks_before, tasks_after, cleaned = asyncio.run(asyncio.wait_for(go(), timeout=10))
    assert tasks_after <= tasks_before, (
        f"tasks leaked: {tasks_after} after connect vs {tasks_before} before"
    )
    assert cleaned, "connect coroutine's finally did not run — subprocess would leak"


# ── Boot does not block on dead saved servers ─────────────────────────────────


def test_boot_mcp_connect_is_backgrounded(tmp_path, monkeypatch):
    """The saved-server connect loop runs in a background task, not blocking lifespan. With a slow or
    dead server, /health must become reachable before any connect timeout expires: the cap is
    `_CONNECT_TIMEOUT`, 30s by default, and the 5s asserted below is generous for lifespan overhead
    alone. The saved server here hangs on connect for far longer than either."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "b.db"))
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "s.yaml"))
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(tmp_path / "mcp.yaml"))
    monkeypatch.setenv("KOTOBA_PENDING_MCP", str(tmp_path / "pending.yaml"))
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))

    import kotoba.core.mcp.config as cfg_mod
    import kotoba.core.mcp.client as client

    cfg_mod.save_server("dead-server", {"command": "sleep", "args": ["9999"]})

    connect_started = asyncio.Event() if False else {"t": None}  # simple mutable flag

    orig_submit = client.MCPManager._submit

    async def _slow_submit(self, op, payload):
        if op == "connect":
            connect_started["t"] = asyncio.get_event_loop().time()
            await asyncio.sleep(5)
            return []
        return await orig_submit(self, op, payload)

    monkeypatch.setattr(client.MCPManager, "_submit", _slow_submit)

    import time
    from fastapi.testclient import TestClient
    import kotoba.server as main

    start = time.monotonic()
    with TestClient(main.app) as c:
        r = c.get("/health")
        elapsed = time.monotonic() - start

    assert r.status_code == 200
    assert elapsed < 5.0, f"/health took {elapsed:.1f}s — boot is blocking on MCP connect"


# ── Shutdown/disconnect cannot hang ──────────────────────────────────────────


def test_disconnect_session_does_not_hang(monkeypatch):
    """A disconnect_from_server call that stalls must be bounded, not hang the caller indefinitely.

    The inner per-call timeout is 8s and `_submit` caps the whole operation at 12s, so the disconnect
    has to come back well inside the 22s asserted at the end; the `wait_for` around it is only a safety
    net so a regression fails instead of hanging the suite. A fake session is planted first, because the
    disconnect_session path is the one under test."""
    import kotoba.core.mcp.client as client
    if not _mcp_available():
        pytest.skip("mcp SDK not installed")

    async def go():
        mgr = _mgr()
        await mgr.start()

        async def _hang(session):
            await asyncio.sleep(9999)

        mgr.group.disconnect_from_server = _hang

        mgr._session_by_name["fake"] = object()
        mgr.server_tools["fake"] = []

        import time
        t0 = time.monotonic()
        await asyncio.wait_for(mgr.disconnect("fake"), timeout=25)
        elapsed = time.monotonic() - t0

        await mgr.aclose()
        return elapsed

    elapsed = asyncio.run(go())
    assert elapsed < 22, f"disconnect hung for {elapsed:.1f}s"


def test_aclose_does_not_hang_on_wedged_child(monkeypatch):
    """aclose() has a 15s hard cap on the owner-task wait — a stdio child that ignores SIGTERM cannot
    prevent uvicorn shutdown indefinitely. The wedge is simulated by stalling the owner task after the
    group has exited, with `group` already cleared: the state a child still holding the group's
    `__aexit__` leaves behind."""
    import kotoba.core.mcp.client as client
    if not _mcp_available():
        pytest.skip("mcp SDK not installed")

    async def go():
        mgr = _mgr()
        await mgr.start()

        original_run = mgr._run

        async def _wedged_run():
            try:
                await original_run()
            finally:
                await asyncio.sleep(9999)

        mgr._owner_task.cancel()
        try:
            await mgr._owner_task
        except Exception:
            pass
        mgr._owner_task = asyncio.create_task(_wedged_run())
        mgr._ready = asyncio.Event()
        mgr._ready.set()
        mgr.group = None

        import time
        t0 = time.monotonic()
        await mgr.aclose()
        return time.monotonic() - t0

    elapsed = asyncio.run(go())
    assert elapsed < 18, f"aclose hung for {elapsed:.1f}s — 15s cap not working"


# ── GitHub PAT never reaches mcp.yaml ─────────────────────────────────────────


def test_github_pat_not_written_to_yaml(tmp_path, monkeypatch):
    """build_cfg injects the PAT into the connect cfg (needed by _params_from_cfg), but save_server must
    strip `headers` so the raw token is never persisted to the plaintext YAML. The connect cfg keeps its
    header — that is what authenticates the real connection — and the strip reproduced below is what
    mcp_connect and mcp_install do before they persist anything."""
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(tmp_path / "mcp.yaml"))
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_supersecret")

    from kotoba.core.mcp.known import build_cfg
    from kotoba.core.mcp.config import save_server, load_servers

    _, cfg = build_cfg("github")
    assert "Authorization" in cfg.get("headers", {})

    save_server("github", {k: v for k, v in cfg.items() if k != "headers"})

    saved = load_servers()
    yaml_text = (tmp_path / "mcp.yaml").read_text(encoding="utf-8")

    assert "ghp_supersecret" not in yaml_text, "PAT was written to plaintext YAML"
    assert "Authorization" not in yaml_text, "Authorization header was written to YAML"
    assert "github" in saved


def test_github_pat_rotation_takes_effect_on_next_build(monkeypatch):
    """Rotating the PAT in .env is effective on the next boot because the saved YAML has no headers and
    build_cfg re-resolves from the current env on boot (the frozen-args fix).

    The stripped cfg is what satisfies the re-resolve condition — `not _orig.get("auth_key") and not
    _orig.get("headers") and resolve_known(_name)` — and github is a remote server, so its `url` has to
    survive the strip or there would be nothing left to re-resolve against."""
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_old")
    from kotoba.core.mcp.known import build_cfg

    _, cfg1 = build_cfg("github")
    assert cfg1["headers"]["Authorization"] == "Bearer ghp_old"

    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_new")
    _, cfg2 = build_cfg("github")
    assert cfg2["headers"]["Authorization"] == "Bearer ghp_new"

    saved_clean = {k: v for k, v in cfg2.items() if k != "headers"}
    assert "headers" not in saved_clean
    assert "url" in saved_clean


# ── save_server failures are logged ───────────────────────────────────────────


def test_save_server_failure_is_logged_not_swallowed(tmp_path, monkeypatch, caplog):
    """A save_server failure (a permission error on ~/.kotoba/, say) must produce a warning log entry —
    not silently pass, which leaves the server absent on restart with no trace. Exercised through the
    auth_flow.finish_connect path."""
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(tmp_path / "mcp.yaml"))

    import kotoba.core.mcp.config as cfg_mod
    from unittest.mock import patch

    def _boom(name, data):
        raise OSError("disk full")

    with patch.object(cfg_mod, "save_server", side_effect=_boom):
        with caplog.at_level(logging.WARNING, logger="kotoba.mcp"):
            from kotoba.core.mcp import auth_flow

            class _FakeCtx:
                mcp = None
                session_id = "s"

            asyncio.run(auth_flow.finish_connect(_FakeCtx(), "testserver", ["testserver__tool"], {}))

    assert any("persist" in r.message.lower() or "failed" in r.message.lower()
               for r in caplog.records), "no warning logged when save_server fails"


def test_mcp_install_save_failure_logged(tmp_path, monkeypatch, caplog):
    """mcp_install.execute logs a warning when save_server raises — the install still succeeds for
    this session, but the operator sees a trace in the logs."""
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(tmp_path / "mcp.yaml"))

    from unittest.mock import AsyncMock, MagicMock, patch
    import kotoba.core.mcp.config as cfg_mod
    import kotoba.tools.action.mcp_install as mi

    fake_mcp = MagicMock()
    fake_mcp.server_tools = {}
    fake_mcp.connect = AsyncMock(return_value=["browser__navigate"])

    class _Ctx:
        mcp = fake_mcp
        session_id = "s"
        db = None

    with patch("kotoba.core.interaction.request_approval", new_callable=AsyncMock, return_value=(True, None)), \
         patch.object(cfg_mod, "save_server", side_effect=OSError("disk full")), \
         patch("kotoba.core.mcp_active.activate"):
        with caplog.at_level(logging.WARNING, logger="kotoba.mcp"):
            result = asyncio.run(mi.execute({"name": "browser"}, _Ctx()))

    assert "browser" in (result or ""), f"unexpected result: {result!r}"
    assert any("persist" in r.message.lower() or "failed" in r.message.lower()
               for r in caplog.records), "no warning logged when save_server fails in mcp_install"


# ── _ensure_owner reconnects after crash ─────────────────────────────────────


def test_ensure_owner_reconnects_servers_after_crash(monkeypatch):
    """After the owner task crashes, the next _ensure_owner call fires a background reconnect for every
    previously-connected server — one attempt each, never an infinite retry loop.

    A "previously connected" config is injected by hand, the crash is a cancel of the owner task, and
    the replacement connect succeeds on its first call so the run can finish."""
    import kotoba.core.mcp.client as client
    if not _mcp_available():
        pytest.skip("mcp SDK not installed")

    async def go():
        mgr = _mgr()
        await mgr.start()

        mgr._server_cfgs["myserver"] = {"command": "fake", "args": []}
        mgr.server_tools["myserver"] = ["myserver__tool"]

        attempts = []

        orig_do_connect = mgr._do_connect

        async def _counting_connect(name, cfg):
            attempts.append(name)
            mgr.server_tools[name] = [f"{name}__tool"]
            mgr._server_cfgs[name] = cfg
            return mgr.server_tools[name]

        monkeypatch.setattr(mgr, "_do_connect", _counting_connect)

        mgr._owner_task.cancel()
        try:
            await mgr._owner_task
        except Exception:
            pass

        await mgr._ensure_owner()
        await asyncio.sleep(0.3)

        await mgr.aclose()
        return attempts

    attempts = asyncio.run(asyncio.wait_for(go(), timeout=15))
    assert "myserver" in attempts, "auto-reconnect did not attempt the previously-connected server"
    assert attempts.count("myserver") == 1, f"expected 1 reconnect attempt, got {attempts.count('myserver')}"


def test_ensure_owner_no_reconnect_on_first_start(monkeypatch):
    """On the FIRST start() call (not a restart), _ensure_owner must NOT fire a background reconnect —
    there are no previously-connected servers and _server_cfgs is empty."""
    import kotoba.core.mcp.client as client
    if not _mcp_available():
        pytest.skip("mcp SDK not installed")

    reconnect_fired = {"v": False}

    async def go():
        mgr = _mgr()
        orig = mgr._reconnect_saved

        async def _spy(cfgs):
            reconnect_fired["v"] = True
            return await orig(cfgs)

        monkeypatch.setattr(mgr, "_reconnect_saved", _spy)
        await mgr.start()
        await asyncio.sleep(0.1)
        await mgr.aclose()

    asyncio.run(asyncio.wait_for(go(), timeout=10))
    assert not reconnect_fired["v"], "_reconnect_saved fired on first start — should only fire on restart"


def test_a_disconnected_server_is_not_resurrected_by_the_crash_reconnect(monkeypatch):
    """DELETE /api/mcp/{name} removes every persistent trace; the in-memory cfg outlived them all.

    `disconnect()` popped `server_tools` and `_session_by_name` and left `_server_cfgs[name]` behind —
    the one copy that still holds the live `Authorization` header. `_ensure_owner` snapshots that dict
    on any owner crash (a transport error from ANY connected server) and replays every entry through
    `_reconnect_saved`, so a server the user deleted — whose `mcp:{name}` key was wiped from the
    keystore and whose token they may have revoked — comes back connected, tools re-registered, on the
    strength of a Bearer nothing on disk remembers. `config.strip_secrets`' docstring names exactly this
    ("a stored header SHADOWS revocation"); this was the in-memory door to it. `_server_cfgs` also grew
    for the process's whole life."""
    import kotoba.core.mcp.client as client
    if not _mcp_available():
        pytest.skip("mcp SDK not installed")

    async def go():
        mgr = _mgr()
        await mgr.start()
        mgr._server_cfgs["gone"] = {"url": "https://x.test/mcp",
                                    "headers": {"Authorization": "Bearer LIVE_TOKEN"}}
        mgr.server_tools["gone"] = ["gone__tool"]

        await mgr.disconnect("gone")
        left = dict(mgr._server_cfgs)

        attempts = []

        async def _counting_connect(name, cfg):
            attempts.append(name)
            return []

        monkeypatch.setattr(mgr, "_do_connect", _counting_connect)
        mgr._owner_task.cancel()
        try:
            await mgr._owner_task
        except Exception:
            pass
        await mgr._ensure_owner()
        await asyncio.sleep(0.3)
        await mgr.aclose()
        return left, attempts

    left, attempts = asyncio.run(asyncio.wait_for(go(), timeout=15))
    assert "gone" not in left, "the deleted server's config (and its Bearer) is still in memory"
    assert "gone" not in attempts, "the deleted server was reconnected after an unrelated crash"
