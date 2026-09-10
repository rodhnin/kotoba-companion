"""Every text file we read or write must name its encoding.

`Path.read_text`/`write_text` with no `encoding=` use the process locale. That is UTF-8 here and in the
official Docker image (LANG=C.UTF-8), but a Windows clone defaults to cp1252 — and `write_text`
TRUNCATES before it encodes, so a report containing "café" or a kanji loses its content and raises a
UnicodeEncodeError that the surrounding `except ValueError` swallows.
"""
from __future__ import annotations

import re
from pathlib import Path


API = Path(__file__).resolve().parent.parent / "src"
SKIP_PARTS = {".venv", "__pycache__", "tests", "_private-archive"}
CALL = re.compile(r"\.(?:read_text|write_text)\s*\(")
# Only Path.read_text/write_text take `encoding`. `ctx.read_text` and `atomic_file.write_text` are OUR
# helpers, which fix utf-8 internally, so demanding the kwarg there is a TypeError — and the loop's
# `except` swallowed exactly that, silently disabling _publish_file. Match the RECEIVER, never the bare
# method name: `p.write_text(...)` on a real Path must still be caught.
OURS = re.compile(r"\b(?:await\s+)?(?:ctx|self|atomic_file)\.(?:read_text|write_text)\s*\(")

ACCENTED = "Informe sobre el café — añadidos y 日本語\n"


def _source_files():
    for p in sorted(API.rglob("*.py")):
        if not any(s in p.parts for s in SKIP_PARTS):
            yield p


def test_no_text_io_relies_on_the_process_locale():
    offenders = []
    for p in _source_files():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not CALL.search(line) or "encoding=" in line:
                continue
            if "async def read_text" in line or OURS.search(line):
                continue
            offenders.append(f"{p.relative_to(API)}:{i}: {line.strip()}")
    assert not offenders, "text IO without an explicit encoding:\n  " + "\n  ".join(offenders)


def test_the_file_library_round_trips_accented_and_cjk_text(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    import importlib

    import kotoba.core.file_library as fl

    importlib.reload(fl)
    rel = fl.save_text("reports/informe.md", ACCENTED)
    assert rel is not None
    assert (tmp_path / "files" / rel).read_text(encoding="utf-8") == ACCENTED


def test_the_settings_file_round_trips_non_ascii(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    import importlib

    import kotoba.core.app_settings as s

    importlib.reload(s)
    s._save({"name": "Kotoba — 言葉"})
    assert s._load()["name"] == "Kotoba — 言葉"


def test_a_report_with_accents_survives_the_citation_rewrite(tmp_path):
    """citations.complete_reports reads, appends and writes back — a locale write would empty the file."""
    import types

    from kotoba.core import citations

    md = tmp_path / "informe.md"
    md.write_text("# Informe sobre el café\n\nAlgo añadido.\n", encoding="utf-8")

    ctx = types.SimpleNamespace(workdir=tmp_path)
    ctx._sources = {"https://example.test/a": "Fuente añadida"}
    ctx._md_writes = ["informe.md"]

    done = citations.complete_reports(ctx)
    body = md.read_text(encoding="utf-8")
    assert "café" in body, "the original accented content must survive"
    assert "añadido" in body
    if done:
        assert "example.test" in body
