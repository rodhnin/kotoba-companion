"""The jailed filesystem action tools: read, write, patch and search.

Every path is resolved against the context's workdir and nothing may reach outside it. The patch
half carries the interesting rules: it matches exactly first and whitespace-fuzzily second, and it
refuses — rather than guessing — whenever the snippet it was given is empty or not unique."""
from __future__ import annotations

import asyncio

from kotoba.tools import ToolContext
from kotoba.tools.action import file_read, file_write, patch, search_files


def _ctx(tmp_path):
    return ToolContext(db=None, session_id="t", workdir=tmp_path, mode="work")


def test_write_then_read_roundtrip(tmp_path):
    ctx = _ctx(tmp_path)

    async def go():
        w = await file_write.execute({"path": "a/b.txt", "content": "hello\nworld"}, ctx)
        r = await file_read.execute({"path": "a/b.txt"}, ctx)
        return w, r

    w, r = asyncio.run(go())
    assert "Wrote" in w
    assert "hello" in r and "world" in r
    assert (tmp_path / "a" / "b.txt").read_text(encoding="utf-8") == "hello\nworld"


def test_read_missing_file(tmp_path):
    r = asyncio.run(file_read.execute({"path": "nope.txt"}, _ctx(tmp_path)))
    assert "no file" in r.lower()


def test_write_traversal_blocked(tmp_path):
    ctx = _ctx(tmp_path)
    r = asyncio.run(file_write.execute({"path": "../escape.txt", "content": "x"}, ctx))
    assert "outside" in r.lower()
    assert not (tmp_path.parent / "escape.txt").exists()


def test_read_traversal_blocked(tmp_path):
    r = asyncio.run(file_read.execute({"path": "../../etc/passwd"}, _ctx(tmp_path)))
    assert "outside" in r.lower()


def test_patch_exact(tmp_path):
    ctx = _ctx(tmp_path)

    async def go():
        await file_write.execute({"path": "f.py", "content": "x = 1\ny = 2\n"}, ctx)
        return await patch.execute({"path": "f.py", "old_string": "x = 1", "new_string": "x = 42"}, ctx)

    res = asyncio.run(go())
    assert "Updated" in res
    assert "x = 42" in (tmp_path / "f.py").read_text(encoding="utf-8")


def test_patch_fuzzy_whitespace(tmp_path):
    """The snippet is spaced differently from the file, so the exact match fails and the
    whitespace-normalised one has to carry it."""
    ctx = _ctx(tmp_path)

    async def go():
        await file_write.execute({"path": "f.py", "content": "def  foo( a ,b ):\n    return a + b\n"}, ctx)
        return await patch.execute(
            {"path": "f.py", "old_string": "def foo( a ,b ):", "new_string": "def foo(a, b):"}, ctx
        )

    res = asyncio.run(go())
    assert res and "Updated" in res
    assert "def foo(a, b):" in (tmp_path / "f.py").read_text(encoding="utf-8")


def test_patch_not_found_returns_failure(tmp_path):
    """Nothing matched, so the tool returns the failure sentinel and the loop narrates a FAIL."""
    ctx = _ctx(tmp_path)

    async def go():
        await file_write.execute({"path": "f.txt", "content": "alpha"}, ctx)
        return await patch.execute({"path": "f.txt", "old_string": "zzz", "new_string": "q"}, ctx)

    assert asyncio.run(go()) is None


def test_search_files_finds_matches(tmp_path):
    ctx = _ctx(tmp_path)

    async def go():
        await file_write.execute({"path": "one.txt", "content": "needle here\nother"}, ctx)
        await file_write.execute({"path": "sub/two.txt", "content": "no match\nNEEDLE again"}, ctx)
        return await search_files.execute({"query": "needle"}, ctx)

    res = asyncio.run(go())
    assert "one.txt" in res and "needle" in res.lower()


def test_read_pagination(tmp_path):
    ctx = _ctx(tmp_path)
    body = "\n".join(f"line{i}" for i in range(100))

    async def go():
        await file_write.execute({"path": "big.txt", "content": body}, ctx)
        return await file_read.execute({"path": "big.txt", "start": 0, "limit": 10}, ctx)

    res = asyncio.run(go())
    assert "line0" in res and "line9" in res
    assert "line50" not in res
    assert "more lines" in res


# --- audit regressions: patch must refuse a degenerate or a non-unique snippet ----------------------
def test_patch_empty_old_string_refused():
    """An empty or whitespace-only `old` used to match everywhere, silently prepending `new`.

    It is refused as `empty` now. A real unique snippet still replaces, and the whitespace-fuzzy
    match still works while it stays unique."""
    from kotoba.tools.action.patch import _apply_patch

    assert _apply_patch("hello world", "", "XXX") == ("hello world", "empty")
    assert _apply_patch("hello world", "   ", "XXX") == ("hello world", "empty")
    assert _apply_patch("hello world", "world", "there") == ("hello there", "ok")
    assert _apply_patch("a    b", "a b", "X")[1] == "ok"


def test_patch_refuses_non_unique_old_string():
    """A snippet that appears twice must NOT quietly patch the first hit — it is refused as
    `notunique` and the text comes back unchanged, and a wider surrounding snippet resolves it.

    The fuzzy fallback refuses on the same ground: with `a  b` and `a   b` there is no exact match
    for `a b` at all, but the whitespace-normalised pattern matches BOTH, so it refuses rather than
    guesses."""
    from kotoba.tools.action.patch import _apply_patch

    txt = "x = 1\ny = 2\nx = 1\n"
    out, status = _apply_patch(txt, "x = 1", "x = 99")
    assert status == "notunique" and out == txt
    assert _apply_patch(txt, "y = 2\nx = 1", "y = 2\nx = 99")[1] == "ok"
    assert _apply_patch("a  b\n\na   b\n", "a b", "Z")[1] == "notunique"


def test_write_file_rejects_oversize_content(tmp_path):
    """6 MB is over `file_write._MAX_BYTES` (5 MB): the write is refused and nothing lands on disk,
    while a normal file beside it still writes."""
    import asyncio
    from kotoba.tools.action import file_write
    from kotoba.tools import ToolContext
    ctx = ToolContext(db=None, session_id="w", mode="work")
    ctx.workdir = tmp_path
    huge = "A" * (6 * 1024 * 1024)
    msg = asyncio.run(file_write.execute({"path": "big.txt", "content": huge}, ctx))
    assert "too big" in msg.lower()
    assert not (tmp_path / "big.txt").exists()
    ok = asyncio.run(file_write.execute({"path": "ok.txt", "content": "hi"}, ctx))
    assert "Wrote" in ok and (tmp_path / "ok.txt").read_text(encoding="utf-8") == "hi"
