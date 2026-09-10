"""`@` may only name a file in her workdir — a jail worth exactly the escapes somebody tried.

`PathCompleter(get_paths=...)` reads as a jail and is not one: `get_paths` is only the base a
RELATIVE fragment joins to, so an absolute fragment, `~`, and `..` all walked straight out of it —
`@/etc/pas` offered `passwd`, `@~/` painted the whole home directory into the box, `@../` listed the
parent of her files.

Every escape here builds the completer and names the jail by hand, proving nothing about WHICH
directory the running CLI actually jails it to; the last test closes that gap — dropping the
argument would otherwise leave `@` completing wherever `kotoba` happened to start."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from conftest import needs_posix_terminal, posix_only, make_symlink
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kotoba.cli.input.prompt import Prompt, SlashCompleter
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.theme import GLYPHS_UNICODE


@pytest.fixture
def wd(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "private").mkdir(parents=True)
    (home / "private" / "diary.txt").write_text("x")
    (home / "taxes.pdf").write_text("x")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("USERPROFILE", raising=False)

    work = home / ".kotoba" / "files"
    (work / "research").mkdir(parents=True)
    (work / "research" / "notes.md").write_text("x")
    (work / "report.md").write_text("x")
    make_symlink(work / "outward", home / "private")
    make_symlink(work / "inward", work / "research")

    # The classic prefix-vs-boundary mistake: a sibling whose name merely STARTS with the workdir's.
    (home / ".kotoba" / "files-evil").mkdir()
    (home / ".kotoba" / "files-evil" / "secret.txt").write_text("x")
    return work


def offered(wd, fragment: str) -> list[tuple[str, str]]:
    """The rows `@<fragment>` puts on the glass, as (what accepting inserts, what is shown)."""
    doc = Document("@" + fragment, len(fragment) + 1)
    return [(c.text, c.display_text)
            for c in SlashCompleter(str(wd)).get_completions(doc, None)]


def labels(wd, fragment: str) -> list[str]:
    return [shown for _, shown in offered(wd, fragment)]


NOTICE = ("", SlashCompleter.OUTSIDE)

WAYS_OUT = [
    "/etc/pas",          # the reported repro: an absolute path to somebody else's business
    "/",
    "~",                 # a bare tilde lists the home's siblings by prefix
    "~/",
    "~/private/",
    "..",
    "../",
    "../../",
    "./../",
    "research/../../",   # a real subdirectory first, then out — the dirname alone looks innocent
    "../files-evil/",
    "outward",           # a symlink inside her workdir pointing out of it
    "outward/",
]


@pytest.mark.parametrize("fragment", WAYS_OUT)
def test_no_spelling_of_the_way_out_completes_anything(wd, fragment):
    assert offered(wd, fragment) == [NOTICE], f"@{fragment} escaped the workdir"


def test_nothing_from_the_home_directory_ever_reaches_the_list(wd):
    for fragment in ("~", "~/", "~/ta", "~/private/", "~/private/di"):
        assert "taxes.pdf" not in labels(wd, fragment)
        assert "diary.txt" not in labels(wd, fragment)


def test_a_symlink_that_leaves_the_workdir_is_not_offered_even_in_the_plain_listing(wd):
    """The listing itself is inside, so only the per-row check can catch this one."""
    assert labels(wd, "") == ["inward/", "report.md", "research/"]
    assert labels(wd, "out") == []


def test_a_symlink_that_stays_inside_the_workdir_still_completes(wd):
    assert labels(wd, "inward/") == ["notes.md"]


def test_a_sibling_whose_name_merely_starts_with_the_workdirs_is_not_inside_it(wd):
    evil = wd.parent / "files-evil"
    assert offered(wd, str(evil) + "/") == [NOTICE]
    assert offered(wd, str(evil) + "/sec") == [NOTICE]
    # Listing the PARENT by prefix must not leak the sibling either.
    assert labels(wd, str(wd)) == ["files/"]
    assert "secret.txt" not in labels(wd, str(wd))


@posix_only("$HOME as the home directory")
def test_an_absolute_path_to_the_workdir_itself_still_completes(wd):
    assert labels(wd, str(wd) + "/") == ["inward/", "report.md", "research/"]
    assert labels(wd, str(wd) + "/re") == ["report.md", "research/"]
    assert labels(wd, "~/.kotoba/files/re") == ["report.md", "research/"]


def test_a_subdirectory_still_completes_as_it_always_did(wd):
    assert labels(wd, "re") == ["report.md", "research/"]
    assert labels(wd, "research/") == ["notes.md"]
    assert offered(wd, "research/no") == [("tes.md", "notes.md")]


def test_a_workdir_that_is_itself_a_symlink_completes_by_either_name(tmp_path):
    """Normal on a Mac and in a container: /tmp and $HOME are symlinks there. Compare RESOLVED paths or
    every completion in a real install dies."""
    real = tmp_path / "real"
    (real / "research").mkdir(parents=True)
    (real / "report.md").write_text("x")
    link = tmp_path / "files"
    make_symlink(link, real)

    assert labels(link, "") == ["report.md", "research/"]
    assert labels(link, str(link) + "/") == ["report.md", "research/"]
    assert labels(link, str(real) + "/") == ["report.md", "research/"]


def test_the_notice_says_where_the_path_went_and_taking_it_clears_the_way_out(wd):
    row = next(iter(SlashCompleter(str(wd)).get_completions(Document("@~/private", 10), None)))
    assert row.display_text == "that's outside her workdir"
    assert "/attach" in row.display_meta_text
    assert (row.text, row.start_position) == ("", -len("~/private"))


@needs_posix_terminal
def test_the_notice_survives_prompt_toolkits_cull_and_reaches_the_glass(wd):
    """The trap, and the only test here that would have caught it: a LONE completion that would insert
    nothing is deleted by `Buffer._create_completer_coroutine` before the menu is ever drawn, so a
    notice with `start_position=0` left the box showing no list at all. Driving the real editor is what
    proves the row is on the glass; asking the completer only proves it was offered."""
    caps = Caps(color="none", background="dark", unicode=True, interactive=True, width=80,
                g=dict(GLYPHS_UNICODE))

    async def drive(typed: str) -> list[str]:
        with create_pipe_input() as pipe:
            prompt = Prompt(caps, workdir=str(wd), complete_while_typing=True,
                            input=pipe, output=DummyOutput())
            asking = asyncio.create_task(prompt.ask_async())
            await asyncio.sleep(0.1)
            pipe.send_text(typed)
            await asyncio.sleep(0.4)
            state = prompt.session.default_buffer.complete_state
            shown = [] if state is None else [c.display_text for c in state.completions]
            pipe.send_text("\r")
            await asyncio.wait_for(asking, 5)
            return shown

    assert asyncio.run(drive("@~/")) == ["that's outside her workdir"]
    assert asyncio.run(drive("@re")) == ["report.md", "research/"]


def test_the_running_cli_jails_the_box_to_her_library_and_not_to_the_cwd(tmp_path, monkeypatch):
    """Who hands the completer its jail. `SlashCompleter(workdir)` is asked for by name in every test
    above; `cli.app.run` is the one caller in the product, and `root()` falls back to `os.getcwd()` when
    nothing is passed — which in a clone is the source tree and in a shell is wherever the person was
    standing. Nothing here changes if that argument goes: the escapes are all still refused, around the
    wrong directory."""
    from kotoba.cli import app as cli_app
    from kotoba.core import file_library

    library = tmp_path / "library"
    (library / "research").mkdir(parents=True)
    (library / "report.md").write_text("x")
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(library))
    monkeypatch.chdir(tmp_path)

    caps = Caps(color="none", background="dark", unicode=True, interactive=False, width=80,
                height=24, g=dict(GLYPHS_UNICODE))
    monkeypatch.setattr(cli_app, "detect", lambda **kw: caps)
    handed: dict = {}
    built = cli_app.Prompt

    def spy(for_caps, **kw):
        handed.update(kw)
        return built(for_caps, **kw)

    async def straight_out(self) -> int:
        return 0

    monkeypatch.setattr(cli_app, "Prompt", spy)
    monkeypatch.setattr(cli_app.App, "run", straight_out)

    assert asyncio.run(cli_app.run(no_face=True)) == 0
    given = handed.get("workdir")
    assert given, "the box was built with no workdir, so `@` completes the directory kotoba was run in"
    assert Path(given).resolve() == file_library.library_dir().resolve()
    assert SlashCompleter(given).root() == library.resolve()
    assert labels(library, "re") == ["report.md", "research/"]
