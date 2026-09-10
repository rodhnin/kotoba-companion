"""A command that asks for a secret must not end in a traceback when nobody can answer.

Run over ssh with no tty, from a script, or from an editor's shell, `secret.read` gets an empty stdin
and raises. Uncaught, the person sees a Python stack and reads it as a broken command rather than as
a question they were not there to answer.
"""
from __future__ import annotations

from kotoba.core import logs
from kotoba.discord import run as run_mod


class Args:
    save_token = True


def _no_terminal(monkeypatch):
    from kotoba.cli import secret

    def raise_eof(prompt=""):
        raise EOFError

    monkeypatch.setattr(secret, "read", raise_eof)
    monkeypatch.setattr(run_mod, "_installed", lambda: True)
    monkeypatch.setattr(logs, "also_to_console", lambda: None)


def test_with_no_way_to_ask_it_says_what_to_do_instead(monkeypatch, capsys):
    _no_terminal(monkeypatch)
    monkeypatch.setattr(run_mod.config, "token_from_env", lambda: "")
    assert run_mod.run(Args()) == 1
    said = capsys.readouterr().err
    assert "DISCORD_BOT_TOKEN" in said
    assert "Traceback" not in said


def test_the_environment_answers_when_the_person_cannot(monkeypatch):
    _no_terminal(monkeypatch)
    monkeypatch.setattr(run_mod.config, "token_from_env", lambda: "a-token")
    saved: list[str] = []

    async def fake_save(raw):
        saved.append(raw)
        return 0

    monkeypatch.setattr(run_mod, "save_token", fake_save)
    assert run_mod.run(Args()) == 0
    assert saved == ["a-token"]
