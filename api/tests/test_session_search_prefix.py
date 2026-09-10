"""session_search returned nothing for a word that was right there: FTS5's default tokenizer indexes
whole tokens, so a singular query never matched its own plural — "latencia" got 0 hits against a real
index while `LIKE '%latencia%'` found 2. Spanish inflects by suffix, so this is not an edge case, it is
most of the language.

The fix is FTS5's PREFIX query (`"tok" *`) on tokens of 4+ characters — no migration or re-index needed,
unlike a `prefix=` index or the trigram tokenizer (which also drops `rank`). The length floor bounds
recall vs noise: `"la"` matched 212 turns, `"la" *` matched 937. These tests run against a real sqlite
FTS5 index, not a stub.
"""
from __future__ import annotations

import asyncio

import pytest

import kotoba.tools.builtin.session_search as ss
from kotoba.db.database import Database

CORPUS = [
    "Las latencias de la voz bajaron mucho con flash v2.5",
    "Calcula los primeros veinte numeros primos y guardalos",
    "Hablamos de expresiones faciales del avatar",
    "La configuracion del tunel de Cloudflare",
]


class _Ctx:
    def __init__(self, db):
        self.db = db


_n = 0


def _search(tmp_path, query: str) -> str:
    global _n
    _n += 1

    async def go():
        db = Database("sqlite:///" + str(tmp_path / f"fts{_n}.db"))
        await db.connect()
        try:
            await db.conn.execute("INSERT OR IGNORE INTO sessions (id) VALUES ('s1')")
            for text in CORPUS:
                await db.insert_turn("s1", "user", text)
            return await ss.execute({"query": query}, _Ctx(db))
        finally:
            await db.close()

    return asyncio.run(go())


def test_singular_query_finds_the_plural_in_the_index(tmp_path):
    """The exact live failure: `latencia` must now reach `latencias`."""
    out = _search(tmp_path, "latencia")
    assert "latencias" in out, out


def test_exact_matches_still_work(tmp_path):
    """flash → 2 and primos → 8 on the live index; the prefix change must not lose the exact hits."""
    assert "flash" in _search(tmp_path, "flash")
    assert "primos" in _search(tmp_path, "primos")


@pytest.mark.parametrize("query,expected", [
    ("expresion", "expresiones"),
    ("configuracion", "configuracion"),
])
def test_other_inflections(tmp_path, query, expected):
    assert expected in _search(tmp_path, query)


def test_a_word_that_is_not_there_still_finds_nothing(tmp_path):
    """Prefix matching widens recall; it must not turn into 'everything matches'."""
    out = _search(tmp_path, "blender")
    assert "Nothing came up" in out, out


def test_multi_token_query_still_ands(tmp_path):
    assert "latencias" in _search(tmp_path, "latencia flash")
    assert "Nothing came up" in _search(tmp_path, "latencia blender")


# --- the expression itself -------------------------------------------------------------------------

def test_long_tokens_get_the_wildcard_short_ones_do_not():
    assert ss._sanitize("latencia") == '"latencia" *'
    assert ss._sanitize("voz") == '"voz"', "3 chars: exact, or 'la'/'de' would flood the top-5"
    assert ss._sanitize("la voz flash") == '"la" "voz" "flash" *'


@pytest.mark.parametrize("nasty", ['* OR *', 'AND NEAR(a b)', '"unbalanced', '(((', 'x*y', '^start'])
def test_operator_injection_is_neutralised(tmp_path, nasty):
    """Every token stays quoted, so FTS5 operators in user speech are literals, never syntax errors."""
    out = _search(tmp_path, nasty)
    assert isinstance(out, str) and out, out


def test_empty_query_short_circuits():
    assert "No search terms" in asyncio.run(ss.execute({"query": "   "}, None))
