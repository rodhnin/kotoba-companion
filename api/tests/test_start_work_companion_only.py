"""Delegation tools are companion-only: offering them in work mode made work mode delegate to itself.

Found end-to-end. `start_work` was offered in WORK mode, so the work-mode model (a low-effort reasoner)
kept calling `start_work` to "do the browser task" — each call hitting the one-work-per-session guard
("you're already working") — instead of driving the `browser_*` tools directly. A site login deadlocked
that way: the credentials were collected (`ask_user`/`ask_secret`) but the loop kept calling `start_work`
and never navigated."""
from __future__ import annotations

import types

import kotoba.tools.registry as reg
from kotoba.tools.registry import ToolSpec, schemas_for


def _names(mode):
    return {s.get("name") for s in schemas_for(mode=mode, allow_risk={"read", "write", "exec", "network"})}


def setup_module():
    # Register a fake start_work (core toolset) the way the real one is, so the gate is exercised even if
    # discovery order differs.
    mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": "start_work"},
                                __name__="kotoba.tools.builtin.start_work", execute=None)
    reg.register(ToolSpec(name="start_work", module=mod, schema=mod.SCHEMA, toolset="core", risk="read"))


def test_start_work_offered_in_companion():
    assert "start_work" in _names("companion")


def test_start_work_NOT_offered_in_work():
    assert "start_work" not in _names("work")


def test_cancel_work_excluded_from_work():
    mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": "cancel_work"},
                                __name__="kotoba.tools.builtin.cancel_work", execute=None)
    reg.register(ToolSpec(name="cancel_work", module=mod, schema=mod.SCHEMA, toolset="core", risk="read"))
    assert "cancel_work" in _names("companion")
    assert "cancel_work" not in _names("work")


def test_remember_image_and_recall_image_are_offered_in_both_modes():
    """The image-memory tools are NOT delegation tools, and were wrongly filtered out with them.

    `remember_image` used to be work-only, on the grounds that saving a FOUND image is a browse-and-keep
    flow that stalls a voice turn. That is true of the web-fetch path only, and the restriction left the
    commonest case — keep the image the user just attached, bytes already in hand — with no tool at all,
    so she wrote a `memory_write` sentence and told the user she had kept the picture."""
    for name in ("remember_image", "recall_image"):
        mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": name},
                                    __name__=f"tools.builtin.{name}", execute=None)
        reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA, toolset="memory", risk="write"))
    assert "remember_image" in _names("work") and "remember_image" in _names("companion")
    assert "recall_image" in _names("work") and "recall_image" in _names("companion")
