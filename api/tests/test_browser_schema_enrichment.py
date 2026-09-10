"""The browser MCP (@playwright/mcp) ships THIN tool descriptions ("Perform click on a web page") and a
`target` param documented as "...reference from the page snapshot, OR a unique element selector". That
last clause is exactly what lets a small model pass an invented CSS selector (#email) from memory instead
of a real snapshot ref → "does not match any elements" → it stalls. We FIX the descriptions the model sees
(the single chokepoint _to_function_schema) so they force the snapshot→ref workflow and discourage selectors.
"""
from __future__ import annotations

import copy
import types

import kotoba.core.mcp.client as mc


def _tool(name, desc, schema):
    return types.SimpleNamespace(name=name, description=desc, inputSchema=schema)


_CLICK_SCHEMA = {
    "type": "object",
    "properties": {
        "element": {"type": "string", "description": "Human-readable element description"},
        "target": {"type": "string", "description": "Exact target element reference from the page snapshot, or a unique element selector"},
    },
    "required": ["target"],
}

_FILL_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Human-readable field name"},
                    "target": {"type": "string", "description": "Exact target element reference from the page snapshot, or a unique element selector"},
                    "value": {"type": "string", "description": "Value to fill in"},
                },
            },
        }
    },
}


def test_browser_snapshot_description_carries_full_workflow():
    # The full snapshot→ref→verify workflow lives on the ANCHOR tool (browser_snapshot) only — appending it
    # to every browser tool re-sent ~12k tokens of duplicated guidance each iteration (avoidable TPM cost).
    out = mc._to_function_schema("browser__browser_snapshot", _tool("browser_snapshot", "Capture a page snapshot", _CLICK_SCHEMA))
    d = out["description"].lower()
    assert "browser_snapshot" in d              # tells it to snapshot first
    assert "ref" in d                            # use the reference from the snapshot
    assert "selector" in d or "css" in d         # explicitly warns against inventing selectors
    assert "capture a page snapshot" in d        # keeps the original description too


def test_non_anchor_browser_tool_omits_full_workflow():
    # browser_click no longer carries the full workflow (the TPM fix) — only the enriched target param does.
    out = mc._to_function_schema("browser__browser_click", _tool("browser_click", "Perform click on a web page", _CLICK_SCHEMA))
    assert "perform click on a web page" in out["description"].lower()  # original description kept
    assert "how to use the browser" not in out["description"].lower()    # full workflow NOT duplicated here
    assert "snapshot" in out["parameters"]["properties"]["target"]["description"].lower()  # target still pushes ref


def test_target_param_description_pushes_snapshot_ref():
    out = mc._to_function_schema("browser__browser_click", _tool("browser_click", "x", _CLICK_SCHEMA))
    tdesc = out["parameters"]["properties"]["target"]["description"].lower()
    assert "snapshot" in tdesc
    assert "do not" in tdesc or "never" in tdesc  # discourages guessing a selector


def test_nested_fill_form_target_is_enriched():
    out = mc._to_function_schema("browser__browser_fill_form", _tool("browser_fill_form", "Fill multiple form fields", _FILL_SCHEMA))
    nested = out["parameters"]["properties"]["fields"]["items"]["properties"]["target"]["description"].lower()
    assert "snapshot" in nested and ("do not" in nested or "never" in nested)


def test_does_not_mutate_original_tool_schema():
    schema = copy.deepcopy(_CLICK_SCHEMA)
    tool = _tool("browser__browser_click", "Perform click", schema)
    mc._to_function_schema("browser__browser_click", tool)
    # the shared MCP tool object must be untouched (we deep-copy before enriching)
    assert tool.inputSchema["properties"]["target"]["description"] == _CLICK_SCHEMA["properties"]["target"]["description"]


def test_non_browser_tool_unchanged():
    schema = {"type": "object", "properties": {"path": {"type": "string", "description": "file path"}}}
    out = mc._to_function_schema("filesystem__read_file", _tool("read_file", "Read a file", schema))
    assert out["description"] == "Read a file"                       # untouched
    assert out["parameters"]["properties"]["path"]["description"] == "file path"
