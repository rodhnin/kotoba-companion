"""agentic_loop must accept a `mode`. In companion mode the model gets COMPANION_TOOLSETS — which now
INCLUDE terminal+code (shell/execute_code, so she can run a command mid-call, gated by approval), report
(one template fill) and file (jailed to the library) — but mcp/subagent/browser stay work-only."""
from __future__ import annotations

import inspect

import kotoba.core.loop as loop
from kotoba.tools import registry


def test_agentic_loop_accepts_mode_param():
    sig = inspect.signature(loop.agentic_loop)
    assert "mode" in sig.parameters
    assert sig.parameters["mode"].default == "companion"


def test_companion_includes_terminal_and_file_excludes_heavier_action_tools():
    registry.discover()
    names = {s.get("name") for s in registry.schemas_for(mode="companion")}
    # terminal/code ARE offered (run a command mid-call, gated by approval), and so are report and file
    assert "shell" in names and "execute_code" in names
    assert "make_report" in names
    for f in ("write_file", "read_file", "patch", "search_files"):
        assert f in names, f
    # but the heavier action toolsets stay work-only
    for heavy in ("mcp_install", "delegate"):
        assert heavy not in names, heavy


def test_work_schemas_include_heavy_toolsets():
    registry.discover()
    names = {s.get("name") for s in registry.schemas_for(mode="work")}
    assert "shell" in names and "mcp_install" in names


def test_start_and_cancel_work_available_in_companion():
    registry.discover()
    names = {s.get("name") for s in registry.schemas_for(mode="companion")}
    assert "start_work" in names and "cancel_work" in names
