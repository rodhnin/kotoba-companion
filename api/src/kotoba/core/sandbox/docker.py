"""Docker sandbox backend — ephemeral, network-off, resource-limited container.

A long-lived container (`sleep infinity`) is started per session with the jailed workdir bind-mounted at
/work; commands run via `docker exec`. Hardening:
  --network none                       no outbound network
  --read-only + tmpfs /tmp             only /work and a small /tmp are writable
  --memory / --pids-limit              resource caps
  --cap-drop ALL + no-new-privileges   drop capabilities, block setuid escalation
  --user <server uid:gid>              the container runs as the server's own user
Host env, and thus secrets, is NEVER forwarded: `docker exec` uses the image env only."""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from kotoba.core.path_security import validate_within_dir
from kotoba.core.sandbox.base import ExecResult

_IMAGE = os.getenv("KOTOBA_SANDBOX_IMAGE", "python:3.12-slim")


async def _run_cli(args: list[str], timeout: float = 30.0) -> ExecResult:
    """Run a `docker ...` command on the host, capturing output. Used for lifecycle + exec.

    Cancellation (a barge-in over a long `docker exec`) kills the host-side docker client and
    re-raises, mirroring LocalSandbox.run — without this the client process outlived the cancelled
    turn. The in-container command may keep running until the container itself is removed at session
    teardown (`kill()` → `docker rm -f`); the container is the isolation boundary that makes that
    acceptable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        # No docker binary at all: answer with a result instead of raising, so a machine without
        # Docker can still import and use every other backend.
        return ExecResult("", "docker is not installed", 127)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ExecResult("", f"timed out after {timeout}s", 124)
    except asyncio.CancelledError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        raise
    return ExecResult(out.decode(errors="replace"), err.decode(errors="replace"), proc.returncode or 0)


async def docker_available() -> bool:
    """True if the Docker daemon is reachable (async). NO PRODUCTION CALLER — the runtime probe is
    docker_available_sync below."""
    res = await _run_cli(["docker", "version", "--format", "{{.Server.Version}}"], timeout=8.0)
    return res.ok and bool(res.stdout.strip())


def _docker_endpoint_exists() -> bool:
    """Cheap pre-check: is there anywhere for a daemon to be? No subprocess, no syscall that can hang."""
    import os
    from pathlib import Path

    if os.getenv("DOCKER_HOST"):
        return True
    return any(Path(p).exists() for p in ("/var/run/docker.sock", "/run/docker.sock"))


def docker_available_sync() -> bool:
    """Synchronous daemon probe for tool check() (the registry caches the result ~30s).

    Called from schemas_for, which runs inside the event loop on every iteration — so this must not be a
    long blocking call. With an 8s timeout and a wedged daemon it stalled the loop (twice, once per cache
    key), freezing SSE and narration for EVERY session. The socket pre-check answers the common "no docker"
    case instantly, and the CLI probe that remains is bounded tightly."""
    if not _docker_endpoint_exists():
        return False
    try:
        r = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, timeout=2,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def _user_flags() -> list[str]:
    """Keeps container writes owned by the caller — where a caller HAS a numeric identity. Windows has
    neither attribute and Docker Desktop maps ownership itself, so asking there raised AttributeError
    out of start() and the backend could not run at all."""
    try:
        return ["--user", f"{os.getuid()}:{os.getgid()}"]
    except AttributeError:
        return []


class DockerSandbox:
    """One isolated container per Task. Workdir is a host dir jailed to itself."""

    def __init__(self, workdir: str | Path, image: str = _IMAGE) -> None:
        self.workdir = Path(workdir).expanduser().resolve()
        self.image = image
        self.container_id: str | None = None

    async def start(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        res = await _run_cli([
            "docker", "run", "-d", "--rm",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,size=64m,mode=1777",
            "--memory", "512m",
            "--pids-limit", "256",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            *_user_flags(),
            "-v", f"{self.workdir}:/work",
            "-w", "/work",
            self.image,
            "sleep", "infinity",
        ], timeout=120.0)  # first run may pull the image
        if not res.ok:
            raise RuntimeError(f"failed to start sandbox container: {res.stderr.strip()}")
        self.container_id = res.stdout.strip()

    def _require_started(self) -> str:
        if not self.container_id:
            raise RuntimeError("sandbox not started")
        return self.container_id

    async def run(self, command: str, cwd: str = ".", timeout: int = 60) -> ExecResult:
        cid = self._require_started()
        # Stripping the leading '/' is what stops a host path being handed straight to -w. It does not
        # keep the directory under /work — `..` climbs out of it — and nothing needs it to: the
        # container is the boundary, only /work comes from the host, and the command can cd anyway.
        workdir_in = "/work" if cwd in (".", "", "/work") else f"/work/{cwd.lstrip('/')}"
        res = await _run_cli(
            ["docker", "exec", "-w", workdir_in, cid, "sh", "-c", command],
            timeout=timeout + 5,
        )
        return res

    async def run_code(self, code: str, lang: str = "python", timeout: int = 60) -> ExecResult:
        if lang not in ("python", "py"):
            return ExecResult("", f"unsupported language: {lang}", 2)
        await self.write("_run.py", code.encode())
        # `timeout` inside the container enforces the wall-clock limit on the interpreter itself.
        return await self.run(f"timeout {timeout} python _run.py", timeout=timeout)

    async def write(self, path: str, data: bytes) -> None:
        # /work is a host bind mount — write through the host side, jailed to the workdir.
        target = validate_within_dir(path, self.workdir)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)

    async def read(self, path: str) -> bytes:
        target = validate_within_dir(path, self.workdir)
        return await asyncio.to_thread(target.read_bytes)

    async def kill(self) -> None:
        if self.container_id:
            await _run_cli(["docker", "rm", "-f", self.container_id], timeout=20.0)
            self.container_id = None
