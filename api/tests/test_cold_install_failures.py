"""The two ways a cold install failed a stranger without saying anything usable.

The hang: aiosqlite's worker thread is not a daemon, so a raise between `db.connect()` and the first
teardown leaves a process that prints its traceback and never exits. Every acquire-then-throw seam on
that path now releases the connection, and CLI entry points print a sentence a person can act on.

The placeholder: `.env.example` ships `OPENAI_API_KEY=sk-...` literally, and copied unedited it
reached the SDK as a configured key — `doctor` reported the LLM green while a real 401 sat unnamed. A
placeholder now counts as no key at all, and doctor names the file and the fix."""
from __future__ import annotations

import asyncio
import sys
import threading
import time

import httpx
import pytest
from openai import AuthenticationError

import kotoba.cli.session as cli_session
from kotoba.cli import doctor, wizard
from kotoba.cli.__main__ import _once
from kotoba.core import engine, llm


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_ambient_key():
    llm._provider_keys.clear()
    yield
    llm._provider_keys.clear()


def test_a_failed_start_closes_the_database_it_opened(monkeypatch, tmp_path):
    opened = []

    class Spy(engine.Database):
        async def connect(self):
            await super().connect()
            opened.append(self)

    monkeypatch.setattr(engine, "Database", Spy)
    monkeypatch.setenv("SOUL_PATH", str(tmp_path / "nowhere.md"))

    with pytest.raises(FileNotFoundError):
        _run(engine.start(tickers=False))
    assert opened, "the failure under test happens AFTER connect, so connect must have run"
    assert all(db.conn is None for db in opened), "an open connection would keep the process alive"


def test_a_failed_start_leaves_no_worker_thread_behind(monkeypatch, tmp_path):
    """The invariant is that no non-daemon thread SURVIVES, because one of those is what hangs the exit —
    not that every thread has already finished winding down the instant the exception lands. `db.close()`
    returns before aiosqlite's worker has actually exited, so an instantaneous snapshot made this fail or
    pass depending on the scheduler: green under a random test order, red under a fixed one."""
    monkeypatch.setenv("SOUL_PATH", str(tmp_path / "nowhere.md"))
    before = set(threading.enumerate())
    with pytest.raises(FileNotFoundError):
        _run(engine.start(tickers=False))

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        stragglers = [t for t in set(threading.enumerate()) - before if not t.daemon]
        if not stragglers:
            return
        time.sleep(0.02)
    assert not stragglers, f"non-daemon threads survive the raise and hang the exit: {stragglers}"


def test_a_failed_migration_does_not_leave_the_connection_open(monkeypatch, tmp_path):
    from kotoba.db import database as dbmod

    async def boom(conn):
        raise RuntimeError("migration exploded")

    monkeypatch.setattr(dbmod.migrations, "run_migrations", boom)
    db = dbmod.Database("sqlite:///" + str(tmp_path / "x.db"))
    with pytest.raises(RuntimeError):
        _run(db.connect())
    assert db.conn is None


def test_doctor_survives_a_database_that_opens_but_cannot_answer(monkeypatch, tmp_path):
    """The old shape returned the fail Check with the connection still open — doctor itself would
    then hang on exit, on the machine of exactly the person it was diagnosing.

    Waits the same way its neighbour above does, and for the same reason: `db.close()` returns before
    aiosqlite's worker has actually exited, so an instantaneous snapshot grades the scheduler."""
    from kotoba.db import database as dbmod

    async def boom(conn):
        raise RuntimeError("schema is somebody else's")

    monkeypatch.setattr(dbmod.migrations, "run_migrations", boom)
    before = set(threading.enumerate())
    check = _run(doctor._database())
    assert check.status == "fail"
    started = [t for t in set(threading.enumerate()) - before if not t.daemon]
    for t in started:
        t.join(5.0)
    stragglers = [t for t in started if t.is_alive()]
    assert not stragglers, f"doctor would hang on exit: {stragglers}"


def test_once_fails_fast_with_a_sentence_instead_of_a_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SOUL_PATH", str(tmp_path / "nowhere.md"))
    code = _run(_once("hola"))
    err = capsys.readouterr().err
    assert code == 1
    assert "She could not start" in err and "SOUL file not found" in err
    assert "kotoba doctor" in err, "the sentence must say where the full diagnosis lives"


def test_a_session_that_fails_after_start_stops_the_engine(monkeypatch):
    stopped = []
    real_stop = engine.stop

    async def spy_stop(eng):
        stopped.append(eng)
        await real_stop(eng)

    monkeypatch.setattr(cli_session.core_engine, "stop", spy_stop)
    monkeypatch.setattr(cli_session.session_sandbox, "note_connect",
                        lambda sid: (_ for _ in ()).throw(RuntimeError("wiring broke")))
    with pytest.raises(RuntimeError):
        _run(cli_session.Session.open())
    assert stopped, "the engine outlives a half-opened session and its thread hangs the process"


@pytest.mark.parametrize("value", [
    "sk-...", "el_...", "xai-...", "...", "…", "", "   ", "sk-",
    "changeme", "CHANGE-ME", "your-key-here", "sk-your-key-here", "<paste-your-key>", "placeholder",
])
def test_template_placeholders_read_as_no_key(value):
    assert llm.looks_placeholder(value), value


@pytest.mark.parametrize("value", [
    "sk-proj-Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56",
    "sk-not-a-real-key",
    "xai-Ab12Cd34",
    "AKIAIOSFODNN7EXAMPLE",
    "eyJhbGciOi.eyJzdWIi.SflKxwRJSM",
])
def test_a_real_key_is_never_rejected_for_looking_unusual(value):
    assert not llm.looks_placeholder(value), value


def test_the_placeholder_key_builds_no_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-...")
    assert llm.get_client() is None, "a placeholder that builds a client 401s on every turn"


def test_a_placeholder_saved_in_the_app_builds_no_client_either(monkeypatch):
    """Only the ENV half was filtered. The saved half was exempt on the grounds that a stored key had
    been round-tripped first — true of `kotoba setup`, and never true of `/api/settings/llm-key`, which
    stores and leaves `/api/settings/llm-test` to validate afterwards. So `sk-...` pasted into the web
    panel reached the SDK as a configured key, `kotoba doctor` reported the LLM green, and first run was
    over: the whole trap this file is named for, through the door that opened after it was closed."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    llm.set_provider_key("openai", "sk-...")
    assert llm.get_client() is None, "a saved placeholder that builds a client 401s on every turn"


def test_doctor_names_the_file_and_the_fix_for_a_placeholder_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-...")
    check = _run(doctor._llm())
    assert check.status == "fail"
    assert ".env.example" in check.detail and "OPENAI_API_KEY" in check.detail
    assert "kotoba setup" in check.detail
    assert "sk-..." not in check.detail, "never echo the value — a misclassified key is a secret"


def test_the_wizard_offers_itself_when_the_env_holds_a_placeholder(monkeypatch):
    class _NoSavedKeys:
        async def get_key(self, name):
            return None

    monkeypatch.setenv("OPENAI_API_KEY", "sk-...")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert _run(wizard.needed(_NoSavedKeys()))


def test_a_rejected_key_speaks_its_own_sentence():
    rejected = AuthenticationError(
        "Incorrect API key provided",
        response=httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com/v1")),
        body=None,
    )
    line = cli_session._spoken_failure(rejected)
    assert "key" in line and "kotoba setup" in line
    assert cli_session._spoken_failure(RuntimeError("boom")) == (
        "Sorry, something tripped up on my end — let's try that again."
    )


def test_a_turn_dying_on_auth_tells_the_user_about_the_key(monkeypatch):
    async def _rejected_loop(items, sid, db, stream, patterns, **kw):
        raise AuthenticationError(
            "Incorrect API key provided",
            response=httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com/v1")),
            body=None,
        )

    monkeypatch.setattr(cli_session, "agentic_loop", _rejected_loop)

    async def go():
        session = await cli_session.Session.open()
        try:
            return await session.ask("hola")
        finally:
            await session.close()

    reply = _run(go())
    assert "rejected" in reply and "kotoba setup" in reply
    assert "tripped up" not in reply


# ── The ElevenLabs key had the same trap, one line lower in the same template ────────────────────────


@pytest.mark.parametrize("value", ["el_...", "el_", "<paste it here>", "changeme", ""])
def test_a_placeholder_elevenlabs_key_counts_as_no_key(monkeypatch, value):
    """`.env.example` ships `ELEVENLABS_API_KEY=el_...` directly under the OpenAI one. Handed to
    ElevenLabs it 401s, and she simply goes quiet — the same silence, on the channel where silence is
    hardest to tell from a bug."""
    from kotoba.core.voice import config as voice_config

    monkeypatch.setenv("ELEVENLABS_API_KEY", value)
    monkeypatch.setattr(voice_config, "_api_key", None)
    assert voice_config.resolve_api_key() == ""


@pytest.mark.parametrize("value", ["el_9f3a1c7b2e4d", "9f3a1c7b2e4d0a", "sk_live_abc123"])
def test_a_real_elevenlabs_key_is_left_alone(monkeypatch, value):
    """A false 'your key is bad' is worse than the silence being fixed, so anything without a structural
    tell is real — short, unprefixed, oddly prefixed."""
    from kotoba.core.voice import config as voice_config

    monkeypatch.setenv("ELEVENLABS_API_KEY", value)
    monkeypatch.setattr(voice_config, "_api_key", None)
    assert voice_config.resolve_api_key() == value
    assert voice_config.auth_headers() == {"xi-api-key": value}


def test_an_in_app_saved_voice_key_is_never_filtered(monkeypatch):
    """The wizard round-trips a key against the API before saving it, so a saved key outranks this rule
    even if it happens to look like a template."""
    from kotoba.core.voice import config as voice_config

    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_...")
    monkeypatch.setattr(voice_config, "_api_key", "el_...")
    assert voice_config.resolve_api_key() == "el_..."


def test_the_placeholder_error_names_a_route_that_exists_on_every_install(monkeypatch):
    """It used to name `api/.env`, and this test pinned it there. That file is something only a git
    clone has: after `pip install kotoba` there is no `api/` to open, and somebody in a terminal never
    had one — so the one sentence that was supposed to unstick them named a path they cannot visit."""
    from kotoba.core.voice import config as voice_config

    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_...")
    monkeypatch.setattr(voice_config, "_api_key", None)
    with pytest.raises(voice_config.VoiceAuthError) as exc:
        voice_config.auth_headers()
    assert "placeholder" in str(exc.value) and "kotoba setup" in str(exc.value)
    assert "el_..." not in str(exc.value)


def test_no_key_at_all_names_the_same_route(monkeypatch):
    from kotoba.core.voice import config as voice_config

    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(voice_config, "_api_key", None)
    with pytest.raises(voice_config.VoiceAuthError) as exc:
        voice_config.auth_headers()
    assert "kotoba setup" in str(exc.value)


def test_the_offline_line_names_the_route_that_works_on_every_install():
    """What she SAYS when there is no brain. `add OPENAI_API_KEY in api/.env` was wrong for anybody in
    the terminal and meaningless on a wheel — the same defect as the voice sentence above, on the path
    a stranger is most likely to hit first. `kotoba setup` is the route both installs have."""
    from kotoba.core import loop

    assert "kotoba setup" in loop._OFFLINE_MSG
    assert "api/.env" not in loop._OFFLINE_MSG


def test_no_user_facing_line_in_core_sends_anybody_to_api_dot_env():
    """The sentence had three copies and fixing one would have left the other two. `core/mcp/client.py`
    is exempt on purpose: it describes where an MCP server's `${TOKEN}` lives for somebody editing a
    config file in a clone, which is not a route offered to a stranger with no key."""
    import pathlib

    import kotoba.core as core

    root = pathlib.Path(core.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "client.py" and path.parent.name == "mcp":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "api/.env" in line:
                offenders.append(f"{path.relative_to(root)}:{n}")
    assert offenders == [], f"still sending people to a file a wheel does not have: {offenders}"


def test_doctor_survives_an_install_without_the_voice_extra(monkeypatch):
    """`core.voice.__init__` eagerly imports stt/tts, which need `websockets` — an extra that
    `pip install -e "api/[cli]"` does not bring. The first version of this check imported it bare and took
    the WHOLE of doctor down with a ModuleNotFoundError, on exactly the installs where a stranger most
    needs a diagnosis. The tool that reports what is missing must not break when something is missing."""
    import builtins

    from kotoba.cli import doctor

    real = builtins.__import__

    def no_websockets(name, *a, **k):
        if name == "websockets" or name.startswith("websockets."):
            raise ModuleNotFoundError("No module named 'websockets'")
        return real(name, *a, **k)

    for mod in [m for m in list(sys.modules) if m.startswith("kotoba.core.voice")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(builtins, "__import__", no_websockets)

    check = doctor._voice_key()
    assert check.status == "skip" and "voice` extra" in check.detail
    assert doctor.verdict_of([("core", [check])])[0] == 0


def test_doctor_warns_about_voice_without_failing_the_verdict(monkeypatch):
    """`warn`, not `fail`: text is a complete way to use her and the CLI needs no voice at all — a red
    verdict for a missing voice key would teach people to ignore doctor."""
    from kotoba.cli import doctor
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", None)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_...")
    check = doctor._voice_key()
    assert check.status == "warn" and "placeholder" in check.detail
    assert doctor.verdict_of([("core", [check])])[0] == 0

    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_9f3a1c7b2e4d")
    assert doctor._voice_key().status == "ok"


def test_doctor_surfaces_expressive_asked_for_but_silently_stripped(monkeypatch):
    """The disagreement that once ran silent for weeks: `expressive: true` with `tts_engine: fast` —
    her face kept emoting (the tags also drive Live2D), her words stayed warm, and ElevenLabs never
    performed a single [warmly]. The authority is audio_tags_enabled(), never the `expressive` flag,
    and doctor is the one place that says when they disagree — without failing the verdict, because
    `fast` is a legitimate choice."""
    from kotoba.cli import doctor
    from kotoba.core import app_settings
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", None)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_9f3a1c7b2e4d")
    voice = doctor._voice_key()
    assert voice.status == "ok"

    app_settings.set_runtime("tts_engine", "fast")  # expressive and voice_mode keep their defaults
    assert app_settings.runtime_all()["expressive"] is True
    assert app_settings.audio_tags_enabled() is False, "the flag and the authority must disagree here"

    check = doctor._voice_tags(voice)
    assert check.status == "warn"
    assert "fast" in check.detail and "strips" in check.detail
    assert "expressive off" in check.detail, "the deliberate-fast user must be told how to retire this"
    assert doctor.verdict_of([("core", [check])])[0] == 0, "a stripped tag is never a red verdict"


def test_choosing_fast_deliberately_is_never_nagged(monkeypatch):
    """expressive OFF + fast is a coherent choice — ok, not warn. So is the expressive engine. And in
    agent mode the local engine setting is inert: the EL dashboard agent performs the tags."""
    from kotoba.cli import doctor
    from kotoba.core import app_settings
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", None)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_9f3a1c7b2e4d")
    voice = doctor._voice_key()

    app_settings.set_runtime("tts_engine", "fast")
    app_settings.set_runtime("expressive", False)
    assert doctor._voice_tags(voice).status == "ok"

    app_settings.set_runtime("expressive", True)
    app_settings.set_runtime("tts_engine", "expressive")
    assert doctor._voice_tags(voice).status == "ok"

    app_settings.set_runtime("tts_engine", "fast")
    app_settings.set_runtime("voice_mode", "agent")
    check = doctor._voice_tags(voice)
    assert check.status == "ok" and "agent" in check.detail


def test_voice_tags_are_not_asked_about_when_there_is_no_voice():
    """No key or no voice extra means nothing is spoken at all — warning about how tags would be
    performed on top of that is noise, and doctor never guesses downstream of a missing piece."""
    from kotoba.cli import doctor

    for status in ("warn", "skip"):
        check = doctor._voice_tags(doctor.Check("voice key", status, ""))
        assert check.status == "skip" and "not asked" in check.detail


# --- a pasted key brings whitespace with it, and that used to look like a dead network ---------------

@pytest.mark.parametrize("dressed", ["{k} ", " {k}", "{k}\n", "\t{k}\r\n", "  {k}  "])
def test_whitespace_around_a_key_never_reaches_the_wire(dressed, monkeypatch):
    """Whitespace cannot go in an HTTP header, so the client raised before the request left and the
    failure arrived as `APIConnectionError` — which the wizard reads, correctly for its type, as an
    unreachable network. Somebody was told to check a proxy and a firewall over a trailing space.

    The 401 branch of that same wizard has always said "a stray space often comes along with a paste";
    it was never reached, because the request never got far enough to be refused."""
    seen = {}

    class _Client:
        def __init__(self, api_key, **kw):
            seen["key"] = api_key

        class responses:
            @staticmethod
            async def create(**kw):
                return object()

        async def close(self):
            return None

    import openai

    from kotoba.core import first_run

    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)
    ok, _ = _run(first_run.verify_key("openai", dressed.format(k="sk-a-real-looking-key")))
    assert ok
    assert seen["key"] == "sk-a-real-looking-key", "the key reached the client still dressed"


def test_what_is_stored_is_what_was_verified():
    """The probe trims; an untrimmed store would keep a key that passes the wizard and then fails on
    the first real turn, with nothing to connect the two moments."""
    llm.set_provider_key("openai", "  sk-trimmed-on-the-way-in\n")
    assert llm._provider_keys["openai"] == "sk-trimmed-on-the-way-in"
    llm._provider_keys.clear()


def _ok_key():
    from kotoba.cli.doctor import Check

    return Check("llm key", "ok", "OpenAI · gpt-5.6-luna · key saved in the app (encrypted)")


def test_the_effort_and_everything_it_decides_is_on_the_report(monkeypatch):
    """One setting moves four things at once, and before this line nothing on the report named it.

    The stock install is the case that matters: it must say she narrates in the user's own language
    and that the reasoning is not left with the provider — the two things an unset effort silently
    took away."""
    from kotoba.cli import doctor
    from kotoba.core import app_settings

    monkeypatch.delenv("KOTOBA_REASONING_EFFORT", raising=False)
    app_settings.set_runtime("model", "gpt-5.6-luna")
    app_settings.set_runtime("reasoning_effort", app_settings.DEFAULT_REASONING_EFFORT)
    check = doctor._reasoning(_ok_key())
    assert check.status == "ok"
    assert "low" in check.detail and "work mode" in check.detail
    assert "your language" in check.detail
    assert "OpenAI" in check.detail, "the retention half is the one nobody would guess"


def test_turning_the_effort_off_says_what_it_costs_without_nagging(monkeypatch):
    """`off` is a legitimate choice, so it is never a warn — but it drops store=False along with the
    reasoning block, which is not a thing anybody would expect from a knob named 'effort'."""
    from kotoba.cli import doctor
    from kotoba.core import app_settings

    app_settings.set_runtime("model", "gpt-5.6-luna")
    app_settings.set_runtime("reasoning_effort", "off")
    check = doctor._reasoning(_ok_key())
    assert check.status == "ok"
    assert "English" in check.detail and "forget" in check.detail
    assert doctor.verdict_of([("core", [check])])[0] == 0


def test_an_effort_the_provider_folds_is_the_one_thing_worth_a_warning(monkeypatch):
    """Nothing else can tell you: `minimal` is accepted, stored, shown back in Settings, and spent as
    `low`. The warning names the value actually being spent, not the one that was typed."""
    from kotoba.cli import doctor
    from kotoba.core import app_settings

    app_settings.set_runtime("model", "gpt-5.6-luna")
    app_settings.set_runtime("reasoning_effort", "minimal")
    check = doctor._reasoning(_ok_key())
    assert check.status == "warn"
    assert "minimal" in check.detail and "`low`" in check.detail
    assert doctor.verdict_of([("core", [check])])[0] == 0, "a folded effort is never a red verdict"


def test_the_effort_is_reported_as_inert_on_a_model_that_does_not_reason(monkeypatch):
    """model and reasoning_effort are independent settings, and the pair is reachable by pressing
    Enter. Reporting an effort as live on gpt-4o-mini would explain her narration exactly backwards."""
    from kotoba.cli import doctor
    from kotoba.core import app_settings

    app_settings.set_runtime("model", "gpt-4o-mini")
    app_settings.set_runtime("work_model", "gpt-4o-mini")
    app_settings.set_runtime("reasoning_effort", "low")
    check = doctor._reasoning(_ok_key())
    assert check.status == "ok"
    assert "not used" in check.detail and "changes nothing" in check.detail


def test_the_two_modes_are_reported_separately_because_they_answer_separately(monkeypatch):
    """Read off the wire, never re-derived. A report built from the companion model alone promised
    work mode was thinking under a non-reasoning work_model, and called a reasoning work mode inert."""
    from kotoba.cli import doctor
    from kotoba.core import app_settings

    app_settings.set_runtime("reasoning_effort", "low")
    app_settings.set_runtime("model", "gpt-5.6-luna")
    app_settings.set_runtime("work_model", "gpt-4o-mini")
    said = doctor._reasoning(_ok_key()).detail
    assert "low on a companion turn, off in work mode" in said, said

    app_settings.set_runtime("model", "gpt-4o-mini")
    app_settings.set_runtime("work_model", "gpt-5.6-luna")
    said = doctor._reasoning(_ok_key()).detail
    assert "off on a companion turn, medium in work mode" in said, said
    assert "falls back to the written narration lines" in said


def test_an_effort_the_validator_never_saw_is_warned_about_too(monkeypatch):
    """The silent fold this warning exists for. `runtime_all` validates and substitutes the default,
    while the wire reads the env half RAW — so a junk value was folded to `low` and reported as though
    `low` were what somebody chose."""
    from kotoba.cli import doctor
    from kotoba.core import app_settings

    app_settings.set_runtime("model", "gpt-5.6-luna")
    app_settings.set_runtime("work_model", "")
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "junk")   # env only: an override would win over it

    check = doctor._reasoning(_ok_key())
    assert check.status == "warn", check.detail
    assert "junk" in check.detail and "`low`" in check.detail


def test_the_effort_is_not_guessed_at_when_the_key_check_could_not_run():
    """Downstream of a blocked core there is no model to judge it against, and doctor never guesses
    below a hard failure."""
    from kotoba.cli import doctor
    from kotoba.cli.doctor import Check

    blocked = Check("llm key", "skip", "not asked — fix database first")
    assert doctor._reasoning(blocked).status == "skip"
