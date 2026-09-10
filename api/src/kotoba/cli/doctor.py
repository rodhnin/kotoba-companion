"""`kotoba doctor` — why she will not run, in the order that decides it.

The order is the design: soul file before database before key, and anything downstream of a hard
failure is reported as skipped rather than guessed at. Optional pieces never fail the run.

The key check goes through `engine.start` on purpose — a key saved from the Settings panel lives
ENCRYPTED in the database and reaches the client only along that path, so a doctor reading the
environment would tell a configured user she has none. Nothing here prints a secret. A stdio MCP
server's stderr goes to a log file, so this is where a stranger learns theirs does not start.
"""
from __future__ import annotations

from kotoba import DIST_NAME

import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from kotoba.cli import host
from kotoba.cli.render.ascii_fold import fold

# (extra name, modules it installs, what a stranger loses without it)
_EXTRAS = (
    ("server", ("fastapi", "uvicorn"),
     "no `kotoba serve`, no web UI, no ElevenLabs endpoint"),
    ("voice", ("websockets",),
     "no local voice — she cannot listen or speak"),
    ("mcp", ("mcp",),
     "no MCP servers — she is limited to her built-in tools"),
    ("web", ("readability",),
     "`web_extract` falls back to the r.jina.ai reader service, so pages she reads leave this machine"),
    ("cli", ("prompt_toolkit", "rich"),
     "no interactive terminal — only `kotoba --once`"),
)

_COLOURS = {"ok": "32", "warn": "33", "fail": "31", "skip": "90"}


@dataclass
class Check:
    name: str
    status: str      # ok | warn | fail | skip
    detail: str


def run(out: TextIO | None = None, *, plain: bool = False, ascii_only: bool = False) -> int:
    """Print the report and return the exit code: 0 ready, 1 not.

    These are the two render flags this command can act on, and it takes them because it really does
    both things: it paints a status column, and it writes em dashes in almost every detail. `--calm`
    and `--no-face` are refused by name in `__main__` instead — nothing on this report moves and it
    has no face — because a flag accepted where it changes nothing is the defect `kotoba setup` had.

    The fold is the last thing each row passes through, the same place the transcript puts it, so
    a detail carrying a path or a version folds by having been printed rather than at its own call."""
    out = out or sys.stdout
    sections = asyncio.run(collect())

    def say(text: str) -> None:
        print(fold(text) if ascii_only else text, file=out)

    say("kotoba doctor")
    for title, checks in sections:
        say(f"\n{title}")
        for c in checks:
            say(f"  {_paint(c.status, out, plain)}  {c.name:<11} {c.detail}")
    code, verdict = verdict_of(sections)
    say(f"\n{verdict}")
    return code


async def collect() -> list[tuple[str, list[Check]]]:
    return [
        ("what she needs", await _core()),
        ("optional extras", _extras()),
        ("on this machine", _machine()),
        ("mcp servers", await _mcp()),
    ]


def verdict_of(sections: list[tuple[str, list[Check]]]) -> tuple[int, str]:
    checks = [c for _, cs in sections for c in cs]
    broken = [c for c in checks if c.status == "fail"]
    missing = [c for c in checks if c.status == "warn"]
    if broken:
        return 1, f"{_count(len(broken), 'problem')} to fix before she runs."
    if missing:
        return 0, (f"Ready. {_count(len(missing), 'optional piece')} missing; nothing above needs "
                   f"{'it' if len(missing) == 1 else 'them'}.")
    return 0, "Ready. Everything she can use is here."


async def _core() -> list[Check]:
    checks = [_machine_name(), _terminal(), _python(), _soul(), await _database(), _encryption()]
    # Everything above stops her from running, so a failure there makes reading a key pointless. The
    # permissions row blocks nothing and never fails the run: the keys are readable either way, by her
    # and — when the grant was refused — by whoever else the inherited ACL let in.
    blocked = [c.name for c in checks if c.status == "fail"]
    checks.append(_file_perms())
    if blocked:
        checks.append(Check("saved keys", "skip", f"not read — fix {', '.join(blocked)} first"))
        checks.append(Check("llm key", "skip", f"not asked — fix {', '.join(blocked)} first"))
    else:
        checks.append(await _saved_secrets())
        checks.append(await _llm())
    checks.append(_reasoning(checks[-1]))
    # The voice key is read AFTER `_llm`, and that ordering is load-bearing: a key saved from first run
    # or the Settings panel lives encrypted in the database and only reaches `resolve_api_key` when
    # `engine.start` has decrypted it, which is what `_llm` above just did. Blocked, there is nothing to
    # read it out of, so it is skipped rather than reported absent.
    voice = Check("voice key", "skip", f"not asked — fix {', '.join(blocked)} first") if blocked \
        else _voice_key()
    checks.append(voice)
    checks.append(_voice_tags(voice))
    checks.append(_sandbox())
    checks.append(_web_ui())
    return checks


def _web_ui() -> Check:
    """Whether there is a face to open, and from where. This was the one thing doctor never mentioned,
    on the one command whose whole job is to explain why something will not start."""
    from kotoba.cli import serve
    from kotoba.core import frontend
    from kotoba.paths import REPO_ROOT, _CLONE

    # Asked in the order `serve` decides, or the two disagree about the same install: the dev server
    # wins wherever it can run, and answering from the packaged build first sent people to a port
    # nothing was listening on.
    blocked = serve.frontend_blocker(REPO_ROOT)
    if blocked is None:
        return Check("web ui", "ok", "dev server — `kotoba serve` runs it with npm")
    seen = frontend.describe()
    if seen["kind"] in ("packaged", "export"):
        built = {"packaged": "packaged build", "export": "local build"}[seen["kind"]]
        stamped = f", kotoba {seen['version']}" if seen.get("version") else ""
        return Check("web ui", "ok",
                     f"{built}, {seen.get('files', 0)} files{stamped} — `kotoba serve`, then /app")
    if seen.get("named"):
        return Check("web ui", "fail",
                     f"KOTOBA_FRONTEND_DIR points at {seen['named']}, which holds no app.html")
    if _CLONE is None:
        return Check("web ui", "fail",
                     "this install carries no web UI — it was packaged without one; "
                     f"pip install --force-reinstall {DIST_NAME}")
    return Check("web ui", "warn", f"no build, and no dev server either: {blocked}")


def _reasoning(llm_key: Check) -> Check:
    """One setting decides four unrelated things: how hard she thinks, who narrates her tool use in
    what language, whether the provider keeps her reasoning, and which system prompt she is sent.

    Every value here is READ OFF THE WIRE instead of re-derived, because the two modes answer
    separately and a re-derived report got both mixed cases wrong: a non-reasoning work model under a
    reasoning companion still promised work mode was thinking. The `warn` is the one thing nothing
    else can tell you — an effort the provider will not take is folded to a neighbour in silence."""
    if llm_key.status == "skip":
        return Check("reasoning", "skip", llm_key.detail)
    from kotoba.core import app_settings, llm, providers

    spec = providers.get_spec()
    asked = app_settings.runtime_value("reasoning_effort", "KOTOBA_REASONING_EFFORT",
                                       app_settings.DEFAULT_REASONING_EFFORT).strip().lower()
    sent = {m: llm.model_call_kwargs(m).get("reasoning", {}).get("effort", "")
            for m in ("companion", "work")}
    if not any(sent.values()):
        why = ("off" if asked in ("", "off") else
               f"not used — neither model reasons, so `{asked}` changes nothing")
        kept = f", and nothing asks {spec.label} to forget it" if spec.supports_encrypted_reasoning else ""
        return Check("reasoning", "ok",
                     f"{why} — she speaks the written narration lines, which are English whatever "
                     f"language you are in{kept}. Set reasoning_effort to `low` to change both.")
    told = f"{sent['companion'] or 'off'} on a companion turn, {sent['work'] or 'off'} in work mode"
    tail = ("she narrates her own tool use in your language" if sent["companion"] else
            "a companion turn falls back to the written narration lines, which are English")
    stateless = f", carried encrypted rather than kept at {spec.label}" if spec.supports_encrypted_reasoning else ""
    if sent["companion"] and sent["companion"] != asked:
        return Check("reasoning", "warn",
                     f"`{asked}` is not one {spec.label} takes, so a companion turn is spent at "
                     f"`{sent['companion']}` instead — {told}. Set it to `{sent['companion']}` to say "
                     f"that is what you meant.")
    return Check("reasoning", "ok", f"{told} — {tail}{stateless}")


def _voice_tags(voice_key: Check) -> Check:
    """Whether the [warmly]-style audio tags the user asked for are actually PERFORMED. This exact miss
    once ran silent for weeks: `expressive: true` with `tts_engine: fast` strips every tag before
    synthesis, her face keeps emoting (the tags also drive Live2D over SSE), her words stay warm — and
    nothing anywhere said the voice itself had gone flat. The authority is
    app_settings.audio_tags_enabled(), never the `expressive` flag alone: the two can disagree, and the
    disagreement is the finding.

    A `warn`, never a `fail`: `fast` is a legitimate trade (lower latency, no tags), so the message
    names both exits — restore the expressive engine to hear the tags, or turn expressive off to say
    the flat voice is meant, which also retires this warning. Reads settings only; never a provider call."""
    if voice_key.status != "ok":
        return Check("voice tags", "skip", "not asked — no voice to perform them")
    from kotoba.core import app_settings

    values = app_settings.runtime_all()
    if not values["expressive"]:
        return Check("voice tags", "ok", "off — expressive is off, so none are asked for")
    if app_settings.audio_tags_enabled():
        performer = ("your ElevenLabs dashboard agent" if values["voice_mode"] == "agent"
                     else "the expressive engine")
        return Check("voice tags", "ok", f"on — {performer} performs [warmly]-style emotion tags")
    return Check("voice tags", "warn",
                 "asked for but never performed — expressive is on, but tts_engine `fast` strips every "
                 "audio tag before synthesis, so her face emotes while her voice stays flat. Set "
                 "tts_engine to `expressive` to hear them, or set expressive off if fast is the point.")


def _voice_key() -> Check:
    """Can she speak? A `warn`, not a `fail`: text mode is a complete way to use her, and the CLI needs
    no voice at all. The messages below send the reader to `kotoba setup`, which is only true because
    first run asks for this key too — it used to ask for the LLM key and nothing else.

    The import is guarded because the voice package eagerly pulls in `stt`/`tts`, which need
    `websockets`, an extra the `cli` install does not bring. Imported bare it took the WHOLE of doctor
    down with a ModuleNotFoundError on exactly the installs where a stranger most needs a diagnosis: a
    tool that reports what is missing must not be the thing that breaks when something is missing."""
    try:
        from kotoba.core.voice import config as voice_config
    except ImportError:
        return Check("voice key", "skip",
                     "not asked — the `voice` extra is not installed, so there is no voice to configure")

    if voice_config.resolve_api_key():
        source = "saved in the app (encrypted)" if voice_config._api_key else "$ELEVENLABS_API_KEY"
        return Check("voice key", "ok", f"ElevenLabs key present ({source}) — she can listen and speak")
    from kotoba.core.llm import looks_placeholder

    raw = (os.getenv("ELEVENLABS_API_KEY", "") or "").strip()
    if raw and looks_placeholder(raw):
        return Check("voice key", "warn",
                     "$ELEVENLABS_API_KEY still holds the placeholder copied from .env.example — put "
                     f"your real key in {_env_file()}, or run `kotoba setup`. Text still works.")
    return Check("voice key", "warn",
                 f"no ElevenLabs key, so no voice — set $ELEVENLABS_API_KEY in {_env_file()} or run "
                 "`kotoba setup`. Text still works.")


def _machine_name() -> Check:
    """Which machine this is, said FIRST, because it decides what the lines under it mean.

    Doctor named the Python, the soul, the database and eleven other things and never once said what
    it was standing on — so the one command whose job is to explain why she will not run was the one
    place a Windows user could not learn that this is Windows. Never a failure: no operating system is
    a problem to fix, and the two that behave differently say so on their own line."""
    return Check("platform", "ok", host.describe())


def _terminal() -> Check:
    """Whether her interactive terminal can run here at all.

    It is built on the POSIX terminal layer — cbreak, raw mode, TIOCGWINSZ — and Windows ships none of
    those modules, so the import fails before anything draws. Saying that here matters because the
    extras section below reports the opposite in good faith: `prompt_toolkit`, `rich` and `pillow`
    install fine on Windows, so `cli  ok  installed` is true and useless. A warn, not a fail — every
    other way in works, and the message names them rather than leaving a stranger with a dead end."""
    missing = host.missing_terminal_modules()
    if not missing:
        return Check("terminal", "ok", "her interactive terminal can run here")
    return Check("terminal", "warn",
                 f"no interactive terminal — it needs the POSIX terminal layer ({', '.join(missing)}), "
                 "which this system does not have, and the `cli` extra below cannot supply it. `kotoba` "
                 "with no arguments starts the backend and the web UI instead and opens your browser; "
                 "`kotoba --once`, `setup`, `doctor`, `serve` and `discord` are unaffected.")


def _python() -> Check:
    version = ".".join(str(n) for n in sys.version_info[:3])
    if sys.version_info < (3, 11):
        return Check("python", "fail", f"{version} — she needs 3.11 or newer")
    return Check("python", "ok", version)


def _soul() -> Check:
    from kotoba.soul.loader import DEFAULT_SOUL_PATH, resolve_soul_path

    try:
        return Check("soul", "ok", str(resolve_soul_path(os.getenv("SOUL_PATH", DEFAULT_SOUL_PATH))))
    except FileNotFoundError as e:
        return Check("soul", "fail", f"{e} — she has no personality to load, so nothing starts")


async def _database() -> Check:
    """Opening it IS the check: `connect` runs the migrations, so a stale schema surfaces here."""
    from kotoba.db.database import Database

    db = Database(os.getenv("DATABASE_URL", "sqlite:///./kotoba.db"))
    fresh = not Path(db.path).is_file()
    try:
        await db.connect()
        try:
            async with db.conn.execute("PRAGMA user_version") as cur:
                schema = (await cur.fetchone())[0]
        finally:
            await db.close()
    except Exception as e:
        return Check("database", "fail", f"{db.path} cannot be opened or migrated: {e}")
    from kotoba.paths import DB_NAME, db_dir, home_dir
    # Only for the one that is genuinely beside the package. Asked as "not the home" it also fired on a
    # DATABASE_URL somebody had deliberately pointed at another disk, and on a home reached through a
    # symlink or a relative path — telling all three to move a file that was exactly where they put it.
    here = Path(db.path).resolve()
    old_place = here.name == DB_NAME and here.parent == db_dir().resolve() != home_dir().resolve()
    where = "  Beside the package, not in your home — move it there to keep it across upgrades." if old_place else ""
    return Check("database", "ok",
                 f"{db.path} — schema v{schema}{', created just now' if fresh else ''}{where}")


def _file_perms() -> Check:
    """Whether the master key and the database are actually kept to this account.

    It is asked by DOING it, not by reading the platform: the grant is applied by a helper that logs its
    failures and raises nothing, on purpose, so a machine whose files stayed world-readable behaved
    exactly like one that did not — and one of them was here."""
    from kotoba.core import perms
    from kotoba.paths import home_dir

    where = home_dir()
    # The mkdir was OUTSIDE the try, so a home that cannot be made killed the whole report — on the one
    # command whose job is to explain why something will not work. And it made a home that nothing else
    # had asked for, on a run whose paths all pointed elsewhere.
    try:
        if not where.is_dir():
            return Check("permissions", "skip", f"{where} does not exist yet — nothing to keep private")
        ok, said = perms.self_check(where)
    except OSError as e:
        return Check("permissions", "warn", f"could not be tested in {where}: {e}")
    if not ok:
        # WARN, not fail: she runs perfectly well either way, and `fail` printed "problems to fix before
        # she runs" over an install that runs. A filesystem with no ACLs at all would say it for ever.
        return Check("permissions", "warn",
                     f"the master key and the database are not private to this account — {said}")
    if said == "POSIX modes":
        return Check("permissions", "ok", "0600 on the master key, the database and every atomic write")
    return Check("permissions", "ok", f"granted to {said} alone, and to nobody else")


def _encryption() -> Check:
    if not _installed("cryptography"):
        return Check("encryption", "fail",
                     "cryptography is missing, so no API key can be saved or read — it is a required "
                     f"dependency, so this install is broken.  "
                     f"pip install --force-reinstall {DIST_NAME}")
    return Check("encryption", "ok",
                 f"cryptography {_dist_version('cryptography')} — saved keys are encrypted at rest")


async def _saved_secrets() -> Check:
    """Every secret the master key can no longer read, not just the one she thinks with.

    The llm key gets its own line below and says exactly this when it is the casualty. It rarely is the
    only one: a lost or replaced master key orphans the row it encrypted AND the ElevenLabs key, the
    other provider's key, and every MCP credential — each of which fails somewhere else entirely, as a
    voice that went quiet or a server that stopped signing in. They are re-entered, never recovered, so
    the report has to name them together or the person fixes one and meets the next by surprise."""
    from kotoba.core import keystore
    from kotoba.db.database import Database

    db = Database(os.getenv("DATABASE_URL", "sqlite:///./kotoba.db"))
    try:
        await db.connect()
        try:
            async with db.conn.execute("SELECT name, value FROM saved_keys ORDER BY name") as cur:
                rows = [(r["name"], r["value"]) for r in await cur.fetchall()]
        finally:
            await db.close()
    except Exception:
        return Check("saved keys", "skip", "not read — the database above says why")

    orphans = [n for n, v in rows if keystore.is_encrypted(v) and keystore.decrypt(v) is None]
    if not orphans:
        return Check("saved keys", "ok",
                     f"{_count(len(rows), 'secret')} stored, all readable" if rows
                     else "none stored yet")
    return Check("saved keys", "fail",
                 f"{_count(len(orphans), 'saved secret')} cannot be decrypted — the master key changed "
                 f"or was lost (KOTOBA_MASTER_KEY, else {keystore._key_file()}). Affected: "
                 f"{', '.join(orphans)}. Each has to be entered again; none can be recovered.")


async def _llm() -> Check:
    """Run the real startup: the preload it does is the only thing that decrypts a key saved in the app."""
    from kotoba.core import engine as core_engine
    from kotoba.core import keystore, llm, providers

    try:
        engine = await core_engine.start(tickers=False)
    except Exception as e:
        return Check("llm key", "fail", f"startup failed before the key could be read: {e}")
    spec = providers.get_spec()
    name = f"llm:{spec.id}:api_key"
    try:
        client = llm.get_client()
        in_app = bool(await engine.db.get_key(name))
        saved = {row["name"] for row in await engine.db.list_key_names()}
        model = llm.model_name()
    finally:
        await core_engine.stop(engine)

    if client is not None:
        source = "saved in the app (encrypted)" if in_app else f"${spec.key_env}"
        if not providers.serves_model(model, spec.id):
            return Check("llm key", "fail", _mismatch(spec, model, source))
        return Check("llm key", "ok", f"{spec.label} · {model} · key {source}")
    if name in saved:
        return Check("llm key", "fail",
                     f"a {spec.label} key is saved but cannot be decrypted — the master key changed or "
                     f"was lost (KOTOBA_MASTER_KEY, else {keystore._key_file()}). Run `kotoba setup` "
                     "to enter it again.")
    if os.getenv(spec.key_env, "").strip():
        return Check("llm key", "fail",
                     f"${spec.key_env} still holds the placeholder copied from .env.example — put your "
                     f"real {spec.label} key in {_env_file()}, or run `kotoba setup`")
    return Check("llm key", "fail",
                 f"no {spec.label} key — run `kotoba setup`, or set ${spec.key_env} in {_env_file()}")


def _mismatch(spec, model: str, source: str) -> str:
    """A good key pointed at somebody else's model. `provider` and `model` are two settings and only
    one of them moves when a first run is abandoned between them, so this pair is reachable by
    pressing Enter — and it reported `ok … Ready.` while every turn came back 404."""
    from kotoba.core.first_run import model_owner

    owner = model_owner(model, spec.id)
    return (f"{spec.label} · key {source} — but the model is {model}, which {spec.label} does not "
            f"serve{f' ({owner.label} does)' if owner is not None else ''}, so every turn comes back "
            f"empty. Run `kotoba setup`, or `/set model` to one of theirs.")


def _sandbox() -> Check:
    from kotoba.core import sandbox

    backend = sandbox.backend_name()
    if backend == "docker":
        if sandbox.sandbox_available_sync():
            return Check("sandbox", "ok",
                         "docker — her commands run in a container, and only a dangerous one asks you first")
        return Check("sandbox", "fail",
                     "KOTOBA_SANDBOX=docker but no daemon answers — `shell` and `execute_code` are dead. "
                     "Start Docker, or set KOTOBA_SANDBOX=local")
    if backend == "none":
        return Check("sandbox", "warn", "none — `shell` and `execute_code` are not offered at all")
    from kotoba.core import approval

    if approval._windows_shell():
        return Check("sandbox", "ok",
                     "local — her commands run on this machine, each one gated by your approval "
                     "(KOTOBA_SANDBOX=docker to isolate them)")
    return Check("sandbox", "ok",
                 "local — her commands run on this machine; a plain read inside her workdir runs "
                 "unasked, anything else is gated by your approval (KOTOBA_SANDBOX=docker to isolate them)")


def _extras() -> list[Check]:
    checks: list[Check] = []
    for name, modules, cost in _EXTRAS:
        if all(_installed(m) for m in modules):
            checks.append(Check(name, "ok", "installed"))
        else:
            checks.append(Check(name, "warn", f'{cost}.  pip install "{DIST_NAME}[{name}]"'))
    return checks


def _env_file() -> str:
    """Where a key can be put by hand. A wheel has no `api/.env` to edit, and sending somebody to one
    is sending them to a file they cannot find."""
    from kotoba.paths import _CLONE

    return "api/.env" if _CLONE else "the environment"


def _machine() -> list[Check]:
    return [_node(), _browser(), _ripgrep()]


def _node() -> Check:
    if shutil.which("npx") is None:
        return Check("node", "warn",
                     "not found — the MCP servers she installs run through `npx`, so those cannot start "
                     "(a remote HTTP server, or one with a command of its own, still can).  "
                     "https://nodejs.org")
    return Check("node", "ok", f"{_version_of('node')} — npx-launched MCP servers can run")


def _browser() -> Check:
    from kotoba.core.pdf import browser_is_snap, chromium_path, snap_scratch

    found = chromium_path()
    if found is None:
        return Check("browser", "warn",
                     "no Chromium-family browser (brave, chromium, chrome) — no report PDFs and no "
                     "browser automation")
    if browser_is_snap(found):
        if snap_scratch(found) is None:
            return Check("browser", "warn",
                         f"{found} is a snap and has never been run, so `~/snap/{Path(found).name}` "
                         "does not exist yet — the only directory it may write. Start it once for "
                         "report PDFs; browser automation works either way.")
        return Check("browser", "ok",
                     f"{found} — report PDFs and browser automation. A snap sees its own /tmp and no "
                     "dotted directory, so renders go through its own area instead.")
    return Check("browser", "ok", f"{found} — report PDFs and browser automation")


def _ripgrep() -> Check:
    if shutil.which("rg") is None:
        return Check("ripgrep", "warn",
                     "not found — `search_files` silently drops to a literal, non-regex scan of the "
                     "working directory.  https://github.com/BurntSushi/ripgrep")
    return Check("ripgrep", "ok", f"{_version_of('rg')} — regex file search")


async def _mcp() -> list[Check]:
    """Connecting each saved server IS the check — a stdio one that dies on startup fails here and
    nowhere else. Never a hard failure: she runs without any of them."""
    from kotoba.core import logs
    from kotoba.core.mcp.config import load_servers

    if not _installed("mcp"):
        return [Check("servers", "skip", "not asked — install the mcp extra first")]
    if not load_servers():
        return [Check("servers", "ok", "none configured — she runs on her built-in tools")]

    from kotoba.core import engine as core_engine
    from kotoba.core.mcp.client import MCPManager
    from kotoba.db.database import Database

    checks: list[Check] = []
    db = Database(os.getenv("DATABASE_URL", "sqlite:///./kotoba.db"))
    manager = MCPManager()
    try:
        await db.connect()
        await manager.start()
        _stored, hydrated = await core_engine.hydrated_servers(db)
        checks = [await _server(manager, name, cfg) for name, cfg in sorted(hydrated.items())]
    except Exception as e:
        checks = [Check("servers", "warn", f"none could be asked: {e}")]
    finally:
        try:
            await manager.aclose()  # stops the children AND takes their tools back out of the registry
        finally:
            await db.close()
    checks.append(Check("log", "ok", f"{logs.path()} — what each of them printed while starting"))
    return checks


async def _server(manager, name: str, cfg: dict) -> Check:
    from kotoba.core.mcp.client import _AuthRequired

    try:
        tools = await manager.connect(name, cfg)
    except _AuthRequired as need:
        return Check(name, "warn", f"{need.reason} — connect it from Settings, MCP")
    except Exception as e:
        return Check(name, "warn", f"does not start: {e}")
    if not tools:
        return Check(name, "warn", "started but offers no tool she can call")
    return Check(name, "ok", _count(len(tools), "tool"))


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _dist_version(distribution: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(distribution)
    except PackageNotFoundError:
        return "?"


def _version_of(binary: str) -> str:
    """Presence is the check; the version is a courtesy, so anything unexpected degrades to a word.

    `encoding` is named because a tool answering in the console codepage decodes with the LOCALE one,
    and the UnicodeDecodeError that follows is neither an OSError nor a SubprocessError — it would
    escape this and take doctor down, on the one command whose job is to explain what is wrong."""
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=3)
    except Exception:
        return "installed"
    lines = (result.stdout.strip() or result.stderr.strip()).splitlines()
    return lines[0] if lines else "installed"


def _paint(status: str, out: TextIO, plain: bool = False) -> str:
    if plain or os.getenv("NO_COLOR") or not out.isatty() or not host.ansi_available():
        return f"{status:<4}"
    return f"\033[{_COLOURS[status]}m{status:<4}\033[0m"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"
