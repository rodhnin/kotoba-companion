"""What gets written down must not contain a block she said twice.

The spoken chain drops the repeat, but the persisted turn is the raw text the loop returned, and
history is re-sent on every later request — so a doubled turn pays its own length again on every turn
until it scrolls out of the recent-turns window, and shows the model a worked example of stuttering.
It is a standing token cost, not an untidy record. So the last test here walks the package's AST and
fails on any assistant insert_turn that does not go through collapse_repeats, including one that does
not exist yet — the same RepeatCollapse object the speech chain feeds, so what she is heard to say and
what is written down cannot disagree about what a repeat is.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

from kotoba.core import stream as sse
from kotoba.db.database import Database

SRC = pathlib.Path(sse.__file__).resolve().parents[1]

SAID = ("[excited] Me pongo con eso ahora mismo, y además no se me olvida: llama a tu hermana "
        "cuando tengas un momento. Ve mirando la pantalla, que te lo voy dejando listo.")


def test_the_doubled_announcement_is_persisted_once():
    assert sse.collapse_repeats(SAID + SAID) == SAID


def test_a_reply_with_no_repeat_is_untouched():
    """Anything else must reach the history byte for byte — markdown, URLs and fences included."""
    text = ("Listo, Jordan. Te lo dejé en `research/pixi.md`:\n\n"
            "```py\nprint('hola')\n```\n\nFuente: https://pixijs.com/ — dime si quieres más.")
    assert sse.collapse_repeats(text) == text


def test_a_source_cited_twice_keeps_both_links_whole():
    """Measured on the live history: the splitter cuts a URL at its dots, so the tail of a link
    repeated in one reply looked like a repeated sentence. Speech never sees this (UrlFilter runs
    first) but the record keeps her links on purpose, and four real turns lost part of one."""
    text = ("Lo confirmé en la web oficial. ([live2d.jp](https://www.live2d.jp/en/company/?utm_source=openai))\n"
            "Y el programa JUKU sale ahí también. ([live2d.jp](https://www.live2d.jp/en/company/?utm_source=openai))\n")
    assert sse.collapse_repeats(text) == text
    assert sse.collapse_repeats(text).count("https://www.live2d.jp/en/company/?utm_source=openai") == 2


def test_a_blank_line_between_the_copies_does_not_split_the_block():
    """The shape found in the live history: she repeated a greeting AND the paragraph under it. The
    empty line between them used to end the match, leaving the greeting standing twice."""
    block = ("[happy] Ya lo abrí por aquí, Jordan — te leo lo importante enseguida.\n\n"
             "El archivo arranca con un resumen estructurado y sigue con la cadena de propiedad "
             "más reciente que el informe encontró.")
    assert sse.collapse_repeats(block + block).count("Ya lo abrí por aquí") == 1


def test_a_held_repeat_at_the_very_end_is_never_lost():
    """The last sentence can arrive with no closing '.', which is exactly when a held one could be
    dropped instead of released."""
    text = "Hola qué tal, Jordan. Te lo dejo listo. Hola qué tal, Jordan"
    assert sse.collapse_repeats(text) == text


def test_the_speech_chain_and_the_history_agree_on_what_a_repeat_is():
    f = sse.ForbiddenPhraseFilter()
    spoken = f.feed(SAID + SAID) + f.flush()
    assert spoken.count("Ve mirando la pantalla") == sse.collapse_repeats(SAID + SAID).count(
        "Ve mirando la pantalla"
    ) == 1


def test_the_report_finds_the_old_rows_and_repairs_nothing(tmp_path):
    """Rows written before the collapse existed stay exactly as the user said them; the entry point
    only names them, and is inert until someone calls it."""
    async def go():
        db = Database("sqlite:///" + str(tmp_path / "report.db"))
        await db.connect()
        try:
            await db.ensure_session("s1")
            await db.insert_turn("s1", "assistant", SAID + SAID)
            await db.insert_turn("s1", "assistant", "Una respuesta normal, sin repetición.")
            await db.insert_turn("s1", "user", SAID + SAID)  # only her turns are audited
            found = await db.repeated_turn_report()
            kept = await db.fetch_recent_turns("s1", limit=10)
            return found, kept
        finally:
            await db.close()

    found, kept = asyncio.run(go())
    assert [f["removed"] for f in found] == [len(SAID)], found
    assert found[0]["chars"] == len(SAID) * 2 and found[0]["collapsed_chars"] == len(SAID)
    assert any(t["content"] == SAID + SAID for t in kept), "the report must not rewrite history"


def _assistant_inserts() -> list[tuple[str, int, str]]:
    """Every `insert_turn(..., "assistant", <content>)` in the package, as (file, line, content src)."""
    found = []
    for path in SRC.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call) or getattr(node.func, "attr", "") != "insert_turn":
                continue
            roles = [a for a in node.args if isinstance(a, ast.Constant) and a.value == "assistant"]
            if not roles:
                continue
            content = node.args[node.args.index(roles[0]) + 1]
            found.append((path.name, node.lineno, ast.get_source_segment(src, content) or ""))
    return found


def test_every_chain_that_persists_a_reply_collapses_repeats():
    sites = _assistant_inserts()
    missing = [(f, ln) for f, ln, content in sites if "collapse_repeats" not in content]
    assert not missing, (
        f"these write an assistant turn without collapsing a repeated block: {missing} — "
        "history re-sends it on every later request"
    )
    assert len(sites) >= 3, f"expected the three known chains (/v1, voice WS, CLI), found {sites}"
