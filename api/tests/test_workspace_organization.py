"""Workspace organization: screenshots filed by source, make_report persisted to reports/ and to
memory, and a dedicated scratch dir wired as $TMPDIR for shell and code execution.

Relies on the autouse _isolate_user_state fixture (conftest) → KOTOBA_FILES_DIR / DATABASE_URL etc. point
at a throwaway tmp dir, so these never touch the real ~/.kotoba.
"""
from __future__ import annotations

import asyncio
import types

import pytest


# --- screenshots filed by source ------------------------------------------------------------------

@pytest.mark.parametrize("tool,folder", [
    ("browser__take_screenshot", "browser"),
    ("browser__navigate", "browser"),
    ("mcp__blender__get_viewport_screenshot", "blender"),
    ("blender_render", "blender"),
    ("execute_code", "desktop"),
    ("", "desktop"),
])
def test_capture_folder_by_source(tool, folder):
    """A capture is filed under the folder for whatever produced it; anything unrecognised, including
    a missing tool name, is the desktop."""
    from kotoba.core.loop import _capture_folder

    assert _capture_folder(tool) == folder


# --- scratch dir / $TMPDIR ------------------------------------------------------------------------

def test_scratch_dir_exists_and_outside_files(monkeypatch, tmp_path):
    """Scratch is a real directory, and it lives OUTSIDE the Files library — everything in the library
    shows up in the user's Files panel, and temporary junk must never clutter it."""
    monkeypatch.setenv("KOTOBA_TMP_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    from kotoba.core.workspace import scratch_dir
    from kotoba.core import file_library

    sd = scratch_dir()
    assert sd.exists() and sd.is_dir()
    assert not str(sd).startswith(str(file_library.library_dir().resolve()))


def test_sandbox_env_points_tmp_at_scratch(monkeypatch, tmp_path):
    """All three temp-dir variables a subprocess might read point at scratch, so a command that
    writes a temp file writes it somewhere we clean up — and the environment scrub that removes
    secrets still happens."""
    monkeypatch.setenv("KOTOBA_TMP_DIR", str(tmp_path / "scratch"))
    from kotoba.core.sandbox.local import _scrubbed_env
    from kotoba.core.workspace import scratch_dir

    env = _scrubbed_env()
    sd = str(scratch_dir())
    assert env.get("TMPDIR") == sd and env.get("TMP") == sd and env.get("TEMP") == sd
    monkeypatch.setenv("MY_SECRET_TOKEN", "shh")
    assert "MY_SECRET_TOKEN" not in _scrubbed_env()


def test_clean_scratch_removes_old(monkeypatch, tmp_path):
    """The cleaner is age-based: a two-day-old file goes, a fresh one stays, and the count it returns
    is the number actually removed."""
    import os
    import time

    monkeypatch.setenv("KOTOBA_TMP_DIR", str(tmp_path / "scratch"))
    from kotoba.core.workspace import clean_scratch, scratch_dir

    sd = scratch_dir()
    old = sd / "old.txt"
    old.write_text("x")
    os.utime(old, (time.time() - 48 * 3600, time.time() - 48 * 3600))
    fresh = sd / "fresh.txt"
    fresh.write_text("y")
    removed = clean_scratch(max_age_hours=24)
    assert removed == 1 and not old.exists() and fresh.exists()


# --- make_report persists to reports/ + memory ----------------------------------------------------

def test_make_report_saves_html_and_memory(monkeypatch, tmp_path):
    """One make_report call leaves four traces: an .html under the workspace's reports/ folder named
    from a slug of the title, an artifact event announcing it, a durable memory note under the
    "reports" topic so she can refer back to it later, and a spoken return that says it was saved."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "files"))
    events = []

    async def fake_emit_task(session_id, kind, **data):
        events.append({"kind": kind, **data})

    monkeypatch.setattr("kotoba.core.events.emit_task", fake_emit_task)

    from kotoba.tools.action import make_report
    from kotoba.core import file_library, user_memory

    ctx = types.SimpleNamespace(session_id="s-report")
    args = {"title": "Sprint Cleanup", "summary": "Did the thing.", "steps": ["a", "b"], "results": ["ok"]}
    out = asyncio.run(make_report.execute(args, ctx))

    files = [f["path"] for f in file_library.list_all()]
    report = next((p for p in files if p.startswith("reports/") and p.endswith(".html")), None)
    assert report is not None, f"no report html in {files}"
    assert "sprint-cleanup" in report
    assert any(e["kind"] == "artifact" and e.get("path", "").startswith("reports/") for e in events)
    facts = user_memory.topic_facts("reports")
    assert any("Sprint Cleanup" in f for f in facts), f"no report memory in {facts}"
    assert "Files" in out


# --- work-loop guidance tells her how to organize -------------------------------------------------

def test_work_guidance_includes_org_and_scratch():
    """Work mode is told where things go. A companion turn gets none of it — that turn is governed by
    the SOUL prompt alone, and injecting work guidance into it would change how she talks."""
    from kotoba.core.tool_guidance import guidance_for

    g = guidance_for({"shell", "write_file", "make_report"}, "work")
    assert "KEEP FILES ORGANIZED" in g
    assert "reports/" in g and "screenshots/" in g and "TMPDIR" in g
    assert guidance_for({"web_search"}, "companion") == ""


def test_delegation_guidance_only_when_delegate_offered():
    """The advice to split independent work into subagents appears only when `delegate` is actually
    in the toolset — dangling a tool the model cannot call is worse than saying nothing."""
    from kotoba.core.tool_guidance import guidance_for

    g = guidance_for({"delegate", "web_search", "write_file"}, "work")
    assert "SPLIT INDEPENDENT WORK" in g and "delegate" in g
    assert "SPLIT INDEPENDENT WORK" not in guidance_for({"web_search", "write_file"}, "work")
