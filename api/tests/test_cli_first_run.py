"""First run as a person meets it: her, asking, and never a dead end.

Three defects: a provider's raw exception reached the screen truncated mid-word at 160 characters,
taking the one useful half of it — the page where a key is made — with the cut; every wrong answer
ENDED the program, so a typo meant starting over; and nothing named the command that starts it again.

A fourth this screen could introduce on its own: picking xAI leaves `model` — a single global setting
— pointing at gpt-5.4-mini, so the round trip that proves the key would come back 404 and report a
perfectly good xai- key as a bad key."""
from __future__ import annotations

from kotoba import DIST_NAME

import asyncio
import builtins
import io
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
import time

import httpx
import pytest
from conftest import needs_posix_terminal
from openai import (APIConnectionError, AuthenticationError, InternalServerError, NotFoundError,
                    PermissionDeniedError, RateLimitError)

from kotoba.cli import wizard
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_ASCII, GLYPHS_UNICODE, build_console
from kotoba.core import app_settings, providers, voice_key
from kotoba.db.database import Database
from kotoba.soul.loader import sync_from_file

REQUEST = httpx.Request("POST", "https://api.example.com/v1/responses")


def a_caps(**over) -> Caps:
    base = dict(color="none", background="dark", unicode=True, interactive=False,
                width=96, height=24, g=dict(GLYPHS_UNICODE))
    base.update(over)
    return Caps(**base)


def a_screen(caps, buf) -> Screen:
    """A Screen whose console agrees with `caps` about the width, which a real terminal does and a
    captured StringIO does not — rich falls back to 80 and the measurement would be of nothing."""
    screen = Screen(caps, console=build_console(caps, file=buf))
    screen.console.width = caps.width
    return screen


class Answers:
    """One scripted person: what they type at each prompt, and the keys they paste."""

    def __init__(self, typed=(), keys=()) -> None:
        self.typed, self.keys = list(typed), list(keys)
        self.asked, self.pasted = [], []

    def ask(self, _prompt: str = "") -> str:
        self.asked.append(_prompt)
        return self.typed.pop(0) if self.typed else ""

    def secret(self, _prompt: str = "") -> str:
        self.pasted.append(_prompt)
        return self.keys.pop(0) if self.keys else ""


_LEFT_OPEN: list = []


@pytest.fixture(autouse=True)
def _close_what_the_wizard_opened():
    """`run_wizard` hands the database back OPEN so the test can read it afterwards.

    That is fine until a test FAILS: pytest keeps the traceback, the traceback keeps the frame, the
    frame keeps the connection — and aiosqlite's worker is not a daemon thread, so the whole run hung
    at exit exactly when the suite went red. A gate that hangs when it fails is a gate nobody runs."""
    yield
    _drain()


def _drain() -> None:
    """Close every database `run_wizard` handed back. Called at teardown of each test in this module,
    and again at the top of `run_wizard`: another module imports this helper, and an autouse
    fixture only covers the module that defines it. atexit is no use here: CPython joins
    non-daemon threads BEFORE atexit runs, which is the hang itself."""
    while _LEFT_OPEN:
        db = _LEFT_OPEN.pop()
        try:
            asyncio.run(db.close())
        except Exception:
            pass


def run_wizard(monkeypatch, tmp_path, answers, verify=None, caps=None, width=96):
    """The whole wizard against a real database, with the provider round trip stubbed. Returns
    (what `wizard.run` returned, what the screen printed, the database, the (provider, key) pairs the
    stubbed `_verify` was handed) — the database is left open to be read, and closed for you at teardown.

    It is registered for that teardown the moment it opens, and NOT after the wizard returns: registering
    afterwards meant a wizard that RAISED leaked its connection, since aiosqlite's worker is not a daemon
    thread. Measured while the wizard was briefly broken mid-edit: 37 tests failed, 37 workers were left
    alive, and pytest printed its red summary and then hung until `timeout` killed it. The failure that
    makes a suite red must not also be the failure that stops it reporting."""
    _drain()
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    calls: list[tuple[str, str]] = []

    async def stub(provider_id, key):
        calls.append((provider_id, key))
        return await verify(provider_id, key) if verify else (True, "")

    monkeypatch.setattr(wizard, "_verify", stub)
    caps = caps or a_caps(width=width)
    buf = io.StringIO()

    async def go():
        db = Database("sqlite:///" + (tempfile.mkdtemp() + "/kotoba.db"))
        await db.connect()
        _LEFT_OPEN.append(db)
        await sync_from_file(db, "./soul/default.md")
        ok = await wizard.run(db, caps, screen=a_screen(caps, buf),
                              ask=answers.ask, ask_secret=answers.secret)
        return ok, db

    ok, db = asyncio.run(go())
    return ok, buf.getvalue(), db, calls


def run_bare_wizard(monkeypatch, tmp_path, answers, capsys, ascii_only=False):
    """The wizard on an install that never got the `cli` extra: `pip install kotoba`, no rich.

    `_stage` falls back to `_Plain` when `kotoba.cli.render` will not import, and a None in `sys.modules`
    is exactly the ImportError that install raises. Returns (what `wizard.run` returned, what `_Plain`
    printed, the database).

    The database is banked for teardown BEFORE the wizard runs, for the reason `run_wizard`'s own
    docstring measures."""
    for module in ("kotoba.cli.render.caps", "kotoba.cli.render.first_run"):
        monkeypatch.setitem(sys.modules, module, None)
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))

    async def stub(provider_id, key):
        return True, ""

    monkeypatch.setattr(wizard, "_verify", stub)
    _drain()

    async def go():
        db = Database("sqlite:///" + (tempfile.mkdtemp() + "/kotoba.db"))
        await db.connect()
        _LEFT_OPEN.append(db)
        await sync_from_file(db, "./soul/default.md")
        return await wizard.run(db, None, ask=answers.ask, ask_secret=answers.secret,
                                ascii_only=ascii_only), db

    ok, db = asyncio.run(go())
    return ok, capsys.readouterr().out, db


def read(db, coro):
    return asyncio.run(coro(db))


def test_a_wizard_that_raises_still_leaves_no_worker_thread_behind(monkeypatch, tmp_path):
    """The database must register for teardown before the wizard runs, not after it returns — the
    one path that matters, the wizard raising, used to skip registration and leak a non-daemon
    aiosqlite worker thread.

    The invariant: no non-daemon thread SURVIVES. Do not trust an instantaneous `is_alive()`:
    aiosqlite resolves the caller's future one statement before the worker breaks its loop, so
    `close()` can return with the thread still a few bytecodes from gone. Measured on an
    oversubscribed 12-core box: 1 red in 20 runs from that race alone."""
    import threading

    def explode(*a, **k):
        raise AttributeError("'_Themed' object has no attribute 'renders'")

    monkeypatch.setattr(wizard, "run", explode)
    before = {t.ident for t in threading.enumerate()}
    with pytest.raises(AttributeError):
        run_wizard(monkeypatch, tmp_path, Answers(typed=[], keys=[]))
    assert _LEFT_OPEN, "the database was never registered, so nothing will close it"
    opened = _LEFT_OPEN[-1]
    _drain()
    assert opened.conn is None, "the drain did not close the connection the wizard left open"
    started = [t for t in threading.enumerate() if t.ident not in before and not t.daemon]
    for t in started:
        t.join(5.0)
    leaked = [t for t in started if t.is_alive()]
    assert leaked == [], f"a raise left the interpreter unable to exit: {leaked}"


# --- every answer lands where the rest of the product reads it -------------------------------------

def test_each_answer_reaches_the_place_the_product_reads_it(monkeypatch, tmp_path):
    """The mapping kept from the deleted onboarding flow: user_name -> user_profile['name'],
    companion_name -> soul_config.name, language -> soul_config.language. A wizard that asked four
    questions and stored one would be a form with her face on it."""
    answers = Answers(typed=["2", "", "Jordan", "Yuki", "es"], keys=["xai-good-key"])
    ok, _out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)

    async def look(db):
        return (await db.get_key("llm:xai:api_key"),
                await db.fetch_user_profile_as_markdown(),
                await db.fetch_soul_config())

    key, profile, soul = read(db, look)
    assert ok is True
    assert key == "xai-good-key", "the key never reached the keystore"
    assert app_settings.runtime_value("provider", "KOTOBA_LLM_PROVIDER", "openai") == "xai"
    assert "name: Jordan" in profile, "their name never reached user_profile"
    assert soul["name"] == "Yuki", "her name never reached soul_config.name"
    assert soul["language"] == "es", "the language never reached soul_config.language"


def test_the_language_answer_is_the_one_that_also_pins_transcription(monkeypatch, tmp_path):
    """`soul_config.language` is what `core/voice/config.stt_language_for` reads to pin the STT
    session, so a code stored here has to be the shape that function accepts — anything else and the
    control decides how she REPLIES only, which is the half that already shipped once as a bug."""
    from kotoba.core.voice.config import stt_language_for

    answers = Answers(typed=["1", "", "", "", "ja"], keys=["sk-good"])
    _ok, _out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    soul = read(db, lambda db: db.fetch_soul_config())
    assert stt_language_for(soul["language"]) == "ja"


def test_auto_is_what_a_person_who_says_nothing_gets(monkeypatch, tmp_path):
    answers = Answers(typed=["", "", "", ""], keys=["sk-good"])
    ok, _out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    soul = read(db, lambda db: db.fetch_soul_config())
    assert ok is True
    assert soul["language"] == "auto"
    assert soul["name"] == "Kotoba", "an empty answer renamed her"


def test_a_ctrl_c_after_the_key_keeps_the_key(monkeypatch, tmp_path):
    """Everything after the key is skippable, so an interrupt there is not a failed install."""

    class Interrupts(Answers):
        def ask(self, _prompt: str = "") -> str:
            if self.typed:
                return self.typed.pop(0)
            raise KeyboardInterrupt

    answers = Interrupts(typed=["1", ""], keys=["sk-good"])
    ok, out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is True
    assert read(db, lambda db: db.get_key("llm:openai:api_key")) == "sk-good"
    assert "That's me" in out, "she never got to the handover"


# --- nothing is a dead end -------------------------------------------------------------------------

def test_a_key_the_provider_refuses_asks_again_instead_of_exiting(monkeypatch, tmp_path):
    """The measured defect: one wrong key ended the program and the person had to re-run it."""
    seen = []

    async def refuse_then_take(provider_id, key):
        seen.append(key)
        return (True, "") if key == "sk-right" else (False, "that one is not a key of theirs")

    answers = Answers(typed=["1", "", "", ""], keys=["sk-wrong", "sk-right"])
    ok, _out, db, _calls = run_wizard(monkeypatch, tmp_path, answers, verify=refuse_then_take)
    assert seen == ["sk-wrong", "sk-right"], "the second key was never asked for"
    assert ok is True
    assert read(db, lambda db: db.get_key("llm:openai:api_key")) == "sk-right"


def test_a_key_that_keeps_failing_stops_after_three_and_names_the_command(monkeypatch, tmp_path):
    async def always_refuse(provider_id, key):
        return False, "that one is not a key of theirs"

    answers = Answers(typed=["1"], keys=["a", "b", "c", "d"])
    ok, out, _db, calls = run_wizard(monkeypatch, tmp_path, answers, verify=always_refuse)
    assert ok is False
    assert len(calls) == wizard._MAX_TRIES, "it asked forever, or gave up on the first no"
    assert "kotoba setup" in out, "nothing told them how to start again"


def test_a_wrong_option_asks_again_instead_of_exiting(monkeypatch, tmp_path):
    answers = Answers(typed=["9", "1", "", "", "", ""], keys=["sk-good"])
    ok, out, _db, calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is True and calls == [("openai", "sk-good")]
    assert "I don't have a" in out


def test_backing_out_of_the_key_names_the_command_that_starts_over(monkeypatch, tmp_path):
    """Defect 3: an empty answer left the program with no idea what to do next."""
    answers = Answers(typed=["1"], keys=[""])
    ok, out, _db, calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is False and calls == [], "an empty key was spent on a round trip"
    assert "kotoba setup" in out


def test_backing_out_at_the_provider_writes_nothing(monkeypatch, tmp_path):
    class Interrupts(Answers):
        def ask(self, _prompt: str = "") -> str:
            raise KeyboardInterrupt

    ok, out, db, _calls = run_wizard(monkeypatch, tmp_path, Interrupts())
    assert ok is False
    assert read(db, lambda db: db.get_key("llm:openai:api_key")) is None
    assert "kotoba setup" in out


# --- the provider's exception never reaches a person -----------------------------------------------

def _failures():
    def response(code):
        return httpx.Response(code, request=REQUEST)

    openai = providers.PROVIDERS["openai"]
    xai = providers.PROVIDERS["xai"]
    return [
        (openai, APIConnectionError(request=REQUEST), "sk-x"),
        (openai, AuthenticationError(
            "Incorrect API key provided: sk-abc. You can find your API key at "
            "https://platform.openai.com/account/api-keys.", response=response(401),
            body={"error": {"code": "invalid_api_key", "message": "Incorrect API key provided"}}),
         "sk-nope"),
        (openai, AuthenticationError("x", response=response(401),
                                     body={"error": {"code": "invalid_api_key"}}), "xai-abc"),
        (openai, RateLimitError("You exceeded your current quota", response=response(429),
                                body={"error": {"code": "insufficient_quota",
                                                "message": "check your plan and billing details"}}),
         "sk-x"),
        (openai, RateLimitError("slow down", response=response(429),
                                body={"error": {"code": "rate_limit_exceeded"}}), "sk-x"),
        (xai, PermissionDeniedError("no", response=response(403), body=None), "xai-x"),
        (xai, NotFoundError("nope", response=response(404), body=None), "xai-x"),
        (openai, InternalServerError("boom", response=response(500), body=None), "sk-x"),
        (openai, None, ""),
    ]


@pytest.mark.parametrize("spec,exc,key", _failures())
def test_a_failure_is_translated_and_never_quotes_the_exception(spec, exc, key):
    """A whole sentence, ended on purpose: the measured defect ended mid-word at 160 characters. A URL
    is allowed the last position instead of a full stop, because punctuation welded to one breaks
    click-to-open."""
    said = wizard._explain(spec, exc, key)
    tail = said.rstrip()
    assert tail and (tail[-1] in ".!" or tail.split()[-1].startswith("http"))
    if exc is not None:
        assert type(exc).__name__ not in said, "the exception class reached the screen"
    for leak in ("Error code", "Traceback", "{'", '{"', "status_code"):
        assert leak not in said, f"{leak!r} reached the screen"
    for code in ("401", "403", "404", "429", "500"):
        assert code not in said, "an HTTP status reached the screen"


def test_every_command_she_names_is_one_that_exists_and_is_accepted():
    """A screen that ends by naming the wrong command is the same defect as one that names none. `/set`
    refuses `language`, `name` and `voice_id` by design (`settings_view.HERS`), so those may never be
    offered here — and any `/set <key>` that IS offered has to be one `/set` takes.

    Read off the SOURCE, not the module constants: half of what she says is built in an f-string inside
    a function, and that half is the half that names `/set model`."""
    import re as _re

    from kotoba.cli import settings_view
    from kotoba.core import app_settings

    copy = pathlib.Path(wizard.__file__).read_text(encoding="utf-8")
    values = app_settings.runtime_all()
    for key in _re.findall(r"/set (\w+)", copy):
        assert key not in settings_view.HERS, f"/set {key} is refused by design"
        assert settings_view.check(key, "x", values)[1] == "" or key in values, \
            f"/set {key} is not a setting"


def test_a_failure_that_is_not_the_network_is_not_reported_as_the_network():
    """Every exception with no HTTP status behind it used to read as `your network is down` — a bug of
    ours included, which is the one case where that sentence sends a person to fix the wrong thing."""
    spec = providers.PROVIDERS["openai"]
    said = wizard._explain(spec, ValueError("kaboom"), "sk-x")
    assert "network" not in said and "kaboom" not in said
    assert "log" in said, "nothing pointed at where the detail actually is"


def test_a_failure_before_the_request_leaves_does_not_blame_the_provider_or_the_key():
    """Measured under a simulated Windows: a dependency that imports differently there raised inside
    the probe and the screen said something went wrong on the way to OpenAI — for a call that never
    left the machine. It sends somebody to make a second key, or to re-type one that was fine."""
    spec = providers.PROVIDERS["openai"]
    local = (ModuleNotFoundError("No module named 'x'"), AttributeError("nope"), ValueError("k"),
             # The OSError family is the shape our own faults take. A CA bundle named by SSL_CERT_FILE
             # that is not there raises while the client is being BUILT, and the network branch caught
             # it first — sending somebody to inspect a firewall over a missing file.
             FileNotFoundError(2, "No such file or directory", "/etc/ssl/absent.pem"),
             PermissionError(13, "Permission denied"))
    for exc in local:
        said = wizard._explain(spec, exc, "sk-x")
        assert "on the way to" not in said, said
        assert "not your key" in said, said
        assert "log" in said

    class _Answered(Exception):
        status_code = 404
        body = {"error": {"code": "model_not_found", "param": "model", "message": "no"}}

    for network in (OSError("no route to host"), ConnectionRefusedError(111, "refused")):
        said = wizard._explain(spec, network, "sk-x")
        assert "network on this machine" in said, "a real network fault stopped reading as one"

    import openai

    real = openai.APIError
    try:
        openai.APIError = _Answered  # the provider DID answer: the model branch must still win
        assert "it's the model" in wizard._explain(spec, _Answered(), "sk-x")
    finally:
        openai.APIError = real


@pytest.mark.parametrize("spec,exc,key", _failures())
def test_a_page_she_names_is_always_the_last_thing_in_the_sentence(spec, exc, key):
    """A URL too wide for the window is lifted onto a row of its own (`markdown.lift_urls`), so a URL
    written mid-sentence leaves the sentence behind it: `Top it up at and this same key will work`,
    measured on a 52-column terminal."""
    said = wizard._explain(spec, exc, key)
    if "http" in said:
        assert said.split()[-1].startswith("http"), said


def test_every_url_she_reads_out_ends_its_sentence():
    """The same rule for the copy she speaks, not only the translated failures.

    Read off the SOURCE, not the module constants: half of what she says is built in an f-string
    inside a function, and a URL with a full stop welded to it went in that way — the constants this
    used to walk could never have seen it. `test_a_failure_is_translated...` states the rule; a URL
    is allowed the last position precisely because punctuation on the end breaks click-to-open."""
    source = pathlib.Path(wizard.__file__).read_text(encoding="utf-8")
    welded = re.findall(r"\{[a-z_.]*url[a-z_.]*\}[.,;:!?]|https?://\S*[a-zA-Z0-9/][.,;:]", source)
    assert not welded, f"punctuation welded to a URL she prints: {welded}"


def test_the_translation_keeps_the_page_where_a_key_is_made():
    """The one part of the provider's own 401 that was worth reading is the part the 160-character
    truncation removed."""
    spec = providers.PROVIDERS["openai"]
    exc = AuthenticationError("x", response=httpx.Response(401, request=REQUEST),
                              body={"error": {"code": "invalid_api_key"}})
    assert spec.key_url in wizard._explain(spec, exc, "sk-nope")


def test_no_credit_is_not_reported_as_a_bad_key():
    """A well-formed key with an empty account answers 429, and telling that person their key is wrong
    sends them to make a second one that will fail the same way."""
    spec = providers.PROVIDERS["openai"]
    exc = RateLimitError("quota", response=httpx.Response(429, request=REQUEST),
                         body={"error": {"code": "insufficient_quota"}})
    said = wizard._explain(spec, exc, "sk-x")
    assert "credit" in said and spec.credit_url in said
    assert "The key is real" in said


def test_a_key_belonging_to_the_other_provider_is_named_as_such():
    spec = providers.PROVIDERS["openai"]
    exc = AuthenticationError("x", response=httpx.Response(401, request=REQUEST), body=None)
    said = wizard._explain(spec, exc, "xai-abc")
    assert "xAI" in said and "pick" in said


@pytest.mark.parametrize("spec", list(providers.PROVIDERS.values()))
def test_a_provider_is_never_referred_to_with_the_wrong_article(spec):
    """Every provider label so far starts on a letter that is read aloud with a leading vowel, so
    `a OpenAI key` and `a xAI key` are both wrong, and both were written before this existed."""
    other = next(o for o in providers.PROVIDERS.values() if o.id != spec.id)
    said = wizard._looks_wrong(spec, other.key_prefix_hint + "abc")
    assert f"a {other.label}" not in said and f"an {other.label}" in said


def test_nothing_the_wizard_prints_ever_contains_the_key(monkeypatch, tmp_path):
    answers = Answers(typed=["1", "", "", ""], keys=["sk-super-secret-value-9f3a"])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert "sk-super-secret-value-9f3a" not in out
    assert "9f3a" not in out


def test_the_install_with_no_renderer_still_gets_the_whole_thing(monkeypatch, tmp_path):
    """`kotoba setup` is reachable from a bare `pip install kotoba`, and it is the command that install
    most needs. rich, prompt_toolkit and pillow are the `cli` extra, so the chrome is optional and the
    words are not — the plain twin is fed the same strings and writes the same rows.

    The two render modules are dropped from `sys.modules` so the guard is reached at all: an import
    already served from the cache never runs its own import statements, and this test would then
    measure nothing."""
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    real_import = builtins.__import__

    def without_the_extra(name, *a, **k):
        if name.split(".")[0] in ("rich", "prompt_toolkit", "PIL"):
            raise ImportError("No module named " + name)
        return real_import(name, *a, **k)

    async def accept(provider_id, key):
        return True, ""

    monkeypatch.setattr(wizard, "_verify", accept)
    for cached in ("kotoba.cli.render.caps", "kotoba.cli.render.first_run"):
        monkeypatch.delitem(sys.modules, cached, raising=False)
    monkeypatch.setattr(builtins, "__import__", without_the_extra)
    answers = Answers(typed=["1", "", "Jordan", "Yuki", "es"], keys=["sk-good"])

    async def go():
        db = Database("sqlite:///" + (tempfile.mkdtemp() + "/kotoba.db"))
        await db.connect()
        await sync_from_file(db, "./soul/default.md")
        ok = await wizard.run(db, ask=answers.ask, ask_secret=answers.secret)
        soul = await db.fetch_soul_config()
        profile = await db.fetch_user_profile_as_markdown()
        await db.close()
        return ok, soul, profile

    ok, soul, profile = asyncio.run(go())
    assert ok is True
    assert soul["name"] == "Yuki" and soul["language"] == "es" and "name: Jordan" in profile


# --- her voice: the one question whose best answer is often no -------------------------------------

def _voice_key(db):
    from kotoba.core.engine import VOICE_KEY_NAME

    return read(db, lambda db: db.get_key(VOICE_KEY_NAME))


def test_the_voice_key_is_asked_for_stored_encrypted_and_made_live(monkeypatch, tmp_path):
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", "", raising=False)

    async def voice_ok(key):
        return True, ""

    monkeypatch.setattr(voice_key, "verify", voice_ok)
    answers = Answers(typed=["1", "", "", ""], keys=["sk-good", "sk_a_real_voice_key"])
    ok, out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is True
    assert _voice_key(db) == "sk_a_real_voice_key"
    assert voice_config.resolve_api_key() == "sk_a_real_voice_key", \
        "the key was stored where nothing reads it"
    assert "sk_a_real_voice_key" not in out


def test_skipping_the_voice_is_a_choice_and_leaves_a_working_install(monkeypatch, tmp_path):
    """Text is a complete way to use her — `doctor` says so in the same words — so a stranger with no
    ElevenLabs account has to be able to finish this and get a companion."""
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", "", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    answers = Answers(typed=["1", "", "", ""], keys=["sk-good", ""])
    ok, out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is True
    assert _voice_key(db) is None
    assert "reads and writes" in out
    assert "kotoba setup" in out, "nothing said how to come back for the voice"


def test_the_prompt_advertises_the_skip_before_it_is_needed(monkeypatch, tmp_path):
    """A person who has never heard of ElevenLabs must be able to see the way past this without
    guessing that an empty line is allowed."""
    answers = Answers(typed=["1", "", "", ""], keys=["sk-good", ""])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    rows = [line for line in out.splitlines() if "ElevenLabs key" in line and "›" in line]
    assert rows and "Enter skips" in rows[0], f"the prompt row does not offer the skip: {rows}"


def test_a_key_already_in_the_environment_is_never_asked_for_again(monkeypatch, tmp_path):
    """The same rule `needed()` applies to the LLM key. Asked anyway, the wizard would then tell someone
    who has a perfectly good voice that she reads and writes — the closing line and the truth apart.

    This run borrows the app's screen, so the closing asserted below is the handover one; the clause
    that offers the voice again is the half that must be absent when she already has it."""
    tried = []

    async def never(key):
        tried.append(key)
        return True, ""

    monkeypatch.setattr(voice_key, "verify", never)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_already_in_the_environment")
    answers = Answers(typed=["1", "", "", ""], keys=["sk-good"])
    ok, out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is True and tried == []
    assert _voice_key(db) is None, "a key it was never given was saved"
    assert "already done" in out and "reads and writes" not in out
    assert "adds the voice" not in out, "she has a voice and the closing offered to add one"


def test_a_voice_key_the_service_refuses_asks_again_and_then_lets_it_go(monkeypatch, tmp_path):
    tried = []

    async def refuse(key):
        tried.append(key)
        return False, "ElevenLabs doesn't recognise that key."

    monkeypatch.setattr(voice_key, "verify", refuse)
    answers = Answers(typed=["1", "", "", ""], keys=["sk-good", "a", "b", "c"])
    ok, out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is True, "a bad OPTIONAL key must not fail the whole install"
    assert tried == ["a", "b", "c"]
    assert _voice_key(db) is None
    assert "Going without a voice" in out


def test_the_placeholder_voice_key_is_caught_without_a_round_trip(monkeypatch, tmp_path):
    tried = []

    async def never(key):
        tried.append(key)
        return True, ""

    monkeypatch.setattr(voice_key, "verify", never)
    answers = Answers(typed=["1", "", "", ""], keys=["sk-good", "el_...", ""])
    _ok, out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert tried == [], "the placeholder was sent to ElevenLabs"
    assert _voice_key(db) is None
    assert "placeholder" in out


def test_doctor_stops_asking_for_a_key_once_setup_has_given_it_one(monkeypatch, tmp_path):
    """`doctor` tells the reader twice to fix the missing voice key by running `kotoba setup`. That
    sentence was false until this step existed, and this is what keeps it true."""
    from kotoba.cli import doctor
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", "", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    before = doctor._voice_key()
    assert before.status == "warn" and "kotoba setup" in before.detail

    async def voice_ok(key):
        return True, ""

    monkeypatch.setattr(voice_key, "verify", voice_ok)
    answers = Answers(typed=["1", "", "", ""], keys=["sk-good", "sk_a_real_voice_key"])
    run_wizard(monkeypatch, tmp_path, answers)
    after = doctor._voice_key()
    assert after.status == "ok" and "saved in the app" in after.detail


def test_the_voice_check_never_asks_the_endpoint_that_lies(monkeypatch):
    """`/v1/user` is what every guide reaches for and it is the wrong question: ElevenLabs keys carry
    scopes, and a key that speaks perfectly answers it 401 `missing the permission user_read` (measured
    against a live working key). Reaching for it again would report a good key as bad."""
    source = pathlib.Path(voice_key.__file__).read_text(encoding="utf-8")
    called = re.findall(r"api\.elevenlabs\.io(/v1/[\w/]+)", source)
    assert called == ["/v1/voices"], f"the voice check asks {called}"


@pytest.mark.parametrize("status,body,keeps", [
    (200, {}, True),
    (401, {"detail": {"status": "invalid_api_key"}}, False),
    (401, {"detail": {"status": "missing_permissions"}}, True),
    (403, {"detail": {"status": "missing_permissions"}}, True),
    (429, {"detail": {"status": "too_many_requests"}}, False),
    (503, {}, True),
])
def test_only_elevenlabs_own_invalid_key_verdict_rejects_a_key(monkeypatch, status, body, keeps):
    class Reply:
        status_code = status

        def json(self):
            return body

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return Reply()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Client())
    ok, said = asyncio.run(voice_key.verify("sk_whatever"))
    assert ok is keeps
    assert "sk_whatever" not in said
    for leak in ("Error code", "401", "403", "429", "503", "{'"):
        assert leak not in said


def test_a_voice_key_that_cannot_be_checked_is_kept_rather_than_lost(monkeypatch):
    """This step is optional; blocking an install on a network blip would be the worse failure."""
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise OSError("no route to host")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Client())
    ok, said = asyncio.run(voice_key.verify("sk_whatever"))
    assert ok is True and "couldn't reach" in said


# --- the round trip has to ask for a model the provider serves -------------------------------------

def test_choosing_xai_moves_the_model_off_the_openai_default(monkeypatch, tmp_path):
    """`model` is one global setting. Left on gpt-5.4-mini, the verification call to api.x.ai comes
    back 404 and a perfectly good xai- key is reported as a bad key."""
    answers = Answers(typed=["2", "", "", ""], keys=["xai-good"])
    _ok, _out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    from kotoba.core import llm

    assert llm.model_name("companion") == providers.PROVIDERS["xai"].default_model
    assert providers.serves_model(llm.model_name("companion"), "xai")


def test_a_model_the_person_chose_themselves_is_not_overwritten(monkeypatch, tmp_path):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    app_settings.set_runtime("model", "grok-4.5")
    answers = Answers(typed=["2", "", "", ""], keys=["xai-good"])
    run_wizard(monkeypatch, tmp_path, answers)
    from kotoba.core import llm

    assert llm.model_name("companion") == "grok-4.5"


# --- how it looks, and what it refuses to do to the window -----------------------------------------

@pytest.mark.parametrize("width", (40, 62, 96, 200))
def test_no_row_overflows_the_window(monkeypatch, tmp_path, width):
    async def refuse(provider_id, key):
        return False, ("OpenAI doesn't recognise that key. A stray space often comes along with a "
                       "paste — otherwise make a fresh one at https://platform.openai.com/api-keys")

    answers = Answers(typed=["9", "1", "", "Jordan", "Yuki", "zz9"], keys=["sk-bad", "sk-good"])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers, verify=refuse,
                                       caps=a_caps(width=width))
    from rich.cells import cell_len

    long = [line for line in out.splitlines() if cell_len(line) > width]
    assert not long, f"{len(long)} row(s) ran off a {width}-column window: {long[:2]}"


@pytest.mark.parametrize("width", (40, 62))
def test_the_key_page_url_is_never_indented_off_a_narrow_window(monkeypatch, tmp_path, width):
    """The test above never reaches this screen — its stub refuses every key, so the wizard leaves
    four questions before the voice one, and the widest string in the whole wizard is on it.

    The ElevenLabs key page is 43 cells. Her three-cell gutter made the row 46 on a forty-column
    window: three cells the renderer added to a string already too long for the screen it has to be
    read off. Nothing can fit 43 cells into 40 — what can be fixed is us making it worse."""
    from rich.cells import cell_len

    from kotoba.core.voice_key import VOICE_KEY_URL

    answers = Answers(typed=["1", "", "Jordan", "Yuki", "es"], keys=["sk-good"])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers, caps=a_caps(width=width))
    row = next(line for line in out.splitlines() if VOICE_KEY_URL in line)
    assert row.strip() == VOICE_KEY_URL, "the URL is sharing its row with something"
    assert cell_len(row) <= max(width, cell_len(VOICE_KEY_URL)), (
        f"the renderer added {cell_len(row) - cell_len(VOICE_KEY_URL)} cells of its own to a URL "
        f"already {cell_len(VOICE_KEY_URL)} wide on a {width}-column window")
    assert (row == VOICE_KEY_URL) is (width == 40), "the gutter is given up only when it must be"


def test_the_terminal_that_asked_for_ascii_gets_no_unicode_chrome(monkeypatch, tmp_path):
    answers = Answers(typed=["1", "", "Jordan", "Yuki", "es"], keys=["sk-good"])
    caps = a_caps(unicode=False, g=dict(GLYPHS_ASCII))
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers, caps=caps)
    for glyph in ("言", "✓", "›", "—", "·", "█", "…"):
        assert glyph not in out, f"{glyph!r} survived --ascii"
    assert "K KOTOBA" in out, "the sigil folded but her plate went with it"


def test_the_banner_states_nothing_it_cannot_yet_know(monkeypatch, tmp_path):
    """`● LIVE` and `you're connected — just type` are claims about a session that does not exist, and
    the stat grid would name a model no key can reach."""
    answers = Answers(typed=["1"], keys=[""])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers,
                                       caps=a_caps(interactive=True, color="16"))
    head = out.split("First:")[0]
    assert "LIVE" not in head and "just type" not in head
    assert "MODEL" not in head and "TOOLS" not in head
    assert "She talks." in head, "her tagline is true before anything else is"


def test_her_plate_takes_the_name_she_was_given():
    """Renaming her in a wizard that then keeps calling her KOTOBA would be a control doing half its
    job — the exact shape of an earlier bug (Settings → Language)."""
    screen = a_screen(a_caps(), io.StringIO())
    assert "KOTOBA" in screen.plate(face=False).plain
    screen.called("Yuki")
    assert "YUKI" in screen.plate(face=False).plain
    screen.called("")
    assert "KOTOBA" in screen.plate(face=False).plain, "no name falls back to the one she ships with"


def test_a_very_long_name_cannot_push_the_header_around():
    screen = a_screen(a_caps(), io.StringIO())
    screen.called("A" * 90)
    from rich.cells import cell_len

    assert cell_len(screen.plate(face=False).plain) <= 22


def test_band_never_claims_a_voice_without_a_key(monkeypatch):
    """The header row said `local · expressive` on a machine with no ElevenLabs key — one screen after
    the wizard told the person she has no voice. Both modes need a key, so neither is true without one."""
    from kotoba.cli import facts

    runtime = {"voice_mode": "local", "tts_engine": "expressive"}
    monkeypatch.setattr("kotoba.core.voice.config.resolve_api_key", lambda: "")
    assert facts._voice(runtime) == "text only · no key"

    monkeypatch.setattr("kotoba.core.voice.config.resolve_api_key", lambda: "sk_real")
    assert facts._voice(runtime) == "local · expressive"


def test_language_confirms_by_name_not_by_code():
    """Picking `3 Spanish` from a list of names answered `es it is` — the stored value read back as if
    it were the answer. A typed code has no name to give and stays as typed."""
    from kotoba.cli import wizard

    assert [row[1] for row in wizard.LANGUAGES] == ["auto", "English", "Spanish", "Japanese"]
    src = pathlib.Path(wizard.__file__).read_text(encoding="utf-8")
    assert "f\"{label} it is" in src and "f\"{code} it is" not in src


# --- a stranger's terminal, and a stranger's name --------------------------------------------------

def test_the_header_survives_an_install_without_the_voice_extra(monkeypatch):
    """`core.voice.__init__` eagerly pulls in stt/tts, which need `websockets` — an extra that
    `pip install -e "api/[cli]"` does not bring, and that command is the documented one.
    Imported bare, the VOICE row took the whole interactive terminal down with a ModuleNotFoundError
    at startup: `doctor`, `core.engine` and `core.voice_key` all guard this import for that reason."""
    from kotoba.cli import facts

    real_import = builtins.__import__

    def without_websockets(name, *a, **k):
        if name.split(".")[0] == "websockets":
            raise ImportError("No module named 'websockets'")
        return real_import(name, *a, **k)

    for cached in ("kotoba.core.voice", "kotoba.core.voice.stt", "kotoba.core.voice.tts"):
        monkeypatch.delitem(sys.modules, cached, raising=False)
    monkeypatch.setattr(builtins, "__import__", without_websockets)
    said = facts._voice({"voice_mode": "local", "tts_engine": "expressive"})
    assert said and "local" not in said, "the header claimed a voice mode on an install with no voice"


def test_the_first_ctrl_c_is_the_one_that_stops_her():
    """`asyncio.Runner` points SIGINT at cancelling the main task, and this task is blocked in a
    synchronous read: the first press reached nobody, and the cancellation it left behind detonated at
    the next await — `db.save_key`, straight after a good key was accepted. Measured on the binary: a
    traceback over her first-run screen and the key not stored. The handler in force while she waits
    has to be the one that raises."""
    seen = {}

    def ask(_prompt: str = "") -> str:
        seen["during"] = signal.getsignal(signal.SIGINT)
        raise KeyboardInterrupt

    async def go():
        seen["before"] = signal.getsignal(signal.SIGINT)
        answer = wizard._read(wizard._Plain(), ask, "which one?")
        seen["after"] = signal.getsignal(signal.SIGINT)
        return answer

    assert asyncio.run(go()) is None
    assert seen["before"] is not signal.default_int_handler, "the runner never installed its own"
    assert seen["during"] is signal.default_int_handler, "Ctrl+C was still being deferred"
    assert seen["after"] is seen["before"], "the runner's handler was not put back"


def test_an_interrupted_setup_exits_instead_of_printing_a_traceback(monkeypatch):
    """`--once` and the session both catch it; `setup`, `doctor` and `serve` never did, and `setup` is
    the one command a stranger runs first."""
    from kotoba.cli import __main__ as entry
    from kotoba.cli import doctor

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(entry.logs, "to_file", lambda: None)
    monkeypatch.setattr(entry, "_setup", interrupted)
    monkeypatch.setattr(doctor, "run", interrupted)
    for command in ("setup", "doctor"):
        try:
            code = entry.main([command])
        except KeyboardInterrupt:
            pytest.fail(f"Ctrl+C during `kotoba {command}` reached the top level as a traceback")
        assert code == 130


def test_a_name_with_an_accent_survives_a_terminal_that_cannot_spell_it(monkeypatch, tmp_path):
    """`LC_ALL=C` gives stdin an ascii decoder with `errors="surrogateescape"`, so `José` comes back
    from `input()` as surrogates — and SQLite refuses to store one. Measured on the binary: first run
    ended on a UnicodeEncodeError traceback with the key already saved. Re-encoding recovers the real
    text, because the surrogates are the original bytes."""
    answers = Answers(typed=["1", "", "Jos\udcc3\udca9", "", ""], keys=["sk-good"])
    ok, out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is True
    assert "name: José" in read(db, lambda db: db.fetch_user_profile_as_markdown())
    assert "José" in out


def test_backing_out_at_the_key_leaves_the_brain_where_it_was(monkeypatch, tmp_path):
    """`model` is written the moment it is answered; `provider` only when a key is accepted. Backing
    out between the two left `provider: openai` beside `model: grok-4.3` — an install where every turn
    comes back 404, made by pressing Enter on a screen that said nothing is saved yet."""
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    from kotoba.core import llm

    app_settings.set_runtime("provider", "openai")
    app_settings.set_runtime("model", "gpt-5.4-mini")
    answers = Answers(typed=["2", "1"], keys=[""])
    ok, _out, _db, calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is False and calls == [], "an empty key was spent on a round trip"
    assert providers.active_provider_id() == "openai"
    assert llm.model_name("companion") == "gpt-5.4-mini", "her brain was moved and left without a key"


def test_she_never_claims_a_voice_this_install_cannot_perform(monkeypatch, tmp_path):
    """`voice_key.configured()` says False for a missing extra and a missing key alike, so a key
    accepted on a `kotoba[cli]` install got `I can hear you and speak` and then a closing that sent the
    person back to `kotoba setup` — two sentences, both untrue, and neither naming the missing piece."""
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", "", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(wizard, "_voice_runtime", lambda: False)

    async def voice_ok(key):
        return True, ""

    monkeypatch.setattr(voice_key, "verify", voice_ok)
    answers = Answers(typed=["1", "", "", "", ""], keys=["sk-good", "el-good"])
    _ok, out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert _voice_key(db) == "el-good", "the key was refused rather than kept"
    voice_step = out[out.index("genuinely optional"):]
    assert "I can hear you and speak" not in voice_step, "she promised a voice she cannot perform"
    assert voice_step.count(f'{DIST_NAME}[voice]') >= 2, "neither the question nor the closing named the gap"


@pytest.mark.parametrize("typed,stored,said", [
    ("auto", "auto", "between languages"),
    ("Spanish", "es", "Spanish it is"),
    ("es", "es", "Spanish it is"),
    ("3", "es", "Spanish it is"),
    ("JAPANESE", "ja", "Japanese it is"),
])
def test_a_language_can_be_answered_by_anything_the_row_shows(monkeypatch, tmp_path, typed, stored, said):
    """The list prints `auto`, `English`, `Spanish` and typing what you can see was refused — `auto` is
    four letters, so the two-or-three letter code rule could never have taken it. A typed code answered
    `es it is`, which is the stored value read back as if it were the answer."""
    answers = Answers(typed=["1", "", "", "", typed], keys=["sk-good"])
    _ok, out, db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert read(db, lambda db: db.fetch_soul_config())["language"] == stored
    assert said in out


@pytest.mark.parametrize("step,typed", [("model", "8"), ("language", "5")])
def test_the_escape_row_is_never_refused_with_a_range_that_excludes_it(monkeypatch, tmp_path,
                                                                       step, typed):
    """Both lists number a last row — `something else` — and answering with its number was met with
    `a number from 1 to 7`, i.e. a range that leaves out the row printed underneath it."""
    seat = {"model": ["1", typed, "1"], "language": ["1", "", "", "", typed, "2"]}[step]
    answers = Answers(typed=seat, keys=["sk-good"])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert "type the" in out.split(f"`{typed}`")[-1] or "id itself" in out or "code itself" in out
    assert f"I don't have a `{typed}`" not in out and f"I don't know `{typed}`" not in out


def test_the_first_screen_counts_the_questions_it_is_about_to_ask(monkeypatch, tmp_path):
    """A step was inserted between the brain and the key and the opening line still promised the old
    number. The count is the first thing she says and the easiest thing to leave behind."""
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", "", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    answers = Answers(typed=["1", "", "", "", ""], keys=["sk-good", ""])
    run_wizard(monkeypatch, tmp_path, answers)
    words = {"Two": 2, "Three": 3, "Four": 4, "Five": 5, "Six": 6, "Seven": 7, "Eight": 8}
    promised = re.search(r"(\w+) questions and one you can skip", wizard.HELLO)
    assert promised, f"the opening no longer states a count: {wizard.HELLO!r}"
    assert words[promised.group(1)] == len(answers.asked) + len(answers.pasted) - 1


def test_the_opening_never_promises_an_enter_that_answers_instead_of_leaving():
    """`Enter on an empty line stops this` was true at exactly one of the seven prompts. At the very
    next one Enter picks OpenAI, and the hint beside it says so."""
    assert "Enter" not in wizard.LEAVING, wizard.LEAVING
    assert "Ctrl+C" in wizard.LEAVING


def test_the_skip_is_still_advertised_on_a_narrow_window(monkeypatch, tmp_path):
    """`[Enter skips]` is thirteen cells and it was the only twin, so at forty columns the one prompt
    that can be skipped stopped saying so."""
    answers = Answers(typed=["1", "", "", "", ""], keys=["sk-good"])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers, caps=a_caps(width=40))
    row = next(line for line in out.splitlines() if "what I should call you" in line)
    assert "skip" in row, row


@pytest.mark.parametrize("flag,field", [("--plain", "plain"), ("--ascii", "ascii"),
                                        ("--calm", "calm"), ("--no-face", "no_face")])
@pytest.mark.parametrize("order", ("before", "after"))
def test_the_render_flags_are_taken_on_either_side_of_the_subcommand(flag, field, order):
    """These are documented as working on `kotoba setup`, and the setup script passes them after the
    subcommand — where argparse rejected every one of them."""
    from kotoba.cli.__main__ import _parse

    argv = [flag, "setup"] if order == "before" else ["setup", flag]
    assert getattr(_parse(argv), field) is True


@pytest.mark.parametrize("command,flag,honoured", [
    ("doctor", "--plain", True), ("doctor", "--ascii", True),
    ("doctor", "--calm", False), ("doctor", "--no-face", False),
    ("serve", "--plain", False), ("serve", "--ascii", False),
    ("serve", "--calm", False), ("serve", "--no-face", False),
])
@pytest.mark.parametrize("order", ("before", "after"))
def test_a_render_flag_is_acted_on_or_refused_by_name_and_never_swallowed(
        monkeypatch, capsys, command, flag, honoured, order):
    """`kotoba --plain doctor` parsed the flag and then called `doctor.run()` with nothing at all:
    accepted and thrown away, the exact defect `kotoba setup --ascii` was fixed for once already.

    The verdict may not depend on which side it was typed either, so both orders are driven. `doctor`
    ACTS on the two it can — it paints a status column and writes em dashes — and refuses the two it
    cannot; `serve` refuses all four and says why.

    `serve.run` is stubbed, and not only for speed: the real one spawns uvicorn and `npm run dev`.
    `logs.to_file` is stubbed because `main` redirects the child streams on its way past — this test
    is about routing, not about whether the wrap survives."""
    from kotoba.cli import doctor, serve
    from kotoba.cli.__main__ import main
    from kotoba.core import logs

    got: list = []
    monkeypatch.setattr(doctor, "run", lambda *a, **k: got.append(k) or 0)
    monkeypatch.setattr(serve, "run", lambda *a, **k: got.append(k) or 0)
    monkeypatch.setattr(logs, "to_file", lambda *a, **k: None)

    argv = [flag, command] if order == "before" else [command, flag]
    code = main(argv)
    err = capsys.readouterr().err
    if honoured:
        field = {"--plain": "plain", "--ascii": "ascii_only"}[flag]
        assert code == 0 and got and got[0][field] is True, f"{argv} reached {command} without it"
    else:
        assert code == 2, f"{argv} was accepted"
        assert not got, f"{command} ran anyway with a flag it cannot act on"
        assert flag in err and "nothing on `kotoba " + command in err, err
        assert all(ord(c) < 128 for c in err), "the line refusing --ascii may not itself need it"


@pytest.mark.parametrize("argv,folded", [(["--ascii", "--once", "hola"], True),
                                         (["--once", "hola", "--ascii"], True),
                                         (["--once", "hola"], False)])
def test_ascii_reaches_her_words_on_once_and_not_only_the_wizards_chrome(monkeypatch, capsys,
                                                                        argv, folded):
    """`--ascii` was parsed on `--once`, handed to first run, and then dropped on the one thing that
    command exists to print. `kotoba --ascii --once` came back with the em dashes, `·` and `→` that
    `kotoba doctor --ascii` folds — on a terminal that had just said it cannot draw them, and against
    the documented promise that none of them is ever accepted and then ignored.

    The fold is the TABLE and nothing else, so `aquí` keeps its accent: a catch-all that turned every
    remaining non-ASCII character into `?` is the defect `ascii_fold.fold` was written against.

    `logs.to_file` is stubbed because `main` redirects the child streams on its way past, and that wraps
    `mcp.stdio_client` for the whole process. This test is about what reaches stdout."""
    from kotoba.cli import __main__ as entry
    from kotoba.cli import session as cli_session
    from kotoba.core import logs

    async def fake_loop(items, sid, db, stream, patterns, **kw):
        await stream.put("Listo — aquí está: café · 5 → 6")

    async def no_extract(user_msg, db):
        return None

    monkeypatch.setattr(cli_session, "agentic_loop", fake_loop)
    monkeypatch.setattr(cli_session, "extract_and_save_memory", no_extract)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(logs, "to_file", lambda *a, **k: None)

    assert entry.main(argv) == 0
    out = capsys.readouterr().out
    assert "aquí está: café" in out, out
    if folded:
        assert out.strip() == "Listo -- aquí está: café . 5 -> 6", out
    else:
        assert out.strip() == "Listo — aquí está: café · 5 → 6", out


def test_every_subcommand_that_can_refuse_a_flag_has_a_reason_to_give():
    """The refusal reads its reason out of a second table, so a subcommand added to one and not the
    other would refuse a flag by raising KeyError at the person who typed it."""
    from kotoba.cli.__main__ import _FLAGS, _HONOURED, _NOTHING_TO_ACT_ON

    for command, honoured in _HONOURED.items():
        if set(_FLAGS) - set(honoured):
            assert command in _NOTHING_TO_ACT_ON, f"`{command}` would refuse a flag with a traceback"


def test_doctor_paints_by_default_and_stops_when_plain_asks_it_to(monkeypatch):
    """The colour is real, which is why `--plain` is made to WORK on `doctor` rather than refused."""
    from kotoba.cli import doctor

    monkeypatch.setattr(doctor, "collect", _one_check)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert "\033[32m" in _doctor_says(doctor)
    assert "\033[" not in _doctor_says(doctor, plain=True)


def test_doctor_writes_em_dashes_which_is_why_ascii_is_made_to_work_there(monkeypatch):
    from kotoba.cli import doctor

    monkeypatch.setattr(doctor, "collect", _one_check)
    assert "—" in _doctor_says(doctor)
    said = _doctor_says(doctor, ascii_only=True)
    assert "—" not in said and "-- regex file search" in said


async def _one_check():
    from kotoba.cli.doctor import Check

    return [("on this machine", [Check("ripgrep", "ok", "ripgrep 15.1.0 — regex file search")])]


def _doctor_says(doctor, **flags) -> str:
    """The report as a terminal receives it: `_paint` asks the stream whether it is a tty."""
    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    out = _Tty()
    doctor.run(out, **flags)
    return out.getvalue()


def test_only_the_cheapest_model_is_called_the_cheapest(monkeypatch, tmp_path):
    """`cost_hint` prints no ratio under 1.05x, and the confirmation read that silence as `the least
    they charge me` — said of a model that is not the cheapest row on the screen it was picked from."""
    spec = providers.PROVIDERS["openai"]
    floor = min(spec.models, key=lambda m: m.output_cost)
    near = next(m for m in spec.models if m.id != floor.id and not providers.cost_hint(spec, m))
    answers = Answers(typed=["1", str(spec.models.index(near) + 1), "", "", ""], keys=["sk-good"])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    said = next(line for line in out.splitlines() if f"{near.id} it is" in line)
    assert "the least" not in said, said


def test_the_header_never_names_a_model_no_key_can_reach(monkeypatch):
    """A declined first run hands the session straight to its own banner, and that banner said
    `MODEL gpt-5.6-luna · OpenAI` two rows under `All right — I'll be here`. The VOICE row was fixed
    for this exact reason; MODEL is the same claim about the same missing key."""
    from kotoba.cli import facts
    from kotoba.core import llm, providers

    monkeypatch.setattr(llm, "get_client", lambda: None)
    said = facts._model(llm, providers)
    assert providers.get_spec().label in said and "no key" in said
    assert llm.model_name() not in said, "she named a model with nothing to reach it"

    monkeypatch.setattr(llm, "get_client", lambda: object())
    assert llm.model_name() in facts._model(llm, providers)


def test_a_key_in_the_environment_is_still_not_a_voice_without_the_extra(monkeypatch, tmp_path):
    """The other half of the same claim: `voice_key.configured()` is True for an env key on an install
    that has nothing to speak it with, and the screen went straight to `you can talk to me`."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_already_in_the_environment")
    monkeypatch.setattr(wizard, "_voice_runtime", lambda: False)
    answers = Answers(typed=["1", "", "", "", ""], keys=["sk-good"])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert "already done" in out, "she asked for a key the environment already has"
    assert "answer out loud" not in out, "she promised a voice this install cannot perform"
    assert f'{DIST_NAME}[voice]' in out


# --- the model she was handed, and the model the provider will not serve ---------------------------

def _client_that_raises(exc):
    class _Responses:
        async def create(self, **_kw):
            raise exc

    class _Client:
        responses = _Responses()

    return _Client()


def _unknown_model_400(model: str = "pizza"):
    """What api.openai.com actually answers for a model it does not have — measured live against a
    working key: 400, not the 404 `_explain` was written to look for."""
    from openai import BadRequestError

    return BadRequestError("x", response=httpx.Response(400, request=REQUEST), body={"error": {
        "message": f"The requested model '{model}' does not exist.",
        "type": "invalid_request_error", "param": "model", "code": "model_not_found"}})


def test_an_id_she_does_not_know_is_not_confirmed_as_if_she_did(monkeypatch, tmp_path):
    """The free-text escape takes anything the OTHER provider does not claim, so `pizza` was accepted
    — and answered with the same tick and the same mint as a model off the list. A person cannot tell
    a typo from a choice, and the next screen blames their key for it."""
    answers = Answers(typed=["1", "pizza", "", "", ""], keys=["sk-good"])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    row = next(line for line in out.splitlines() if "pizza" in line and "which one" not in line)
    assert not row.lstrip().startswith("✓"), f"an unknown id was ticked as if it were checked: {row}"
    assert "know" in row, f"nothing said she does not recognise it: {row}"
    from kotoba.core import llm

    assert llm.model_name("companion") == "pizza", "she refused it instead of taking their word"


@pytest.mark.parametrize("exc", [_unknown_model_400(),
                                 pytest.param(None, id="bare-404")])
def test_a_model_the_provider_will_not_serve_is_never_reported_as_a_bad_key(exc, monkeypatch):
    """Measured live: a real, working key with `model: pizza` came back
    `OpenAI turned that key down and didn't say anything I can put simply`, so a person is sent to
    make a second key that will fail the same way. The status is not the tell — 400 here, 404 on the
    older endpoint — so the body is what has to decide it."""
    from openai import NotFoundError

    monkeypatch.setattr("kotoba.core.llm.model_name", lambda role="companion": "pizza")
    if exc is None:
        exc = NotFoundError("x", response=httpx.Response(404, request=REQUEST), body=None)
    said = wizard._explain(providers.PROVIDERS["openai"], exc, "sk-a-perfectly-good-key")
    assert "pizza" in said, f"the model she cannot use was never named: {said}"
    assert "turned that key down" not in said and "recognise that key" not in said, said
    assert "kotoba setup" in said, f"no way back to the list: {said}"
    assert "/set model" not in said, "first run has no session to run a slash command in"


def test_a_400_that_blames_another_field_is_not_pinned_on_the_model():
    """The 400 rule reads `the only thing the person chose is the model`, and that holds only while the
    provider is not naming a different field — one of ours being wrong is not their model being wrong,
    and neither is it their key."""
    from openai import BadRequestError

    exc = BadRequestError("x", response=httpx.Response(400, request=REQUEST), body={"error": {
        "message": "max_output_tokens must be at least 16.", "type": "invalid_request_error",
        "param": "max_output_tokens", "code": None}})
    said = wizard._explain(providers.PROVIDERS["openai"], exc, "sk-good")
    assert "it's the model" not in said, said
    assert "turned that key down" not in said, "our own bad request read as their bad key"
    assert "log" in said


def test_the_whole_answer_is_where_she_says_it_is(monkeypatch, caplog):
    """Two branches end with `The whole answer is in the log`. It was written at DEBUG and
    `core.logs.to_file` configures INFO, so the file held the httpx request line and nothing else —
    measured on a live run. A sentence that names a file has to put something in it."""
    import logging

    monkeypatch.setattr(wizard.llm, "get_client",
                        lambda: _client_that_raises(ValueError("kaboom in the provider")))
    with caplog.at_level(logging.DEBUG, logger="kotoba.cli"):
        ok, said = asyncio.run(wizard._verify("openai", "sk-whatever"))
    assert ok is False and "in the log" in said
    kept = [r for r in caplog.records if r.name == "kotoba.cli" and r.levelno >= logging.INFO]
    assert kept, "she named a log the default level writes nothing to"


def test_the_log_she_names_never_receives_the_key(monkeypatch, caplog):
    """Providers mask the key in their own 401 text, but not all of them and not always — and this is
    the one place the wizard writes anything about a failed key to disk.

    The records are re-rendered through a real `logging.Formatter`, which includes `exc_text`: that is
    where a leak would actually show up."""
    import logging

    secret = "sk-super-secret-value-9f3a"
    monkeypatch.setattr(wizard.llm, "get_client",
                        lambda: _client_that_raises(ValueError(f"bad key {secret} rejected")))
    with caplog.at_level(logging.DEBUG, logger="kotoba.cli"):
        asyncio.run(wizard._verify("openai", secret))
    shown = logging.Formatter()
    written = "\n".join(shown.format(r) for r in caplog.records)
    assert secret not in written and "9f3a" not in written, written


def test_the_placeholder_is_refused_instead_of_asked_about():
    """`get_client` refuses to build on the example file's dummy, so asking anyway reaches no provider
    and the failure came back as `that's this install` — pointing at `doctor` for a missing piece that
    is not missing. Three true sentences in a row would still be three sentences too many."""
    from kotoba.cli import wizard

    src = pathlib.Path(wizard.__file__).read_text(encoding="utf-8")
    body = src[src.index("async def _take_key"):src.index("async def _ask_your_name")]
    assert "looks_placeholder" in body and "continue" in body
    assert "but I'll ask anyway" not in src
    assert wizard._looks_wrong(providers.PROVIDERS["openai"], "sk-...") == ""


# --- the install with no renderer, which is the one a bare `pip install kotoba` gives ---------------

@needs_posix_terminal
def test_the_bare_install_is_never_told_to_type_the_command_it_refuses(monkeypatch, tmp_path, capsys):
    """`_Plain` closed with "Type `kotoba` and I'm here" — and `kotoba` on that install answers
    `pip install "kotoba[cli]"  — or use --once` and exits 1. Her last sentence of first run pointed
    at the one command this install will not run.

    The second half drives what `kotoba` on its own actually does here. The attribute goes with the
    module: `from kotoba.cli import app` reads it off the PACKAGE once anything has imported it, so a
    None in `sys.modules` alone is bypassed and the real app starts — which in a full run is a read from
    a stdin pytest has closed, not an ImportError. That is what the `delattr` below is for."""
    from kotoba.cli.__main__ import _interactive

    answers = Answers(typed=["1", "", "Jordan", "Yuki", "es"], keys=["sk-good", ""])
    ok, out, _db = run_bare_wizard(monkeypatch, tmp_path, answers, capsys)
    assert ok is True
    assert "Type kotoba and I'm here" not in out, out[-400:]
    assert f'pip install "{DIST_NAME}[cli]"' in out, "she never names what makes `kotoba` work"
    assert "--once" in out, "nor the way to ask her something on the install she is running on"

    import kotoba.cli

    monkeypatch.setitem(sys.modules, "kotoba.cli.app", None)
    monkeypatch.delattr(kotoba.cli, "app", raising=False)
    args = type("A", (), dict(sessions=False, settings=False, plain=False, ascii=False,
                              calm=False, no_face=False))()
    assert _interactive(args) == 1
    assert f'pip install "{DIST_NAME}[cli]"' in capsys.readouterr().err


def test_the_bare_install_folds_for_ascii_and_still_spells_a_name_right(monkeypatch, tmp_path, capsys):
    """`--ascii` was parsed and then dropped on the one path that has no `caps` to carry it, so the
    install least likely to draw an em dash was the only one that always got them. The fold is the
    table and nothing else — the same rule as `theme.fold`, so a name keeps its accents."""
    answers = Answers(typed=["1", "", "José", "Yuki", "es"], keys=["sk-good", ""])
    _ok, out, _db = run_bare_wizard(monkeypatch, tmp_path, answers, capsys, ascii_only=True)
    assert "\u2014" not in out and "\u00b7" not in out and "\u2026" not in out, "chrome survived --ascii"
    assert "José" in out, "the fold took her Spanish with the chrome"


def test_the_bare_install_keeps_its_words_when_ascii_was_not_asked_for(monkeypatch, tmp_path, capsys):
    answers = Answers(typed=["1", "", "Jordan", "Yuki", "es"], keys=["sk-good", ""])
    _ok, out, _db = run_bare_wizard(monkeypatch, tmp_path, answers, capsys)
    assert "\u2014" in out, "nothing asked for ASCII, so nothing folds"


def _setup_with_no_terminal(tmp_path, typed="\n\n\n"):
    """`kotoba setup` in a child with NO controlling terminal and a pipe for stdin: a CI runner, or
    `docker run` without `-t`. `start_new_session` is what makes that deterministic — launched from a
    real terminal the child would inherit one, `/dev/tty` would open, and the branch under test would
    never run. Returns the whole screen, stdout and stderr together, because CPython's own warnings go
    to the second one and land on the first."""
    box = tmp_path / "box"
    box.mkdir()
    env = dict(os.environ)
    env.update({
        # KOTOBA_HOME and USERPROFILE beside HOME, because `Path.home()` reads HOME only on POSIX:
        # on Windows this child resolved its home to the REAL profile, and `kotoba setup` is the
        # command that writes keys. The individual overrides below covered most of it — most is not
        # the promise this makes.
        "HOME": str(box), "USERPROFILE": str(box), "KOTOBA_HOME": str(box / ".kotoba"),
        "DATABASE_URL": f"sqlite:///{box / 'try.db'}",
        "KOTOBA_SETTINGS": str(box / "settings.yaml"), "KOTOBA_MCP_CONFIG": str(box / "mcp.yaml"),
        "KOTOBA_PENDING_MCP": str(box / "pending.yaml"), "KOTOBA_MEMORY_DIR": str(box / "memory"),
        "KOTOBA_FILES_DIR": str(box / "files"), "KOTOBA_PLUGINS_PATH": str(box / "plugins"),
        "KOTOBA_VISUAL_MEMORY_DIR": str(box / "visual"), "KOTOBA_TMP_DIR": str(box / "tmp"),
        "KOTOBA_KEYSTORE_KEY_FILE": str(box / "keystore.key"),
        "KOTOBA_CLI_HISTORY": str(box / "history"), "KOTOBA_CLI_LOG": str(box / "cli.log"),
        "OPENAI_API_KEY": "", "XAI_API_KEY": "", "ELEVENLABS_API_KEY": "",
    })
    done = subprocess.run([sys.executable, "-m", "kotoba.cli", "setup", "--ascii"], input=typed,
                          capture_output=True, text=True, cwd=str(box), env=env,
                          start_new_session=True, timeout=180)
    return done.stdout + done.stderr


def test_the_key_row_is_not_written_over_by_cpython_and_promises_only_what_it_can_do(tmp_path):
    """Two halves of one screen. `getpass` turns the echo off on `/dev/tty`, then on stdin; with
    NEITHER it prints two lines of CPython and reads stdin anyway — and both landed inside the row that
    had just said the key would not show. The prose is hers, so the machine's warnings may not appear
    on it at all; and where nothing here can turn an echo off, the row may not claim that it did."""
    screen = _setup_with_no_terminal(tmp_path)
    assert "OpenAI key" in screen, screen[-800:]
    assert "GetPassWarning" not in screen and "may be echoed" not in screen, screen[-800:]
    assert "nothing shows as you type" not in screen, screen[-800:]
    assert "I won't echo it while you type" not in screen, screen[-800:]
    assert "it WILL show as you type" in screen, "warned about nothing at all"


@needs_posix_terminal
def test_a_terminal_that_can_hide_a_key_still_does():
    """The other half of the promise, and the one a pipe cannot answer: where the echo IS ours to turn
    off, the key must not reach the screen at all. So this runs the read on a real pty and reads back
    everything that terminal was told to draw — the typed key may appear exactly once, in the line the
    child chose to print."""
    import pty
    import select

    code = ("from kotoba.cli import secret\n"
            "assert secret.can_hide(), 'a pty is a terminal'\n"
            "print('GOT:' + secret.read('key: '), flush=True)\n")
    parent, child = pty.openpty()
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=child, stdout=child, stderr=child,
                            start_new_session=True)
    os.close(child)
    seen, sent, deadline = "", False, time.monotonic() + 60
    try:
        while time.monotonic() < deadline and "GOT:" not in seen:
            if select.select([parent], [], [], 0.2)[0]:
                try:
                    chunk = os.read(parent, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                seen += chunk.decode("utf-8", "replace")
            if not sent and "key: " in seen:
                os.write(parent, b"sk-typed-secret\n")     # only once the read is up: it turns the echo
                sent = True                                # off itself, and typing into it earlier races
        assert proc.wait(timeout=20) == 0, seen
    finally:
        os.close(parent)
        if proc.poll() is None:
            proc.kill()
    assert "GOT:sk-typed-secret" in seen, seen
    assert seen.count("sk-typed-secret") == 1, f"the terminal echoed it: {seen!r}"


def test_a_page_where_an_error_object_belongs_is_not_the_provider_judging_the_model():
    """A captive portal or a proxy answers with markup, and the bare-400/404 branch read that as
    "your model is wrong" — sending somebody to change a model that was fine over a sign-in page.

    The body is the tell, never the status. A bare 400 or 404 with no body means the model only when
    the model is one somebody TYPED — a live install proved the other half, refusing the default model
    off our own list with an empty body — so this pins the typed case beside the page."""
    import httpx
    import openai

    from kotoba.core import app_settings

    spec = providers.PROVIDERS["openai"]
    app_settings.set_runtime("model", "gpt-typed-by-hand-9000")

    def answered(code, body):
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        return openai.APIStatusError("x", response=httpx.Response(code, request=request), body=body)

    for code in (400, 404):
        said = wizard._explain(spec, answered(code, "<html><body>Sign in</body></html>"), "sk-x")
        assert "Something answered instead of OpenAI" in said, said
        assert "it's the model" not in said

        bare = wizard._explain(spec, answered(code, None), "sk-x")
        assert "it's the model" in bare, "a bare status with no body still means the model"
        obj = wizard._explain(spec, answered(code, {"error": {"code": "", "param": "", "message": "x"}}), "sk-x")
        assert "it's the model" in obj, "the provider's own error object still means the model"


def test_a_refusal_that_names_no_field_never_blames_a_model_off_our_own_list():
    """Measured on a live Windows install: a bare 400 with an EMPTY body, on the DEFAULT model picked
    from this very list, was reported as "the key is fine — it's the model". The person then went
    looking for a spelling that was right, on a model we had offered them.

    A typed id keeps the old sentence — that is the case it was measured against."""
    import httpx
    import openai

    from kotoba.core import app_settings

    spec = providers.PROVIDERS["openai"]
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    mute = openai.APIStatusError("x", response=httpx.Response(400, request=request), body=None)

    app_settings.set_runtime("model", app_settings.DEFAULT_MODEL)
    said = wizard._explain(spec, mute, "sk-x")
    assert "gave no reason at all" in said and "not the model" in said, said
    assert "it's the model" not in said

    app_settings.set_runtime("model", "gpt-typed-by-hand-9000")
    typed = wizard._explain(spec, mute, "sk-x")
    assert "it's the model" in typed, "a typed id is the case _wrong_model was measured against"

    app_settings.set_runtime("model", app_settings.DEFAULT_MODEL)
    named = openai.APIStatusError("x", response=httpx.Response(404, request=request),
                                  body={"error": {"code": "model_not_found", "param": "model", "message": "x"}})
    assert "it's the model" in wizard._explain(spec, named, "sk-x"), \
        "the provider naming the model is still the provider naming the model"
