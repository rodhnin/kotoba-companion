"""What the completion list and `/help` advertise for a command's argument has to be what the
command actually takes. Examples are asserted against the LIVE handlers, never a copy kept here,
so a part added or renamed (`/help` once grew a `keys` part nothing advertised) fails here.

The sweep asks whether the advertised word is answered DIFFERENTLY from a word that names nothing:
a part the command lacks falls into the same refusal branch as nonsense, and the two outputs come
back identical otherwise — undetectable by just asserting non-emptiness.

`/model` refuses nothing, so it is checked separately: whether the provider actually publishes the
name the table advertises."""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest

from kotoba.cli import slash, state
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console
from kotoba.db.database import Database


class _Grants:
    """The two calls `/approvals rm N` makes, and the one answer that matters: which one went."""

    def __init__(self, *patterns: str) -> None:
        self.saved = [{"pattern": p, "scope": "command"} for p in patterns]
        self.revoked: list[str] = []

    async def list_approved_commands(self) -> list[dict]:
        return list(self.saved)

    async def delete_approved_command(self, pattern: str) -> None:
        self.revoked.append(pattern)


def wired(db=None) -> tuple[App, io.StringIO]:
    caps = Caps(color="none", background="dark", unicode=True, width=80, height=40,
                g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1", engine=SimpleNamespace(db=db, mcp=None),
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app, buf


def said(app, name: str, arg: str = "") -> str:
    asyncio.run(slash.run(app, commands.Command(name, arg)))
    return app.screen.console.file.getvalue()


def example(name: str) -> str:
    """The argument the entry advertises: what follows the command's own name in its description."""
    _, _, rest = commands.COMMANDS[name].partition(f"{name} ")
    return rest.strip()


def test_the_help_entry_advertises_a_part():
    assert example("/help"), "/help takes a part and its row is the only place that says so"


def test_the_part_it_advertises_is_one_help_answers_to():
    app, _ = wired()

    out = said(app, "/help", example("/help"))

    assert "no part of /help called" not in out


def test_the_number_the_approvals_entry_advertises_revokes_that_row():
    grants = _Grants("git", "ls", "docker")
    app, _ = wired(grants)

    said(app, "/approvals", example("/approvals"))

    assert grants.revoked == ["docker"], "the advertised form must revoke the row it names"


def test_no_entry_advertises_a_command_that_is_not_its_own():
    for name, description in commands.COMMANDS.items():
        named = [w for w in description.split() if w.startswith("/")]
        assert all(w == name for w in named), f"{name} advertises {named}"


def unanswerable(advertised: str) -> str:
    """The advertised argument with its last word replaced by one that names nothing — a run of `z`, or
    of `9` where the command counts rows.

    The same LENGTH, because a refusal quotes the word it refused and then wraps around it: a word one
    cell shorter moves the wrap, and a wrap that moved is a difference between two refusals that has
    nothing to do with either word. Only the invalid half is ever written down here; a valid one would
    be the copy of the live table this file exists to do without."""
    head, _, last = advertised.rpartition(" ")
    return f"{head} {('9' if last.isdigit() else 'z') * len(last)}".strip()


def last_word(arg: str) -> str:
    return arg.rpartition(" ")[2]


def _needs_a_database(app, tmp_path):
    async def open_it() -> Database:
        db = Database("sqlite:///" + str(tmp_path / "entries.db"))
        await db.connect()
        return db

    return open_it


def _needs_saved_grants(app, tmp_path):
    app.session.engine.db = _Grants("git", "ls", "docker")


def _needs_a_long_job(app, tmp_path):
    app.works = [state.Work("read the release notes", 1)]


def _needs_something_handed_over(app, tmp_path):
    app.gifts = [state.Gift("report", str(tmp_path / "notes.md"), "a report")]


def _needs_two_helpers(app, tmp_path):
    app.last_helpers = [state.Helper(f"h{i}", "researcher", f"goal {i}", state="ok")
                        for i in (1, 2)]


def _needs_the_file_it_names(app, tmp_path):
    """`~` is the throwaway HOME the test set, so the advertised spelling has to expand to reach this."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)


#: command -> what has to exist for the advertised argument to name something.
WIRING = {"/settings": _needs_a_database, "/approvals": _needs_saved_grants,
          "/work": _needs_a_long_job, "/open": _needs_something_handed_over,
          "/helpers": _needs_two_helpers, "/attach": _needs_the_file_it_names}

#: Every entry that advertises an argument, except `/model` — see the module docstring.
SWEPT = ("/help", "/settings", "/set", "/approvals", "/work", "/open", "/attach", "/helpers", "/face")


def answered(name: str, arg: str, tmp_path) -> str:
    """What ONE command printed, on an app wired with whatever its argument names.

    The database is opened and closed inside the loop the command runs in: an aiosqlite connection that
    outlives the loop that made it fails on the way out."""
    app, buf = wired()
    seed = WIRING[name](app, tmp_path) if name in WIRING else None

    async def go() -> None:
        db = await seed() if seed is not None else None
        if db is not None:
            app.session.engine.db = db
        try:
            await slash.run(app, commands.Command(name, arg))
        finally:
            if db is not None:
                await db.close()

    asyncio.run(go())
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _never_really_launch_anything(monkeypatch):
    """`/open 1` hands its target to the desktop for real. The advertised form has to REACH that call —
    that is the whole of what makes it usable — so what is stubbed is the spawn and never the branch."""
    monkeypatch.setattr(slash, "_launch", lambda target: None)


@pytest.mark.parametrize("name", SWEPT)
def test_the_argument_an_entry_advertises_is_one_the_command_answers_to(name, tmp_path, monkeypatch):
    """The table is the only place an argument is advertised, so a word the handler does not have is a
    word nobody can be told about — and it reads, on screen, exactly like nonsense."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    advertised = example(name)
    real = answered(name, advertised, tmp_path)
    nonsense = answered(name, unanswerable(advertised), tmp_path)

    bogus = unanswerable(advertised)
    assert real != nonsense, (
        f"{name} {advertised!r} is answered exactly like {bogus!r} — the table is advertising an "
        f"argument the command does not have")
    # Every refusal in the family QUOTES the word it refused, so two of them differ by that word and by
    # nothing else. Swapping it back is what tells a real answer from a refusal wearing the right word.
    assert real != nonsense.replace(last_word(bogus), last_word(advertised)), (
        f"{name} {advertised!r} gets the same refusal {bogus!r} gets, with its own word in it")


def test_the_model_the_entry_advertises_is_one_a_provider_publishes(tmp_path, monkeypatch):
    """`/model` refuses nothing — it writes whatever name it is handed — so the sweep above cannot see
    it. What it DOES say is whether the provider publishes the name, and an entry advertising a model
    nobody serves would print that warning over its own example."""
    from kotoba.core import providers

    advertised = example("/model")
    serves = [spec for spec in providers.PROVIDERS.values()
              if advertised in {choice.id for choice in spec.models + spec.code_models}]
    assert serves, (f"no provider lists {advertised!r} — the entry advertises a name that prints the "
                    f"typo warning over its own example")
    assert not slash._unlisted(advertised, {"provider": serves[0].id, "base_url": ""})


def test_no_advertised_argument_escapes_the_sweep():
    """The guard on the guard: a new example added to the table has to be checked by something here, or
    the file goes back to proving that somebody typed a word after a command."""
    advertising = {name for name in commands.COMMANDS if example(name)}
    assert advertising == set(SWEPT) | {"/model"}, sorted(advertising.symmetric_difference(
        set(SWEPT) | {"/model"}))
