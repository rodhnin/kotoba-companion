"""The prompt's capability claims are written from what the turn will really be offered.

A surface can withhold tools from one speaker. If the prompt is still built from the whole toolset,
her "what I cannot do" block is wrong for every such turn, and the refusal that follows reads as a
fault instead of a rule. `schemas_for` has always taken the exclusion; `load_context` had to forward
it, and nothing pinned that it did.
"""
from __future__ import annotations

import asyncio

from kotoba.core import context


class _DB:
    async def fetch_recent_turns(self, *a, **k): return []
    async def fetch_soul_config(self): return {"name": "Kotoba", "personality": "warm"}
    async def fetch_user_profile_as_markdown(self): return ""


def _prompt(items) -> str:
    return next(m["content"] for m in items if m.get("role") == "developer")


def _run(monkeypatch, tmp_path, exclude):
    from kotoba.models.schemas import ChatRequest

    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    req = ChatRequest(messages=[{"role": "user", "content": "hola"}], session_id="s-excl")
    return asyncio.run(context.load_context(req, _DB(), "s-excl", exclude_tools=exclude))


def test_the_withheld_tool_leaves_the_prompt(monkeypatch, tmp_path):
    seen: dict = {}
    from kotoba.tools import registry

    original = registry.schemas_for

    def spy(mode, *a, **k):
        seen["exclude"] = k.get("exclude_tools")
        return original(mode, *a, **k)

    monkeypatch.setattr(registry, "schemas_for", spy)
    _run(monkeypatch, tmp_path, frozenset({"shell"}))
    assert seen["exclude"] == frozenset({"shell"}), "load_context did not forward the exclusion"


def test_nothing_is_withheld_by_default(monkeypatch, tmp_path):
    seen: dict = {}
    from kotoba.tools import registry

    original = registry.schemas_for

    def spy(mode, *a, **k):
        seen["exclude"] = k.get("exclude_tools")
        return original(mode, *a, **k)

    monkeypatch.setattr(registry, "schemas_for", spy)
    _run(monkeypatch, tmp_path, None)
    assert seen["exclude"] is None
