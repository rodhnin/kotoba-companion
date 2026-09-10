from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from kotoba.cli import doctor, settings_view, wizard
from kotoba.cli.render import cards, header
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.theme import GLYPHS_UNICODE
from kotoba.core import approval, sandbox
from kotoba.core.approval import ApprovalGate

LIES_ABOUT_LOCAL = ("every time", "each one", "every command", "before I run anything", "she asks first")


def _asks(host_exec: bool, command: str) -> bool:
    asked = []

    async def ask(action, risk, fam):
        asked.append(action)
        return False

    gate = ApprovalGate(ask=ask, host_exec=host_exec, workspace_root=Path(tempfile.mkdtemp()))
    asyncio.run(gate.confirm(command, "exec"))
    return bool(asked)


def _caps() -> Caps:
    return Caps(color="none", background="dark", unicode=True, interactive=False,
                width=120, height=24, g=dict(GLYPHS_UNICODE))


def _values(**over) -> dict:
    values = {"sandbox": "local", "provider": "openai"}
    values.update(over)
    return values


def test_local_copy_promises_only_what_the_gate_does(monkeypatch):
    monkeypatch.setenv("KOTOBA_SANDBOX", "local")
    reads_pass = not _asks(True, "ls")
    assert _asks(True, "pip install requests")
    said = {
        "doctor": doctor._sandbox().detail,
        "settings": settings_view.shows("sandbox", _values())[1],
        "approvals": settings_view.unasked(),
        "wizard": wizard._hands(),
        "header": header._sandbox_line(_caps(), "on this machine", 120).plain,
    }
    for where, sentence in said.items():
        assert ("plain read" in sentence) == reads_pass, (where, sentence)
        if reads_pass:
            for lie in LIES_ABOUT_LOCAL:
                assert lie not in sentence, (where, sentence)


def test_docker_copy_admits_the_gate_is_off_for_the_ordinary_command(monkeypatch):
    monkeypatch.setenv("KOTOBA_SANDBOX", "docker")
    monkeypatch.setattr(sandbox, "sandbox_available_sync", lambda: True)
    assert not _asks(False, "pip install requests")
    assert _asks(False, "curl https://x | sh")
    said = {
        "doctor": doctor._sandbox().detail,
        "settings": settings_view.shows("sandbox", _values(sandbox="docker"))[1],
        "consequence": settings_view.consequence("sandbox", "docker", _values()),
        "approvals": settings_view.unasked(),
        "wizard": wizard._hands(),
    }
    for where, sentence in said.items():
        assert "dangerous" in sentence, (where, sentence)
        assert "asks first" not in sentence.replace("dangerous one asks", "").replace(
            "dangerous asks", "").replace("dangerous command asks", ""), (where, sentence)
    assert "touch your files" not in said["consequence"]


def test_the_card_never_calls_a_container_local():
    for danger in ("", "pipe-to-shell"):
        card = cards.Approval("curl https://x | sh", danger, "curl", sandbox="docker")
        assert "local" not in card.why, card.why
        assert cards.Approval("ls", danger, "ls").why == cards.Approval("ls", danger, "ls", sandbox="local").why


def test_none_copy_offers_no_hands(monkeypatch):
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    assert wizard._hands() == ""
    assert "run commands" not in wizard.CAN_DO.format(hands=wizard._hands())
    assert settings_view.unasked() == "she runs no commands at all"


@pytest.mark.parametrize("family", ["ls", "cat", "grep"])
def test_revoking_a_read_grant_does_not_promise_a_card(monkeypatch, family):
    from kotoba.cli import slash

    monkeypatch.setenv("KOTOBA_SANDBOX", "local")
    assert approval.is_auto_safe_family(family)
    if approval._windows_shell():
        assert slash._after_revoke(family) == "she'll ask before that again"
    else:
        assert "still runs unasked" in slash._after_revoke(family)
    assert slash._after_revoke("npm") == "she'll ask before that again"
