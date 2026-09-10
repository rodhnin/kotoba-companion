"""The tool registry: ToolSpec, discover, schemas_for, and the check cache."""
from __future__ import annotations

import types

import pytest

import kotoba.tools as tools
import kotoba.tools.registry as reg
from kotoba.tools.registry import ToolSpec, schemas_for


# --- helpers ---------------------------------------------------------------

def _names(schemas):
    return {s.get("name", "__builtin__") for s in schemas}


@pytest.fixture
def clean_registry():
    """Snapshot the real registry + cache, let the test mutate, then restore."""
    saved_reg = dict(reg._REGISTRY)
    saved_cache = dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved_reg)
        reg._check_cache.clear()
        reg._check_cache.update(saved_cache)


def _fake_module(name, **attrs):
    schema = attrs.pop("schema", {"type": "function", "name": name})
    mod = types.SimpleNamespace(SCHEMA=schema, __name__=f"tools.action.{name}", **attrs)
    return mod


_BUILTINS = {"web_search", "web_extract", "memory_write", "memory_recall", "session_search", "todo", "clarify"}
_ACTION = {"read_file", "write_file", "patch", "search_files", "shell", "execute_code"}


def test_discover_registers_builtins_and_action_tools():
    """Both halves of the registry come up: the conversational built-ins and the action tools."""
    names = set(reg.registry().keys())
    assert _BUILTINS <= names
    assert _ACTION <= names


def test_legacy_exports_present_and_consistent():
    """The module-level exports are the registry, seen from another angle.

    web_search is a built-in, so it is not a function tool; the action tools are."""
    assert set(tools.TOOL_REGISTRY.keys()) == set(reg.registry().keys())
    assert len(tools.TOOL_SCHEMAS) == len(reg.registry())
    assert "web_search" not in tools.FUNCTION_TOOLS
    assert "memory_write" in tools.FUNCTION_TOOLS
    assert "write_file" in tools.FUNCTION_TOOLS


# --- mode / risk gating ----------------------------------------------------

def test_companion_offers_conversational_plus_terminal_and_file_not_heavier_actions():
    """Companion mode is the conversational and safe-write tools (memory, skills, cron) PLUS the
    terminal and code runners (approval-gated) and the file tools (jailed, one syscall each).

    The heavier action toolsets — mcp, subagent — stay work-only: a multi-step build belongs in a
    background task. The file tools are here because live QA found "make me a report" could not reach
    them from a call at all."""
    got = _names(schemas_for("companion"))
    assert {"shell", "execute_code"} <= got
    assert {"read_file", "write_file", "patch", "search_files"} <= got
    assert not ({"mcp_install", "delegate"} & got)


def test_companion_can_call_make_report():
    """Live QA found `report` missing from COMPANION_TOOLSETS, so a spoken "make me a report" could not
    reach make_report at all — she hand-built one with a shell heredoc and the Report panel never filled.
    It is companion-safe (one template fill, no sandbox, no approval, no follow-up; the Chromium PDF
    render lives in the /report.pdf HTTP endpoint, not in the turn), so it must be offered in BOTH
    modes."""
    assert "make_report" in _names(schemas_for("companion"))
    assert "make_report" in _names(schemas_for("work"))


def test_companion_read_only_excludes_memory_write():
    got = _names(schemas_for("companion", {"read"}))
    assert "memory_write" not in got
    assert "web_extract" in got and "session_search" in got


def test_work_mode_includes_action_tools():
    """The built-in function tools are all there, and so are the file action tools — those have no
    external dependency, so nothing can gate them out."""
    got = _names(schemas_for("work", {"read", "write", "exec", "network"}))
    assert _BUILTINS - {"web_search"} <= got
    assert {"read_file", "write_file", "patch", "search_files"} <= got


def test_empty_allow_risk_yields_no_tools():
    assert schemas_for("work", set()) == []
    assert schemas_for("companion", set()) == []


def test_companion_includes_terminal_and_file_but_not_the_heavy_toolsets(clean_registry):
    """The same gating, asked of registrations this test made rather than of the real registry.

    The terminal toolset IS in the voice call (approval-gated) and so is file, which is jailed to the
    library and therefore safer than the shell. Browser stays work-only."""
    reg.register(ToolSpec(name="shell", module=_fake_module("shell"),
                          schema={"type": "function", "name": "shell"}, toolset="terminal", risk="exec"))
    reg.register(ToolSpec(name="write_file", module=_fake_module("write_file"),
                          schema={"type": "function", "name": "write_file"}, toolset="file", risk="write"))
    reg.register(ToolSpec(name="browser__click", module=_fake_module("browser__click"),
                          schema={"type": "function", "name": "browser__click"}, toolset="browser", risk="write"))
    comp = _names(schemas_for("companion"))
    assert "shell" in comp
    assert "write_file" in comp
    assert "browser__click" not in comp
    assert "shell" in _names(schemas_for("work", {"exec"}))


def test_toolset_filter_keeps_builtins_only_for_one_toolset(clean_registry):
    """A toolset filter keeps the toolset asked for and the built-ins, which are never filtered; any
    other toolset — `todo` lives in core — drops out."""
    reg.register(ToolSpec(name="read_file", module=_fake_module("read_file"),
                          schema={"type": "function", "name": "read_file"}, toolset="file", risk="read"))
    got = _names(schemas_for("work", {"read", "write", "exec", "network"}, toolset_filter="file"))
    assert "read_file" in got
    assert "__builtin__" in got
    assert "todo" not in got


# --- check() gating, and the 30s result cache ------------------------------

def test_check_false_hides_tool(clean_registry):
    reg.register(ToolSpec(name="needs_docker", module=_fake_module("needs_docker"),
                          schema={"type": "function", "name": "needs_docker"},
                          toolset="code", risk="exec", check=lambda: False))
    assert "needs_docker" not in _names(schemas_for("work", {"exec"}))


def test_check_exception_treated_as_unavailable(clean_registry):
    """A check() that raises must not reach the loop: the tool is simply not offered."""
    def boom():
        raise RuntimeError("docker probe failed")

    reg.register(ToolSpec(name="flaky", module=_fake_module("flaky"),
                          schema={"type": "function", "name": "flaky"},
                          toolset="code", risk="exec", check=boom))
    assert "flaky" not in _names(schemas_for("work", {"exec"}))


def test_check_result_is_cached_within_ttl(clean_registry):
    """A check() may do real IO, so its result is probed at most once per _CHECK_TTL window."""
    calls = {"n": 0}

    def counting_check():
        calls["n"] += 1
        return True

    reg.register(ToolSpec(name="counted", module=_fake_module("counted"),
                          schema={"type": "function", "name": "counted"},
                          toolset="code", risk="exec", check=counting_check))
    schemas_for("work", {"exec"})
    schemas_for("work", {"exec"})
    schemas_for("work", {"exec"})
    assert calls["n"] == 1


# --- spec building edge cases ----------------------------------------------

def test_module_without_schema_is_ignored():
    mod = types.SimpleNamespace(__name__="kotoba.tools.action.not_a_tool")
    assert reg._spec_from_module(mod, "not_a_tool") is None


def test_builtin_schema_without_name_uses_fallback():
    mod = types.SimpleNamespace(SCHEMA={"type": "web_search"}, BUILT_IN=True,
                                __name__="kotoba.tools.builtin.web_search")
    spec = reg._spec_from_module(mod, "web_search")
    assert spec is not None and spec.name == "web_search" and spec.built_in is True


def test_duplicate_name_last_wins_with_warning(clean_registry, caplog):
    """Two specs under one name: the second registration wins, and the collision is warned about."""
    s1 = ToolSpec(name="dup", module=_fake_module("dup"), schema={"type": "function", "name": "dup"})
    s2 = ToolSpec(name="dup", module=_fake_module("dup2"), schema={"type": "function", "name": "dup"},
                  toolset="file")
    reg.register(s1)
    with caplog.at_level("WARNING"):
        reg.register(s2)
    assert reg.registry()["dup"].toolset == "file"
    assert any("already registered" in r.message for r in caplog.records)
