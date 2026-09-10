"""The bot token is the whole account. Anyone holding it is her, everywhere she is.

It has three ways out and each is a different kind of accident: written into a tool schema, handed
back by the credential tool, or carried inside an exception that a friendly error message prints in
full. The third is the one that actually happens — a transport error can quote the request it failed
on, headers and all, and "the bot stopped: <everything>" is a sentence somebody pastes into a chat.
"""
from __future__ import annotations

import json

import pytest

from kotoba.discord import config, run as run_mod

FAKE = "MTIzNDU2Nzg5MDEyMzQ1Njc4.Gabcde.fghijklmnopqrstuvwxyz0123456789ABC"


def test_no_tool_ever_describes_the_token_to_the_model():
    from kotoba.tools import action

    for name in dir(action):
        mod = getattr(action, name)
        schema = getattr(mod, "SCHEMA", None)
        if not isinstance(schema, dict):
            continue
        blob = json.dumps(schema)
        assert config.TOKEN_KEY not in blob
        assert "bot token" not in blob.lower()


@pytest.mark.parametrize("exc", [
    RuntimeError(f"401 Unauthorized: Authorization=Bot {FAKE}"),
    ConnectionError(f"failed on https://discord.com/api?token={FAKE}"),
])
def test_an_error_the_operator_reads_carries_no_token(exc, capsys, monkeypatch):
    logged = []
    monkeypatch.setattr(run_mod.log, "error", lambda msg, *a: logged.append(msg % a))
    try:
        raise exc                      # raised, so it carries the traceback the log wants
    except Exception as raised:
        assert run_mod._explain(raised) == 1
    printed = capsys.readouterr().err
    assert FAKE not in printed, "the token was printed to the operator's terminal and log"
    assert "stopped" in printed, "and the message still has to say what happened"
    assert logged and FAKE not in logged[0], "it went into the log file instead"
    assert "Traceback" in logged[0], "and the traceback still has to be there to debug with"
