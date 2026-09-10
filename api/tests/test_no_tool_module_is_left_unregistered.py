"""Every tool module on disk is actually offered — the import is not the registration.

One package both imports its modules and lists them for discovery; the other has no list at all and
names its modules one by one. Either way a new file can be added, imported, reviewed and shipped
while the model never sees it, and the failure is silent: nothing raises, she simply cannot do the
thing.

This project has already paid for that shape once from the other direction — an import order that
once left her with only 15 of 29 tools and one log line. This is the same question asked of the
file system instead of the import graph."""
from __future__ import annotations

import pathlib

import kotoba.tools.action as action
import kotoba.tools.builtin as builtin
import kotoba.tools.registry as reg


def _modules_on_disk(pkg) -> set[str]:
    d = pathlib.Path(pkg.__file__).parent
    return {p.stem for p in d.glob("*.py") if p.stem != "__init__"}


def test_every_action_module_is_in_ACTION_TOOLS():
    listed = {m.__name__.rsplit(".", 1)[-1] for m in action.ACTION_TOOLS}
    missing = _modules_on_disk(action) - listed
    assert not missing, f"action tool module(s) never registered: {sorted(missing)}"


def test_every_builtin_module_reaches_the_live_registry():
    modules = {getattr(spec.module, "__name__", "").rsplit(".", 1)[-1]
               for spec in reg.registry().values()}
    missing = _modules_on_disk(builtin) - modules
    assert not missing, f"builtin tool module(s) never registered: {sorted(missing)}"


def test_the_registry_holds_a_spec_for_each_listed_action_tool():
    by_module = {getattr(spec.module, "__name__", "") for spec in reg.registry().values()}
    for mod in action.ACTION_TOOLS:
        assert mod.__name__ in by_module, f"{mod.__name__} is listed but has no ToolSpec"
