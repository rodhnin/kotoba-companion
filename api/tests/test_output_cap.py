"""A browser_snapshot of a big page can exceed the default 16k tool-output cap, which would truncate
away the element ref the model needs. browser_* tools get a larger cap; everything else keeps 16k (the
cap exists to keep tokens-per-minute sane on chatty MCP *-fetch tools)."""
from __future__ import annotations

import kotoba.core.loop as loop


def test_cap_tool_text_respects_explicit_cap():
    """Under the cap it is handed back untouched; over it, truncated and said to be."""
    big = "x" * 22_000
    assert loop._cap_tool_text(big, 24_000) == big
    assert len(loop._cap_tool_text(big, 16_000)) < len(big)
    assert "truncated" in loop._cap_tool_text(big, 16_000)


def test_browser_gets_larger_output_cap():
    assert loop._output_cap("browser__browser_snapshot") >= 24_000
    assert loop._output_cap("browser__browser_navigate") >= 24_000
    assert loop._output_cap("browser__browser_snapshot") > loop._MAX_TOOL_OUTPUT_CHARS


def test_non_browser_keeps_default_cap():
    """Everything that is not a browser tool keeps the 16k default."""
    assert loop._output_cap("web_extract") == loop._MAX_TOOL_OUTPUT_CHARS
    assert loop._output_cap("shell") == loop._MAX_TOOL_OUTPUT_CHARS
    assert loop._MAX_TOOL_OUTPUT_CHARS == 16_000
