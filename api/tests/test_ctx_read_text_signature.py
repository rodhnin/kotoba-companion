"""`ctx.read_text` is our own helper — passing `encoding=` to it is a TypeError.

The publish path reads a just-written file back through it, inside a `try/except Exception: pass`. An
encoding sweep added `encoding="utf-8"` to that call, so the very first statement of the try raised and
`_publish_file` never ran: in the default setup the new/edited badge never appeared, and with a separate
KOTOBA_WORKSPACE_DIR the file never entered the library at all — so a recreated sandbox could not
rehydrate it and "edit the HTML you just made" lost the file. Nothing failed loudly.
"""
from __future__ import annotations

import asyncio
import inspect
import tempfile
from pathlib import Path


from kotoba.tools import ToolContext
import kotoba.core.loop


def test_ctx_read_text_takes_no_encoding():
    params = set(inspect.signature(ToolContext.read_text).parameters)
    assert "encoding" not in params, "if this gains an encoding param, the loop may pass one again"


def test_it_decodes_as_utf8_on_its_own():
    """The reason the caller must NOT pass one: the helper already fixes it."""
    wd = Path(tempfile.mkdtemp())
    (wd / "acentos.md").write_text("café — 日本語\n", encoding="utf-8")
    ctx = ToolContext(db=None, session_id="s", workdir=wd)
    assert asyncio.run(ctx.read_text("acentos.md")) == "café — 日本語\n"


def test_the_publish_read_back_call_is_the_bare_form():
    """Source check: the call site sits inside `except Exception: pass`, so a TypeError there is silent
    and no behavioural test would notice. Pin the shape instead."""
    src = Path(kotoba.core.loop.__file__).read_text(encoding="utf-8")
    assert "await ctx.read_text(path)" in src
    assert "ctx.read_text(path, encoding" not in src


def test_publish_actually_runs_after_a_write(monkeypatch, tmp_path):
    """End to end on the helper the loop calls: the file reaches the library index, badge and all."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "library"))
    monkeypatch.delenv("KOTOBA_WORKSPACE_DIR", raising=False)
    import importlib

    import kotoba.core.file_library as fl

    importlib.reload(fl)
    wd = fl.library_dir()
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "notes.md").write_text("hola", encoding="utf-8")

    from kotoba.core.loop import _publish_file

    _publish_file("s", wd, "notes.md", "hola")

    listed = {f["path"]: f for f in fl.list_all()}
    assert "notes.md" in listed, "the write never reached the Files panel index"
    assert listed["notes.md"]["action"] in ("created", "edited")
    assert listed["notes.md"].get("seen") is not True, "a fresh write must still carry its badge"
