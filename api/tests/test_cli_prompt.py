"""The keys, driven byte for byte through a pipe, because a key that changes meaning is a key nobody
can test by hand twice the same way."""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import needs_posix_terminal
from kotoba.cli.input import commands
from kotoba.cli.input.prompt import Prompt, SlashCompleter, history_path
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.theme import GLYPHS_UNICODE

ALT_ENTER = "\x1b\r"
ENTER = "\r"


def tty_caps(**kw) -> Caps:
    return Caps(color="none", background="dark", unicode=True, interactive=True,
                width=80, g=dict(GLYPHS_UNICODE), **kw)


def typed(keys: str, caps: Caps | None = None) -> str | None:
    """One prompt, driven from a pipe with every key already in it."""
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        prompt = Prompt(caps or tty_caps(), complete_while_typing=False,
                        input=pipe, output=DummyOutput())
        return asyncio.run(prompt.ask_async())


def typed_slowly(steps: list[str]) -> str | None:
    """The same, with a beat between presses. Completion is an async task, so a Tab and the Enter after
    it delivered in ONE read are processed before any completion exists to insert."""
    async def drive() -> str | None:
        with create_pipe_input() as pipe:
            prompt = Prompt(tty_caps(), complete_while_typing=False,
                            input=pipe, output=DummyOutput())
            asking = asyncio.create_task(prompt.ask_async())
            for keys in steps:
                await asyncio.sleep(0.08)
                pipe.send_text(keys)
            return await asyncio.wait_for(asking, 5)

    return asyncio.run(drive())


@needs_posix_terminal
def test_enter_sends_the_line():
    assert typed("hola" + ENTER) == "hola"


@needs_posix_terminal
def test_alt_enter_starts_a_new_line_and_the_next_enter_sends_both():
    assert typed("one" + ALT_ENTER + "two" + ENTER) == "one\ntwo"


@needs_posix_terminal
def test_a_line_broken_by_alt_enter_keeps_every_line_it_was_given():
    assert typed("a" + ALT_ENTER + "b" + ALT_ENTER + "c" + ENTER) == "a\nb\nc"


@needs_posix_terminal
def test_tab_completes_a_slash_command_to_its_full_name():
    assert typed_slowly(["/hel", "\t", ENTER]) == "/help"


@needs_posix_terminal
def test_tab_in_the_middle_of_a_sentence_completes_nothing_and_changes_nothing():
    assert typed_slowly(["tell me about", "\t", ENTER]) == "tell me about"


@needs_posix_terminal
def test_ctrl_r_does_not_open_prompt_toolkits_own_reverse_search():
    assert typed("hola\x12" + ENTER) == "hola"


@needs_posix_terminal
def test_ctrl_d_on_an_empty_line_is_how_you_leave():
    assert typed("\x04") is None


def test_the_completer_offers_every_command_behind_a_bare_slash():
    from prompt_toolkit.document import Document

    hits = list(SlashCompleter().get_completions(Document("/", 1), None))
    assert [c.text for c in hits] == list(commands.COMMANDS)


def test_a_completion_replaces_the_slash_it_was_typed_after_and_not_only_the_word():
    from prompt_toolkit.document import Document

    hit = next(iter(SlashCompleter().get_completions(Document("/hel", 4), None)))
    assert hit.start_position == -4


@needs_posix_terminal
def test_history_goes_to_the_configured_file_and_never_to_the_real_home(monkeypatch, tmp_path):
    """With no variable of its own it follows the home Kotoba was told to use — which is what makes
    the second half of this test's name true under an isolated suite. It used to read the real home
    directly, so every install sharing one machine also shared one history."""
    from kotoba.paths import home_dir

    monkeypatch.delenv("KOTOBA_CLI_HISTORY", raising=False)
    assert history_path() == home_dir() / "cli_history"
    assert history_path() != Path.home() / ".kotoba" / "cli_history"
    monkeypatch.setenv("KOTOBA_CLI_HISTORY", str(tmp_path / "nested" / "cli_history"))
    assert typed("remember me" + ENTER) == "remember me"
    assert "remember me" in (tmp_path / "nested" / "cli_history").read_text(encoding="utf-8")


@needs_posix_terminal
def test_a_line_you_sent_before_ghosts_after_the_cursor_and_the_right_arrow_takes_it(monkeypatch, tmp_path):
    """The demo ships `auto_suggest=AutoSuggestFromHistory()` and the port carried the ghost's STYLE
    (`auto-suggestion` in `_style`) without the argument that draws it — the box looked right and
    remembered nothing. The suggestion task is async, so the arrow gets a beat, like a completion."""
    monkeypatch.setenv("KOTOBA_CLI_HISTORY", str(tmp_path / "cli_history"))

    async def drive() -> tuple[str | None, str | None]:
        with create_pipe_input() as pipe:
            prompt = Prompt(tty_caps(), complete_while_typing=False,
                            input=pipe, output=DummyOutput())
            pipe.send_text("search live2d lipsync" + ENTER)
            first = await asyncio.wait_for(prompt.ask_async(), 5)
            asking = asyncio.create_task(prompt.ask_async())
            for keys in ("sea", "\x1b[C", ENTER):
                await asyncio.sleep(0.08)
                pipe.send_text(keys)
            return first, await asyncio.wait_for(asking, 5)

    first, second = asyncio.run(drive())
    assert first == "search live2d lipsync"
    assert second == "search live2d lipsync", "the ghost was not there for the arrow to accept"


def test_a_prompt_with_no_terminal_reads_a_plain_line_and_writes_no_escape_byte(monkeypatch, capsys):
    caps = Caps(color="none", background="dark", unicode=True, interactive=False,
                width=80, g=dict(GLYPHS_UNICODE))
    monkeypatch.setattr(sys, "stdin", io.StringIO("piped question\n"))
    prompt = Prompt(caps)
    assert prompt.session is None
    assert asyncio.run(prompt.ask_async()) == "piped question"
    assert asyncio.run(prompt.ask_async()) is None
    assert "\x1b" not in capsys.readouterr().out


@needs_posix_terminal
def test_whatever_was_typed_during_the_boot_probes_starts_the_first_line():
    assert typed(ENTER, tty_caps(leftover="already typed")) == "already typed"


@needs_posix_terminal
def test_a_key_binding_that_raises_is_logged_and_the_line_survives_it(caplog):
    """prompt_toolkit does not swallow it — `Application._handle_exception` prints the whole Python
    traceback over her transcript through `run_in_terminal` and then blocks the box on `Press ENTER to
    continue...`, so the scrollback is gone and the next Enter is eaten by a prompt nobody asked for.
    Verified in a real pty before this guard existed. Six of the seven handlers call out — into the
    app's line-up and paste, and into a panel that reads settings off the engine."""
    def blow_up(_buffer):
        raise RuntimeError("the line-up blew up")

    with create_pipe_input() as pipe:
        prompt = Prompt(tty_caps(), complete_while_typing=False,
                        input=pipe, output=DummyOutput())
        prompt.line_up = blow_up
        pipe.send_text("\x12kept" + ENTER)
        with caplog.at_level("WARNING", logger="kotoba.cli"):
            assert asyncio.run(prompt.ask_async()) == "kept"

    assert "_no_reverse_search key binding failed" in caplog.text
    assert "the line-up blew up" in caplog.text
