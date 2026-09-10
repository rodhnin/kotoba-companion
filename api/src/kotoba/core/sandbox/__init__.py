"""Sandbox selection. `KOTOBA_SANDBOX=local|docker|none` (default local).

- local:  run on the HOST — a child process in a jailed workdir, scrubbed env, timeouts.
- docker: local Docker daemon, opt-in extra isolation; workdir bind-mounted.
- none:   no execution backend, so shell/execute_code are simply not offered.

What `none` covers, exactly: no tool of HERS starts a child process on this machine — shell and
execute_code are withheld, and search_files answers from its own walk instead of spawning ripgrep.
What it does NOT cover: an MCP stdio server and the browser are separately installed and separately
approved, and they still launch when used. This setting governs the agent, not the app."""
from __future__ import annotations

from pathlib import Path

from kotoba.core.sandbox.base import ExecResult, Sandbox

__all__ = [
    "ExecResult", "Sandbox", "backend_name", "sandbox_available_sync", "create_sandbox",
]


def backend_name() -> str:
    # Read at runtime via app_settings so the Settings panel can switch local/docker/none live.
    from kotoba.core import app_settings
    return app_settings.runtime_value("sandbox", "KOTOBA_SANDBOX", "local").strip().lower()


def sandbox_available_sync() -> bool:
    """Sync variant for tool check() (registry caches it ~30s). False unless a backend is usable."""
    name = backend_name()
    if name == "local":
        return True
    if name == "docker":
        from kotoba.core.sandbox.docker import docker_available_sync

        return docker_available_sync()
    return False


def create_sandbox(workdir: str | Path) -> Sandbox | None:
    name = backend_name()
    if name == "local":
        from kotoba.core.sandbox.local import LocalSandbox

        return LocalSandbox(workdir)
    if name == "docker":
        from kotoba.core.sandbox.docker import DockerSandbox

        return DockerSandbox(workdir)
    return None
