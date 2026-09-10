"""Per-session background-work status registry. One work item per session; the voice turn reads the
snapshot to report progress / announce results. In-memory, single instance."""
from __future__ import annotations

import kotoba.core.work_state as ws


def setup_function():
    ws._state.clear()
    ws._tasks.clear()


def test_start_marks_running_and_is_running():
    ws.start("s1", "build a page")
    assert ws.is_running("s1") is True
    snap = ws.get("s1")
    assert snap["status"] == "running" and snap["goal"] == "build a page"


def test_set_step_updates_running_step():
    ws.start("s1", "g")
    ws.set_step("s1", "taking screenshot")
    assert ws.get("s1")["step"] == "taking screenshot"


def test_finish_marks_done_with_summary_and_files():
    ws.start("s1", "g")
    ws.finish("s1", "made index.html", ["index.html"])
    snap = ws.get("s1")
    assert snap["status"] == "done" and snap["summary"] == "made index.html"
    assert snap["files"] == ["index.html"] and snap["announced"] is False
    assert ws.is_running("s1") is False


def test_fail_marks_failed_with_reason():
    ws.start("s1", "g")
    ws.fail("s1", "browser timed out")
    snap = ws.get("s1")
    assert snap["status"] == "failed" and snap["reason"] == "browser timed out"


def test_prompt_note_running_and_done_then_announced():
    assert ws.prompt_note("none") == ""              # no work → no note
    ws.start("s2", "build a page")
    assert "running" in ws.prompt_note("s2").lower()
    ws.finish("s2", "done it", [])
    note = ws.prompt_note("s2")
    assert "done it" in note                           # unannounced done surfaces
    ws.mark_announced("s2")
    assert ws.prompt_note("s2") == ""                  # announced → no longer surfaced


def test_get_unknown_session_is_idle():
    assert ws.get("nope")["status"] == "idle"
    assert ws.is_running("nope") is False
