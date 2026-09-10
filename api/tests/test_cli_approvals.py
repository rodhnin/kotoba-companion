"""Revoking an "always allow" grant from the terminal.

`/settings security` used to list the saved families and say "revoke them in the web app" — an
instruction an install started with `kotoba` (engine in-process, no FastAPI) cannot follow.
`DELETE /api/approvals/{pattern}` and `db.delete_approved_command` already existed; `/approvals` is
the terminal's path to them.

The listing says what each grant permits NOW, so a family the interpreter denylist has since
neutered (`sh`, `bash`, `python`…) reads as spent rather than live, and a read-only grant reads as
scoped to her workdir instead of unlimited."""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest

from kotoba.cli import settings_view, slash
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console


class _DB:
    """Only the two methods `/approvals` touches; delete really removes so a re-list reflects it."""

    def __init__(self, patterns):
        self._p = list(patterns)

    async def list_approved_commands(self):
        return [{"pattern": p, "scope": "command"} for p in self._p]

    async def delete_approved_command(self, pattern):
        self._p = [p for p in self._p if p != pattern]


def wired(patterns) -> tuple[App, io.StringIO, _DB]:
    caps = Caps(color="none", background="dark", unicode=True, width=90, g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    db = _DB(patterns)
    app.session = SimpleNamespace(session_id="s1", engine=SimpleNamespace(db=db))
    return app, buf, db


def said(app, arg: str = "") -> str:
    asyncio.run(slash.run(app, commands.Command("/approvals", arg)))
    return app.screen.console.file.getvalue()


# --- the listing says what each grant permits NOW ---------------------------------------------------

def test_grant_permits_tells_a_neutered_grant_from_a_live_one():
    assert settings_view.grant_permits("git") == "runs git without asking"
    assert settings_view.grant_permits("npm") == "runs npm without asking"
    assert settings_view.grant_permits("ls") == "runs inside her workdir without asking"
    assert settings_view.grant_permits("cat") == "runs inside her workdir without asking"
    assert settings_view.grant_permits("execute_code").startswith("runs code without asking")
    for spent in ("sh", "bash", "python", "python3", "env", "xargs", "docker", "node"):
        assert settings_view.grant_permits(spent) == "asks every time — this no longer grants anything"


def test_the_listing_shows_every_family_and_what_each_one_lets_her_do():
    """Every saved family is listed with the sentence describing what it still buys — including
    `sh`, which the denylist neutered and which must therefore not read as a live grant."""
    app, _, _ = wired(["git", "sh", "ls"])
    out = said(app)
    assert "A L W A Y S   A L L O W E D" in out               # the letter-spaced sticker chip
    assert "git" in out and "runs git without asking" in out
    assert "ls" in out and "runs inside her workdir without asking" in out
    assert "sh" in out and "asks every time" in out
    assert "/approvals rm" in out


def test_an_empty_listing_says_nothing_is_always_allowed():
    app, _, _ = wired([])
    out = said(app)
    assert "nothing is always-allowed" in out
    assert "A L W A Y S   A L L O W E D" not in out


# --- revoking -------------------------------------------------------------------------------------

def test_rm_revokes_the_family_and_says_she_will_ask_again():
    app, _, db = wired(["git", "sh"])
    out = said(app, "rm git")
    assert "revoked git" in out
    assert asyncio.run(db.list_approved_commands()) == [{"pattern": "sh", "scope": "command"}]


@pytest.mark.parametrize("verb", ["rm", "remove", "revoke", "delete"])
def test_every_revoke_spelling_works(verb):
    app, _, db = wired(["git"])
    said(app, f"{verb} git")
    assert asyncio.run(db.list_approved_commands()) == []


def test_rm_of_a_family_she_never_saved_says_so_and_changes_nothing():
    app, _, db = wired(["git"])
    out = said(app, "rm npm")
    assert "isn't one she's saved" in out
    assert asyncio.run(db.list_approved_commands()) == [{"pattern": "git", "scope": "command"}]


def test_rm_with_no_family_asks_which_one():
    app, _, db = wired(["git"])
    out = said(app, "rm")
    assert "which one" in out
    assert asyncio.run(db.list_approved_commands()) == [{"pattern": "git", "scope": "command"}]


def test_a_verb_that_is_neither_list_nor_revoke_says_what_it_takes():
    app, _, _ = wired(["git"])
    out = said(app, "please")
    assert "/approvals rm <name-or-number>" in out


# --- the stale wording is gone --------------------------------------------------------------------

def test_the_settings_security_section_no_longer_points_at_a_web_app():
    """The security section describes each grant instead of pointing at a web app that a
    terminal-only install does not have — and the neutered `sh` grant does not read as live."""
    data = {"security": {"approvals": [{"pattern": "git", "scope": "command"},
                                       {"pattern": "sh", "scope": "command"}],
                         "trust": "workspace"},
            "browser_cdp": ""}
    rows = settings_view.section_info("SECURITY", data)
    joined = " ".join(f"{left} {right} {tail}" for left, right, tail in rows)
    assert "web app" not in joined
    assert "runs git without asking" in joined
    assert "asks every time" in joined


# --- one list, one shape --------------------------------------------------------------------------

class _ScopedDB(_DB):
    """The two widths a grant comes in, side by side, the way the real table holds them."""

    def __init__(self, grants):
        self._g = list(grants)

    async def list_approved_commands(self):
        return [{"pattern": p, "scope": s} for p, s in self._g]


def wired_scoped(grants, width: int) -> App:
    caps = Caps(color="none", background="dark", unicode=True, width=width, height=40,
                g=dict(GLYPHS_UNICODE))
    console = build_console(caps, file=io.StringIO())
    console.width = width
    screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1", engine=SimpleNamespace(db=_ScopedDB(grants)))
    return app


MIXED = [("df -h", "exact"), ("ls", "command"), ("sleep 15 && echo 'terminado'", "exact"),
         ("sh", "command")]


@pytest.mark.parametrize("width", (40, 90))
def test_every_grant_draws_in_the_one_shape_whatever_its_width(width):
    """Reported live as looking wrong: the listing put an exact grant's sentence on an indented
    second row and a family's beside it on the same row, one column over — two shapes in one
    numbered list. Every grant is now the number, the line it stands for, and what it buys UNDER
    it, at one column. What tells an exact grant from a family is the SENTENCE
    (`settings_view.grant_permits`), never the shape — and the sentence wraps, so at a narrow
    window a family's is no longer clipped beside a whole one."""
    import re

    from kotoba.cli.render import rows

    lines = said(wired_scoped(MIXED, width)).split("\n")
    heads = [i for i, line in enumerate(lines) if re.match(r"^\d+\.", line)]
    assert len(heads) == len(MIXED), lines
    for n, (i, (pattern, scope)) in enumerate(zip(heads, MIXED), 1):
        head, body = lines[i], lines[i + 1]
        assert head[:rows.GRANT_COL] == f"{n}.".ljust(rows.GRANT_COL), head
        assert head[rows.GRANT_COL:].rstrip() == pattern, head
        assert body.startswith(" " * rows.GRANT_COL) and body[rows.GRANT_COL] != " ", body
        assert "…" not in body and body.strip().startswith(
            settings_view.grant_permits(pattern, scope)[:12]), body
