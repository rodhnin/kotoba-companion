"""DockerSandbox isolation, run against a REAL Docker daemon rather than mocks.

The isolation properties only exist in the daemon's behaviour, so a mocked version of this file would
pin nothing. That real daemon is also why the whole module waits to be asked for: every test here
starts a container from a public image, and on a machine that has not cached it that is a hundred-odd
megabytes fetched from a registry and then EXECUTED — not something a bare `pytest` may decide for
somebody who just cloned. The daemon is not even probed until the opt-in is set.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from conftest import NETWORK_ALLOWED, needs_network

from kotoba.core.path_security import PathSecurityError
from kotoba.core.sandbox.docker import DockerSandbox, _run_cli, docker_available

pytestmark = [
    pytest.mark.network,
    needs_network,
    pytest.mark.skipif(not (NETWORK_ALLOWED and asyncio.run(docker_available())),
                       reason="Docker daemon not available"),
]


def _run(coro):
    return asyncio.run(coro)


async def _container_exists(cid: str) -> bool:
    res = await _run_cli(["docker", "ps", "-aq", "--filter", f"id={cid}"], timeout=10.0)
    return bool(res.stdout.strip())


def test_echo_and_python_run(tmp_path):
    async def go():
        sb = DockerSandbox(tmp_path)
        await sb.start()
        try:
            r1 = await sb.run("echo hola")
            r2 = await sb.run_code("print(2 + 2)")
            return r1, r2
        finally:
            await sb.kill()

    r1, r2 = _run(go())
    assert r1.ok and "hola" in r1.stdout
    assert r2.ok and r2.stdout.strip() == "4"


def test_network_is_off(tmp_path):
    """`--network none` means the container has no interface to open a socket on."""
    async def go():
        sb = DockerSandbox(tmp_path)
        await sb.start()
        try:
            # --network none → no socket can be opened.
            return await sb.run_code(
                "import socket; socket.setdefaulttimeout(3); "
                "socket.create_connection(('1.1.1.1', 53))"
            )
        finally:
            await sb.kill()

    res = _run(go())
    assert not res.ok


def test_root_fs_readonly_workdir_writable(tmp_path):
    async def go():
        sb = DockerSandbox(tmp_path)
        await sb.start()
        try:
            work = await sb.run("touch /work/ok.txt && echo W=$?")
            root = await sb.run("touch /rootfile 2>/dev/null; echo R=$?")
            return work, root
        finally:
            await sb.kill()

    work, root = _run(go())
    assert "W=0" in work.stdout            # /work (bind mount) is writable
    assert "R=0" not in root.stdout        # read-only root fs → write fails


def test_host_secrets_not_forwarded(tmp_path):
    async def go():
        os.environ["KOTOBA_SECRET_TEST"] = "leak-me"
        try:
            sb = DockerSandbox(tmp_path)
            await sb.start()
            try:
                return await sb.run("printenv KOTOBA_SECRET_TEST || echo MISSING")
            finally:
                await sb.kill()
        finally:
            os.environ.pop("KOTOBA_SECRET_TEST", None)

    res = _run(go())
    assert "leak-me" not in res.stdout
    assert "MISSING" in res.stdout


def test_kill_removes_container(tmp_path):
    async def go():
        sb = DockerSandbox(tmp_path)
        await sb.start()
        cid = sb.container_id
        assert cid and await _container_exists(cid)
        await sb.kill()
        assert sb.container_id is None
        # Give Docker a moment to reap the --rm container.
        for _ in range(10):
            if not await _container_exists(cid):
                break
            await asyncio.sleep(0.5)
        return await _container_exists(cid)

    still_there = _run(go())
    assert still_there is False


def test_write_read_roundtrip_and_jail(tmp_path):
    async def go():
        sb = DockerSandbox(tmp_path)
        await sb.start()
        try:
            await sb.write("note.txt", b"hi there")
            data = await sb.read("note.txt")
            escaped = None
            try:
                await sb.write("../escape.txt", b"x")
            except PathSecurityError as e:
                escaped = e
            return data, escaped
        finally:
            await sb.kill()

    data, escaped = _run(go())
    assert data == b"hi there"
    assert isinstance(escaped, PathSecurityError)


def test_command_timeout(tmp_path):
    async def go():
        sb = DockerSandbox(tmp_path)
        await sb.start()
        try:
            return await sb.run("sleep 30", timeout=1)
        finally:
            await sb.kill()

    res = _run(go())
    assert res.exit_code == 124  # killed by the outer timeout
