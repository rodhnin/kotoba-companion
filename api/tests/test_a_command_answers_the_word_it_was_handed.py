"""The slash surface, on the seven things it used to do quietly.

A command's rule: a word it is handed either does something or is answered. Most of the family
took an argument, dropped it, and did the bare thing — obedience-shaped, and on the two toggles
worse than silence: `/calm off` with motion already off turned motion ON.

Three more break the same rule elsewhere: `/face` refused a name its neighbours would accept and
answered nothing instead of the usage line its siblings give; `/model` wrote a name the provider
does not publish in the same words a real switch prints; and `_carry`'s SENT row promised a read
that only holds while `KOTOBA_WORKSPACE_DIR` stays at its default."""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from rich.cells import cell_len
from rich.text import Text

from kotoba.cli import slash
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.kaomoji import EMOTIONS
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

OPENAI = {"provider": "openai", "base_url": ""}


def wired(width: int = 96, height: int = 40):
    caps = Caps(color="none", background="dark", unicode=True, width=width, height=height,
                g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1",
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app, buf


def said(app, name: str, arg: str = "") -> str:
    """What this ONE command printed: the buffer is emptied first, so what came before is out of the
    way while the screen's own memory of it (`last_blank`) is not."""
    app.screen.console.file.truncate(0)
    app.screen.console.file.seek(0)
    asyncio.run(slash.run(app, commands.Command(name, arg)))
    return app.screen.console.file.getvalue()


def shape(out: str) -> tuple[str, str]:
    """(the first row, the last row) of what a command printed — a blank at both ends is the rail every
    handler in the file runs on: separate, the sentence, blank."""
    lines = out.splitlines()
    return lines[0].strip(), lines[-1].strip()


# --- 1: the air around a refusal -------------------------------------------------------------------

def test_a_face_that_misses_gets_the_same_air_as_the_refusal_next_to_it():
    app, _ = wired()
    app.screen.row(Text("a row already on the glass"))
    face = said(app, "/face", "bogus")
    app.screen.row(Text("a row already on the glass"))
    setting = said(app, "/set", "bogus bogus")

    assert "I don't have a face called bogus" in face
    assert shape(face) == ("", "") == shape(setting)


# --- 2 + 3: the name, and no name at all -----------------------------------------------------------

def test_a_face_named_in_capitals_is_the_face_it_names():
    """`settings_view.check` lower-cases an enum value and `_settings` lower-cases the section, so
    /face EXCITED is excited for the same reason /settings BRAIN is BRAIN."""
    app, _ = wired()
    out = said(app, "/face", "EXCITED")

    assert "I don't have a face" not in out
    assert app.screen.face.emotion == "excited"


def test_face_with_no_name_points_at_the_command_that_lists_the_names():
    app, _ = wired()
    was = app.screen.face.emotion
    out = said(app, "/face", "")

    assert "(nothing)" not in out
    assert "/emotions" in out
    assert app.screen.face.emotion == was


# --- 4: a model name the provider does not publish -------------------------------------------------

def test_a_model_nobody_lists_is_still_written_and_no_longer_written_in_silence(monkeypatch, tmp_path):
    """Never refused — a custom base_url can be pointed at anything — but never mute either: the line
    that reports the write is the last moment a typo is cheap to fix."""
    from kotoba.core import app_settings

    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    app, _ = wired()
    out = said(app, "/model", "does-not-exist-9000")

    assert "does-not-exist-9000" in out and "doesn't list that one" in out
    assert app_settings.runtime_all()["model"] == "does-not-exist-9000"


def test_a_name_shaped_like_the_other_providers_names_the_switch_that_goes_with_it():
    assert slash._unlisted("gpt-5.6-luna", OPENAI) == ""
    assert slash._unlisted("gpt-5.3-codex", OPENAI) == ""
    assert "/set provider xai goes with it" in slash._unlisted("grok-4.3", OPENAI)
    assert slash._unlisted("grok-4.3", OPENAI).startswith("xAI (Grok) is the one")
    assert "/set provider openai" in slash._unlisted("gpt-5.6-luna", {"provider": "xai",
                                                                     "base_url": ""})
    # A base_url of your own is you saying the provider's list is not the ceiling.
    assert slash._unlisted("does-not-exist-9000", {**OPENAI, "base_url": "http://box:1234/v1"}) == ""


def test_the_bare_model_row_is_the_headers_own_answer_and_not_a_second_one(monkeypatch, tmp_path):
    """`_model`'s own docstring promises the header, the bar and the listing cannot disagree, and this
    row was building its own pair — so it had neither of the two states `facts` knows: no key at
    all, and a model this provider does not serve."""
    from kotoba.cli import facts
    from kotoba.core import app_settings, llm, providers

    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    app, _ = wired()

    monkeypatch.setattr(llm, "get_client", lambda: None)
    assert "no key yet" in said(app, "/model")

    monkeypatch.setattr(llm, "get_client", lambda: object())
    app_settings.set_runtime("provider", "xai")
    out = said(app, "/model")

    # One command moved half the pair: the provider is xAI and the model is still OpenAI's.
    assert providers.get_spec().id == "xai" and llm.model_name().startswith("gpt-")
    assert "wrong model" in out and "/model <name> switches it" in out
    assert out.strip().startswith(facts._model(llm, providers))


# --- 5: the no-argument family ---------------------------------------------------------------------

@pytest.mark.parametrize("name", ["/last", "/emotions", "/calm", "/plate"])
def test_a_command_that_takes_nothing_says_so_instead_of_doing_the_bare_thing(name):
    app, _ = wired()
    before = (app.caps.reduced_motion, app.screen.plate_mode)
    app.screen.row(Text("a row already on the glass"))
    out = said(app, name, "bogus")

    assert f"{name} takes nothing" in out
    assert (app.caps.reduced_motion, app.screen.plate_mode) == before
    assert "nothing has run yet this session" not in out and "affectionate" not in out
    assert shape(out) == ("", "")


@pytest.mark.parametrize("name,arg", [("/last", ""), ("/emotions", ""), ("/calm", ""), ("/plate", "")])
def test_and_with_nothing_handed_to_it_the_command_still_runs(name, arg):
    app, _ = wired()
    out = said(app, name, arg)

    assert "takes nothing" not in out


# --- 6: a grid whose columns are measured ----------------------------------------------------------

def test_every_column_of_the_emotion_grid_starts_at_the_same_x():
    """Counted, not measured, was the defect: `( ･︵･ )` is seven characters that draw eight cells and
    `( ￣_￣ )?` draws ten, so a name padded to 13 CHARACTERS put column two somewhere different on
    almost every row."""
    app, _ = wired()
    out = said(app, "/emotions")

    grid = []
    for line in out.splitlines():
        starts = [cell_len(line[:line.index(e)]) for e in EMOTIONS if e in line]
        if starts:
            grid.append(starts)
    assert [len(row) for row in grid] == [3, 3, 3, 3, 2], out
    for column in range(3):
        seen = {row[column] for row in grid if len(row) > column}
        assert len(seen) == 1, (column, seen, out)


# --- 7: a promise that only the default setup keeps ------------------------------------------------

def _carry_app():
    heard: list[str] = []
    return SimpleNamespace(screen=SimpleNamespace(chrome=heard.append), heard=heard,
                           session=SimpleNamespace(session_id="s-carry"), shared_n=0, gifts=[],
                           prompt=SimpleNamespace(pending=""))


def test_the_sent_row_promises_the_reading_only_where_she_can_reach_the_file(tmp_path, monkeypatch):
    from kotoba.cli.render import cards
    from kotoba.core import file_library, path_security, workspace

    src = tmp_path / "trace.txt"
    src.write_text("a line", encoding="utf-8")
    app = _carry_app()

    # Default (no KOTOBA_WORKSPACE_DIR): the library IS the workdir, so the promise holds.
    assert workspace.resolve_workdir(None) == file_library.library_dir()
    assert "she'll read it there" in slash._carry(app, str(src), "trace.txt")

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("KOTOBA_WORKSPACE_DIR", str(project))
    note = slash._carry(app, str(src), "trace.txt")

    # The note is written for a person, so the product elides $HOME to `~`. On Windows pytest's tmp
    # lives UNDER the profile, so the literal absolute path is never in the note — asserting it there
    # measured where the temp directory happens to be, not what the row promised.
    assert "she'll read it there" not in note
    assert cards._shown(project) in note
    # And that is the truth about the file, not only about the sentence.
    stored = file_library.resolve("trace.txt")
    assert stored is not None
    with pytest.raises(path_security.PathSecurityError):
        path_security.validate_within_dir(str(stored), workspace.resolve_workdir(None))
