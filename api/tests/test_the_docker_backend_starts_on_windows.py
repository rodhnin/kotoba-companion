"""The Docker backend must degrade, never crash, on a platform with no POSIX uid.

`--user <uid>:<gid>` is what keeps container writes owned by the caller on Linux. Windows has neither
attribute, and Docker Desktop does not need the flag, so asking for it there raised AttributeError out
of `start()` instead of running — reachable whenever DOCKER_HOST is set.
"""
from __future__ import annotations

import pytest
from conftest import posix_only

from kotoba.core.sandbox import docker


@posix_only("a numeric uid, which is the thing being pinned")
def test_posix_still_pins_the_caller():
    flags = docker._user_flags()
    assert flags[0] == "--user"
    assert ":" in flags[1]


def test_a_platform_without_a_uid_simply_omits_the_flag(monkeypatch):
    monkeypatch.delattr(docker.os, "getuid", raising=False)
    monkeypatch.delattr(docker.os, "getgid", raising=False)
    assert docker._user_flags() == []
