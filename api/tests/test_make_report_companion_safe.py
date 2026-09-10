"""Live QA found that `report` had been added to COMPANION_TOOLSETS, so make_report is reachable in a
normal conversation. COMPANION_TOOLSETS exists to keep slow, multi-step work OUT of a live voice turn —
ElevenLabs cuts a turn that goes silent — so these guard the property that made the move safe.

make_report fills a fixed template and returns: no sandbox, no ApprovalGate, no subprocess. The Chromium
render is in GET /api/session/{id}/report.pdf, an HTTP endpoint the user hits by clicking. If anyone ever
moves that render into the tool, the voice call starts dying and these fail first.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import kotoba.tools.action.make_report as mr


class _Boom:
    """Any attribute touch fails — proves the tool never reaches for the gate."""

    def __getattr__(self, name):
        raise AssertionError(f"make_report must not use ctx.approval (touched {name!r})")


class _Ctx:
    # deliberately NO ensure_sandbox(): an AttributeError here means the tool wanted a sandbox.
    session_id = "rep1"
    mode = "companion"
    approval = _Boom()


def _payload(n: int = 0) -> dict:
    return {
        "title": f"QA formal report {n}",
        "summary": "A realistic end-of-work summary paragraph. " * 8,
        "steps": [f"Step {i}: something that took a while, described at length." for i in range(12)],
        "results": [f"Result {i}: an outcome worth writing down." for i in range(12)],
        "files": [f"file-{i}.md — a note about it" for i in range(8)],
        "next_steps": [f"Next {i}: something to do later." for i in range(6)],
    }


def test_make_report_needs_no_sandbox_no_approval_and_no_subprocess(monkeypatch):
    def _no_subprocess(*a, **k):
        raise AssertionError("make_report must not spawn a process — the PDF render is an HTTP endpoint")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_subprocess)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _no_subprocess)

    out = asyncio.run(mr.execute(_payload(), _Ctx()))
    assert out and "Report ready" in out


def test_make_report_returns_well_inside_a_voice_turn():
    """Measured ~2ms on a full-size payload; the ceiling is loose enough not to flake and tight enough to
    catch a Chromium render (seconds) sneaking into the turn."""
    t0 = time.perf_counter()
    asyncio.run(mr.execute(_payload(1), _Ctx()))
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"make_report took {elapsed:.2f}s — too slow to run inside a live voice turn"


def test_report_is_stored_and_opened_so_the_panel_can_fill(monkeypatch):
    """The panel sat on "Preparing your report…" forever because nothing ever called this tool. Calling
    it must both stock the endpoint the viewer reads — GET /api/session/{id}/report — and emit the
    `report_ready` frame the frontend opens the panel on."""
    from kotoba.core import reports

    frames: list[tuple[str, dict]] = []

    async def fake_emit(sid, kind, **data):
        frames.append((kind, data))

    monkeypatch.setattr("kotoba.core.events.emit_task", fake_emit)
    reports.clear_report("rep1")

    asyncio.run(mr.execute(_payload(2), _Ctx()))

    html = reports.get_report("rep1")
    assert html and "QA formal report 2" in html
    ready = [d for k, d in frames if k == "report_ready"]
    assert [d["title"] for d in ready] == ["QA formal report 2"]


@pytest.mark.parametrize("mode", ["companion", "work"])
def test_offered_in_both_modes(mode):
    """The move it all rests on: make_report is in the schema list for a companion turn as well as for
    a work one."""
    from kotoba.tools.registry import discover, schemas_for

    discover()
    assert "make_report" in {s.get("name") for s in schemas_for(mode)}
