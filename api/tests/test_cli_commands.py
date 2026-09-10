"""The command surface: the five places a raw runtime value lies, and the two gates that stop it.

Every string asserted here is one a person reads off the screen, which is why they are asserted whole:
`/set sandbox rocket` printing `must be one of ('local', 'docker', 'none')` is the defect, not a
cosmetic difference.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import os
import re
import subprocess
import sys
import time
from types import SimpleNamespace

from conftest import posix_only, make_symlink
from kotoba.cli import settings_view, slash, state
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console


@contextlib.contextmanager
def opener_spy(monkeypatch):
    """What the desktop was actually HANDED, on either platform.

    POSIX spawns `xdg-open`/`open` through Popen; Windows calls os.startfile with the path alone.
    Asserting the POSIX argv shape reported five Windows failures against a branch doing exactly the
    right thing — and the no-spawn half passed there vacuously, watching a call that never happens."""
    handed: list[str] = []
    if sys.platform == "win32":
        monkeypatch.setattr(os, "startfile", lambda spot: handed.append(str(spot)), raising=False)
    else:
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(subprocess, "Popen",
                            lambda argv, **kw: handed.append(argv[1]) or SimpleNamespace(pid=1))
    yield handed


def wired(height: int = 24) -> tuple[App, io.StringIO]:
    """`height` is 24 because that is what a terminal is unless it says otherwise, and every printed
    listing is fitted to it (`render/listing`). A test about what a WHOLE listing says has to ask for a
    window that holds one."""
    caps = Caps(color="none", background="dark", unicode=True, width=80, height=height,
                g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1", events=SimpleNamespace(steps={}, settle=lambda: None))
    return app, buf


def said(app, name: str, arg: str = "") -> str:
    asyncio.run(slash.run(app, commands.Command(name, arg)))
    return app.screen.console.file.getvalue()


BASE = {"provider": "openai", "base_url": "", "model": "gpt-5.4-mini", "work_model": "",
        "code_model": "", "research_model": "", "utility_model": "", "reasoning_effort": "",
        "expressive": True, "elevenlabs_agent_id": "", "voice_mode": "local",
        "tts_engine": "expressive", "sandbox": "local", "work_timeout": 1500.0,
        "work_max_iter": 40, "work_max_tool_calls": 40, "work_fail_limit": 6}


def values(**over) -> dict:
    return {**BASE, **over}


# --- the five places a raw runtime value lies ------------------------------------------------------

def test_an_empty_base_url_is_not_no_url_but_the_providers_own():
    assert settings_view.shows("base_url", values()) == ("", "openai's own")
    assert settings_view.shows("base_url", values(provider="xai")) == (
        "", "xai's own: https://api.x.ai/v1")


def test_an_empty_per_role_model_inherits_along_the_chain_core_llm_really_walks():
    from kotoba.core.llm import _ROLE_CHAIN

    assert settings_view.shows("work_model", values())[1] == "same as companion"
    assert settings_view.shows("code_model", values())[1] == "same as work"
    # The note is only true while the chain says so, so read the chain rather than trusting the note.
    assert [k for k, _ in _ROLE_CHAIN["code"]] == ["code_model", "work_model", "model"]
    assert [k for k, _ in _ROLE_CHAIN["utility"]] == ["utility_model", "model"]


def test_reasoning_effort_says_when_the_provider_takes_it_as_something_else():
    assert settings_view.shows("reasoning_effort", values()) == ("off", "no reasoning at all")
    assert settings_view.shows("reasoning_effort", values(reasoning_effort="off")) == (
        "off", "no reasoning at all")
    assert settings_view.shows("reasoning_effort", values(reasoning_effort="minimal")) == (
        "minimal", "openai takes it as low")
    assert settings_view.shows("reasoning_effort", values(reasoning_effort="high"))[1] == ""


def test_expressive_on_admits_the_fast_engine_is_deleting_the_tags_anyway():
    from kotoba.core import app_settings

    assert settings_view.shows("expressive", values()) == ("on", "")
    off = values(tts_engine="fast")
    assert settings_view.shows("expressive", off) == (
        "on", "the tags are off anyway — tts_engine is fast")
    # And that is the backend's own rule, not a story the listing tells.
    assert app_settings.audio_tags_enabled.__doc__


def test_sandbox_and_the_two_voice_rows_say_what_they_would_hide():
    from kotoba.core import approval

    local = settings_view.shows("sandbox", values())[1]
    assert local.startswith("she runs on this machine — ")
    assert ("plain read" in local) == (not approval._windows_shell()), local
    assert "dangerous" in settings_view.shows("sandbox", values(sandbox="docker"))[1]
    assert settings_view.shows("sandbox", values(sandbox="none"))[1] == (
        "she runs nothing on this machine at all")
    assert settings_view.shows("voice_mode", values(voice_mode="agent"))[1] == (
        "through ElevenLabs' own tunnel")
    assert settings_view.shows("tts_engine", values(tts_engine="fast"))[1] == (
        "flash_v2_5 by socket, no tags")


# --- what /set will take ---------------------------------------------------------------------------

def test_a_refusal_is_in_her_voice_and_never_the_validators():
    assert settings_view.check("sandbox", "rocket", values())[1] == (
        "sandbox takes local · docker · none — 'rocket' isn't one of them")
    assert settings_view.check("work_timeout", "5", values())[1] == (
        "work_timeout can't go under 30 — that's the floor")
    assert settings_view.check("trust", "nothing", values())[1] == (
        "trust isn't mine to change — it comes from the environment")
    assert settings_view.check("nonsense", "1", values())[1] == (
        "I don't have a setting called nonsense — /settings lists every one of mine")


def test_the_two_numbers_that_are_not_sizes_are_refused_and_never_raised(monkeypatch, tmp_path):
    """`inf` and `nan` are the rule this mirror was missing, and the backend has had it since an
    infinite work_timeout could never be reclaimed (`app_settings._finite`). Missing here it did not
    merely drift: `int(float("inf"))` raises OverflowError inside `check`, which caught only
    ValueError, and `nan < 30` is False so a NaN passed the floor and reached `set_runtime` — whose
    own guard then raised. Neither exception is caught in `slash._set`, so both took the whole session
    down with a traceback, on the one surface whose rule is that she never shows one."""
    from kotoba.core import app_settings

    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    for raw in ("inf", "-inf", "nan", "1e400"):
        for key in ("work_max_iter", "work_timeout"):
            value, why = settings_view.check(key, raw, values())
            assert value is None and why == f"{key} is a number — {raw!r} isn't one", (key, raw)

    app, _ = wired()
    for arg in ("work_max_iter inf", "work_timeout nan"):
        out = said(app, "/set", arg)
        assert "isn't one" in out, out
        app.screen.console.file.truncate(0), app.screen.console.file.seek(0)
    assert app_settings.runtime_all()["work_max_iter"] != float("inf")

    assert settings_view.check("work_max_iter", "12", values()) == (12, "")


def test_a_bool_that_is_neither_is_refused_where_the_backend_would_have_stored_false():
    from kotoba.core import app_settings

    assert settings_view.check("expressive", "tru", values())[1] == (
        "expressive is on or off — 'tru' is neither")
    assert settings_view.check("expressive", "on", values()) == (True, "")
    # The difference is deliberate: a checkbox shows you the result, a printed line does not.
    assert app_settings._bool("tru") is False


def test_her_name_is_not_the_environment_and_says_so_differently():
    why = settings_view.check("name", "Bob", values())[1]
    assert why == ("name isn't something /set reaches — it's what she's got, and it changes where it "
                   "was made")


def test_only_the_two_that_change_the_trust_model_are_gated():
    assert settings_view.GATED_KEYS == ("sandbox", "provider")
    assert settings_view.consequence("sandbox", "none", values()) == (
        "that takes my hands away — shell and execute_code stop being offered and I start no process "
        "on this machine. The MCP servers and the browser are approved separately and still open when "
        "you use them")
    assert settings_view.consequence("provider", "xai", values()).startswith(
        "that sends everything you say to xai instead of openai")


def test_what_a_row_accepts_shortens_rather_than_wrapping_or_being_cut():
    assert settings_view.accepts("reasoning_effort") == "off · minimal · low · medium · high · xhigh · max"
    assert settings_view.accepts("reasoning_effort", 20) == "off … max"
    assert settings_view.accepts("work_timeout") == "a number, 30 or more"
    assert settings_view.accepts("model") == "any name"


def test_every_settable_key_the_backend_has_is_in_a_section_and_nothing_else_is():
    from kotoba.core import app_settings

    listed = [k for _, section in settings_view.SECTIONS for k in section]
    assert sorted(listed) == sorted(app_settings.runtime_all())


# --- when ------------------------------------------------------------------------------------------

def test_a_timestamp_is_read_as_the_utc_sqlite_writes_and_shown_on_the_local_clock():
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    assert settings_view.when(stamp) == "today " + time.strftime("%H:%M", time.localtime())
    yesterday = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 86400))
    assert settings_view.when(yesterday).split()[0] in ("yesterday", "today")
    assert settings_view.when("") == ""
    assert settings_view.when("not a date") == "not a date"


# --- the commands themselves -----------------------------------------------------------------------

def test_exit_is_quit_under_another_name_and_is_not_a_row_of_its_own_in_the_listing():
    assert commands.parse("/exit").name == "/quit"
    assert commands.parse("/exit").known is True
    assert "/exit" not in commands.COMMANDS
    assert commands.complete("/e") == ["/emotions"]


def test_help_heads_each_of_its_four_blocks_with_a_chip():
    app, _ = wired(height=60)
    out = said(app, "/help")
    for word in ("C O M M A N D S", "L A U N C H", "K E Y S", "T R Y"):
        assert word in out
    for name in commands.COMMANDS:
        assert name in out


def test_a_column_is_as_wide_as_its_own_content_and_not_the_next_blocks():
    """Every second column starts on the same cell within its own block, and that cell is set by the
    longest entry in THAT block. Hard-coded widths are how two entries ended up welded into one word."""
    app, _ = wired(height=60)
    out = said(app, "/help")
    blocks, current = [], []
    for line in out.split("\n"):
        if not line.strip() or line.lstrip().startswith(("C O M M", "L A U N", "K E Y", "T R Y")):
            if len(current) > 1:
                blocks.append(current)
            current = []
        elif "  " in line.strip():
            current.append(line)
    if len(current) > 1:
        blocks.append(current)
    assert len(blocks) >= 3, out
    origins = []
    for block in blocks:
        starts = {re.search(r"\s{2,}", line.rstrip()).end() for line in block}
        assert len(starts) == 1, block
        origins.append(starts.pop())
    assert len(set(origins)) > 1, "every block was given the same width, so one of them was not measured"


def test_the_long_job_commands_say_there_isnt_one_rather_than_drawing_an_empty_receipt():
    app, buf = wired()
    assert "nothing of hers is running out here — /work is for the long job" in said(app, "/work")
    assert "nothing of hers is running out here — /stop is for the long job" in said(app, "/stop")
    assert "WORK" not in buf.getvalue()


def test_work_n_names_the_span_it_has_instead_of_the_number_you_asked_for():
    app, _ = wired()
    app.works = [state.Work("the first one", 1)]
    assert "there's no job 4 this session — job 1 is all of them" in said(app, "/work", "4")
    app.works.append(state.Work("the second one", 2))
    assert "there's no job 9 this session — 1 to 2 is all of them" in said(app, "/work", "9")


def test_work_with_no_argument_takes_the_running_one_and_else_the_last():
    app, _ = wired()
    app.works = [state.Work("the first one", 1), state.Work("the second one", 2)]
    app.works[0].state, app.works[1].state = "ok", "running"
    assert "the second one" in said(app, "/work")
    app.works[1].state = "ok"
    assert "the second one" in said(app, "/work")


def test_a_stopped_job_stamps_every_row_that_was_still_open():
    job = state.Work("the long one", 1)
    job.tools.append(state.Tool("shell", "npm test"))
    job.helpers.append(state.Helper("h1", "helper", "look at the front end", state="running"))
    slash._cut(job)
    assert job.state == "interrupted"
    assert [t.state for t in job.tools] == ["interrupted"]
    assert [h.state for h in job.helpers] == ["interrupted"]


def test_open_lists_what_she_handed_over_when_the_number_is_not_one_of_them():
    app, _ = wired()
    assert "she hasn't handed you anything yet" in said(app, "/open")
    app.gifts.append(state.Gift("sent", "trace.png", "she can see this one"))
    assert "trace.png" in said(app, "/open", "9")


def test_a_row_number_she_never_printed_never_resolves_to_a_row(monkeypatch):
    """Python indexes backwards from the end, so `app.gifts[int(arg) - 1]` answered `/open 0` with the
    LAST gift and `/open -1` with the second-to-last — and answered them by spawning `xdg-open` on a
    file the person had not asked for. Both are what somebody types meaning "the first one".

    `/work` already bounds its own ordinal on both sides; `/open` and `/helpers` did not, and `/open`
    is the one that launches something. Out of range is out of range: they fall back to the listing,
    exactly as `/open 9` already did."""
    import subprocess

    spawned = []
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: spawned.append(argv) or SimpleNamespace(pid=1))
    app, _ = wired()
    app.gifts.append(state.Gift("link", "https://example.org/first", "first"))
    app.gifts.append(state.Gift("link", "https://example.org/last", "last"))
    app.last_helpers = [state.Helper("h1", "researcher", "the first goal"),
                        state.Helper("h2", "coder", "the last goal")]

    for arg in ("0", "-1", "-2"):
        out = said(app, "/open", arg)
        assert "opening →" not in out, f"/open {arg} opened something"
        assert spawned == [], f"/open {arg} spawned {spawned}"
        assert "https://example.org/first" in out and "https://example.org/last" in out
        app.screen.console.file.truncate(0), app.screen.console.file.seek(0)

    for arg in ("0", "-1"):
        out = said(app, "/helpers", arg)
        assert "2 helpers last time" in out, f"/helpers {arg} picked one"
        app.screen.console.file.truncate(0), app.screen.console.file.seek(0)


def test_open_actually_spawns_the_platform_opener_for_a_real_file(tmp_path, monkeypatch):
    """Live QA found `/open N` printing `opening →` and spawning NOTHING. The command is meant to hand
    the file to the platform opener, so the spawn is the contract; the printed line may only claim it
    after it happened."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    (tmp_path / "files" / "reports").mkdir(parents=True)
    (tmp_path / "files" / "reports" / "latency.md").write_text("hi", encoding="utf-8")
    with opener_spy(monkeypatch) as handed:
        app, _ = wired()
        app.gifts.append(state.Gift("ready", "reports/latency.md", "the write-up"))
        out = said(app, "/open", "1")
    assert "opening → reports/latency.md" in out
    assert handed == [str(tmp_path / "files" / "reports" / "latency.md")]


@posix_only("a desktop opener chosen by name — Windows has exactly one and it is os.startfile")
def test_the_opener_is_the_one_this_desktop_answers_to(tmp_path, monkeypatch):
    """The spy asserts WHAT was handed over, so on its own it stopped pinning WHO it was handed to —
    and `open` versus `xdg-open` is a real per-platform choice with nothing else watching it."""
    import subprocess

    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    (tmp_path / "files").mkdir(parents=True)
    (tmp_path / "files" / "note.md").write_text("hi", encoding="utf-8")
    spawned: list = []
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: spawned.append(argv) or SimpleNamespace(pid=1))
    app, _ = wired()
    app.gifts.append(state.Gift("saved", "note.md", "she wrote this"))
    said(app, "/open", "1")
    assert spawned and spawned[0][0] == ("open" if sys.platform == "darwin" else "xdg-open")


def test_open_on_a_missing_file_says_so_and_spawns_nothing(tmp_path, monkeypatch):
    """The grave half of the same finding — it has to say it could not find the file, not report it as
    done: a gift whose file is gone — or an /attach that rode the context and never touched disk —
    must not be announced as opening."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    with opener_spy(monkeypatch) as handed:
        app, _ = wired()
        app.gifts.append(state.Gift("sent", "trace.png", "she can see this one"))
        out = said(app, "/open", "1")
    # Read past the fold. The path is printed in full and wrapped to the width, so on a machine whose
    # temporary directory is long — a CI workspace, a Windows profile — the name arrives split across
    # two rows and a search for it found nothing, blaming the code for the terminal's own line break.
    flat = out.replace("\n", "")
    assert "there's nothing at" in flat and "trace.png" in flat
    assert "opening →" not in out
    assert handed == []


def test_open_hands_a_link_gift_straight_to_the_opener(monkeypatch):
    with opener_spy(monkeypatch) as handed:
        app, _ = wired()
        app.gifts.append(state.Gift("link", "https://example.org/docs", "the docs"))
        out = said(app, "/open", "1")
    assert "opening → https://example.org/docs" in out
    assert handed == ["https://example.org/docs"]


@posix_only("a platform whose desktop opener can be missing — os.startfile is always there")
def test_open_without_an_opener_installed_says_where_the_thing_is(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    app, _ = wired()
    app.gifts.append(state.Gift("link", "https://example.org/docs", "the docs"))
    out = said(app, "/open", "1")
    assert "I can't open things on this machine" in out and "https://example.org/docs" in out
    assert "opening →" not in out


def test_helpers_reads_the_last_line_up_back_after_the_turn_that_sent_it_out_ended():
    """RE-RECORDED: the old claim `they only run inside a work turn` was itself read from inside one."""
    app, _ = wired()
    assert "no helpers yet" in said(app, "/helpers")
    app.helpers.append(state.Helper("h1", "helper", "read the reconnect paths", state="ok"))
    app._commit_roster()
    assert app.last_helpers and app.helpers == []
    assert "read the reconnect paths" in said(app, "/helpers")


def test_calm_and_plate_are_toggles_and_say_which_way_they_went():
    app, _ = wired()
    assert "motion off" in said(app, "/calm")
    assert app.caps.reduced_motion is True
    assert "motion on" in said(app, "/calm")
    assert "nameplate → quiet" in said(app, "/plate")
    assert app.screen.plate_mode == "quiet"


def test_a_confirm_no_terminal_can_answer_is_left_alone_rather_than_taken_as_a_yes():
    app, _ = wired()
    assert asyncio.run(slash._confirm(app, "sandbox local → none", "that turns the gate off")) is False
    assert "left alone — there's no terminal to ask" in app.screen.console.file.getvalue()
    assert app.confirm is None


def test_open_refuses_a_target_that_leaves_her_files(tmp_path, monkeypatch):
    """A gift's target is a string the MODEL wrote — the artifact frame carries the raw `write_file`
    argument — and `xdg-open` RUNS what it is handed for a `.desktop` file. `../` resolved against her
    library reached the whole disk, which is exactly what every model-supplied path must be stopped
    from doing before it is opened. Reproduced with a stub opener before the jail existed: all
    four spawned. Two of them need POSIX and the WHOLE test was skipped for them, which took the
    two escape shapes that are not platform-specific with it."""
    library = tmp_path / "files"
    (library / "sub").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET", encoding="utf-8")
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "in-home.txt").write_text("HOME", encoding="utf-8")

    targets = ["../outside/secret.txt", str(outside / "secret.txt")]
    if sys.platform != "win32":
        monkeypatch.setenv("HOME", str(tmp_path / "home"))   # Windows expanduser reads USERPROFILE
        make_symlink(library / "innocent.txt", outside / "secret.txt")
        targets += ["~/in-home.txt", "innocent.txt"]

    monkeypatch.setenv("KOTOBA_FILES_DIR", str(library))
    with opener_spy(monkeypatch) as handed:
        for target in targets:
            app, _ = wired()
            app.gifts.append(state.Gift("saved", target, "she wrote this"))
            out = said(app, "/open", "1")
            assert "isn't in your files" in out, target
            assert "opening →" not in out, target
    assert handed == []


def test_open_still_opens_everything_that_really_is_in_her_files(tmp_path, monkeypatch):
    """The other half of the jail: the paths that must keep working. Spaces, quotes and shell
    metacharacters are not a quoting question — the opener receives one argument with the literal
    bytes in it and no shell ever sees them.

    The double quote is POSIX-only because Windows refuses it in a FILENAME (`< > : " / \\ | ? *`),
    so creating the fixture raised there — a test failure that said nothing about the jail."""
    library = tmp_path / "files"
    (library / "sub").mkdir(parents=True)
    names = ["two words.txt", "-dashed.txt", "sub/deep.txt"]
    if sys.platform != "win32":
        names.insert(1, "quote'and\"dquote.txt")
    for name in names:
        (library / name).write_text("hi", encoding="utf-8")

    monkeypatch.setenv("KOTOBA_FILES_DIR", str(library))
    with opener_spy(monkeypatch) as handed:
        for target in [*names, str(library / "sub" / "deep.txt")]:
            app, _ = wired()
            app.gifts.append(state.Gift("saved", target, "she wrote this"))
            assert "opening →" in said(app, "/open", "1"), target
    assert handed == [str(library / n) for n in names] + [str(library / "sub" / "deep.txt")]


@posix_only("a spawned child that can be handed its own stdin — os.startfile takes no streams")
def test_the_opener_never_gets_the_terminal_she_is_reading_keys_from(tmp_path, monkeypatch):
    """It inherits the terminal otherwise, and a handler that reads the tty takes the keys out of
    her prompt."""
    import subprocess

    library = tmp_path / "files"
    library.mkdir(parents=True)
    (library / "note.md").write_text("hi", encoding="utf-8")
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(library))
    seen = {}
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: seen.update(kw) or SimpleNamespace(pid=1))
    app, _ = wired()
    app.gifts.append(state.Gift("saved", "note.md", "she wrote this"))
    said(app, "/open", "1")
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["start_new_session"] is True


# --- the attachment cap, at the terminal's door ----------------------------------------------------

_PNG = bytes.fromhex("89504e470d0a1a0a")


def test_the_fifth_file_of_a_message_is_refused_here_too(tmp_path):
    """`core.attachments.add` answers True or False and every front door has to pass that on. This one
    drew the gift row regardless, so a file she was not carrying was announced as sent — the same
    silence the web door was answering `{"ok": true}` with, one surface over.

    The cap bounds ONE message: `take()` drains the pile, so the sentence has to say send this one
    first rather than "you are out of room"."""
    from kotoba.core import attachments

    attachments._pending.clear()
    attachments._shared.clear()
    app, buf = wired()
    for i in range(attachments.MAX_PER_SESSION + 1):
        shot = tmp_path / f"shot{i}.png"
        shot.write_bytes(_PNG)
        said(app, "/attach", str(shot))
    screen = buf.getvalue()
    attachments._pending.clear()
    attachments._shared.clear()

    assert screen.count("she can see this one") == attachments.MAX_PER_SESSION, screen[-900:]
    flat = " ".join(screen.split())      # her sentence is wrapped to the window like any other row
    assert "shot4.png didn't fit" in flat, screen[-900:]
    assert " ".join(slash._cap_note().split()) in flat, screen[-900:]
    assert len(app.gifts) == attachments.MAX_PER_SESSION, "a row was drawn for a file she is not carrying"


def test_both_doors_refuse_the_fifth_file_in_the_same_words(tmp_path, monkeypatch):
    """The words live in two places for as long as the CLI must not import the server, so they are
    pinned equal here instead of kept in step by hand. Either surface rewording the cap fails this."""
    from kotoba.core import attachments

    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "cap.db"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    from fastapi.testclient import TestClient
    import kotoba.server as server

    attachments._pending.clear()
    body = {"kind": "image", "data_url": "data:image/png;base64,AAAA", "name": "p.png"}
    with TestClient(server.app) as c:
        for _ in range(attachments.MAX_PER_SESSION + 1):
            r = c.post("/api/session/doors/attachment", json=body,
                       headers={"Authorization": "Bearer k"})
    attachments._pending.clear()
    attachments._shared.clear()

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == slash._cap_note(), r.text


# --- the two axes `sandbox` was describing as one -------------------------------------------------

def test_the_sandbox_none_copy_cannot_disagree_with_what_the_tools_do(monkeypatch, tmp_path):
    """`none` was sold as "no gate at all — she runs without asking" on the very screen that asks you to
    agree to it, while `shell.check()` was False and she could run nothing at all. Two axes had been
    conflated: the ApprovalGate is what ASKS, the sandbox is what RUNS, and turning the runner off is
    the strictest setting there is.

    Dangerous in both directions, which is why the copy is pinned against the code and not against
    itself: somebody wanting fewer prompts picks `none` and gets an agent that silently cannot act,
    with no error to explain it, because refusing to run is not an error; somebody wanting safety reads
    it and avoids the one setting that gives them safety."""
    from kotoba.core import sandbox
    from kotoba.tools.action import execute_code, shell

    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "s.yaml"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    assert sandbox.backend_name() == "none"
    assert shell.check() is False and execute_code.check() is False, (
        "if these ever run under `none`, it is this screen that has to be rewritten, not this line")

    vals = values(sandbox="none")
    said = (settings_view.shows("sandbox", vals)[1] + " "
            + settings_view.consequence("sandbox", "none", vals)).lower()
    for sold_as in ("without asking", "no gate", "gate off"):
        assert sold_as not in said, f"the strictest setting there is, sold as the loosest: {said!r}"
    assert "shell" in said and "execute_code" in said, (
        f"nothing named the two tools that stop being offered, which is what changes: {said!r}")

    asks = settings_view.consequence("sandbox", "local", values(sandbox="local")).lower()
    assert "ask" in asks, "and the setting that really does gate must be the one that says so"
