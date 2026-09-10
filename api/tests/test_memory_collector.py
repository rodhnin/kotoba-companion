"""Audit regression — the proactive memory collector actually saves the facts the LLM extracts.

Bug found in audit: the extraction prompt returns facts as {"fact","topic"} OBJECTS (Memory v2.1), but
the handler only accepted bare strings (`isinstance(fact, str)`), so EVERY collected fact was silently
dropped and topics were ignored — "she remembers things about you" quietly stopped working.
"""
from __future__ import annotations

import asyncio
import importlib
import json

import pytest


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "memory"))
    import kotoba.core.user_memory as um

    importlib.reload(um)
    return um


def _fake_extract(payload: dict):
    """Stand-in for llm.utility_extract: returns the canned JSON text the Responses API would produce."""
    content = json.dumps(payload)

    async def _extract(prompt, *, max_output_tokens=512):
        return content

    return _extract


def test_object_shaped_facts_are_saved_with_their_topic(mem, monkeypatch):
    import kotoba.core.memory as memory

    payload = {
        "user_name": None,
        "companion_name": None,
        "facts": [
            {"fact": "Has a dog named Luna", "topic": "pets"},
            {"fact": "Works as a security engineer", "topic": "work"},
        ],
    }
    import kotoba.core.llm as _llm; monkeypatch.setattr(_llm, "utility_extract", _fake_extract(payload))

    asyncio.run(memory.extract_and_save_memory("I have a dog Luna and I'm a security engineer", db=None))

    facts = mem.existing_facts()
    assert any("Luna" in f for f in facts), "object-shaped fact was dropped (the bug)"
    assert any("security engineer" in f for f in facts)
    slugs = {s for s, _ in mem.list_topics()}
    assert {"pets", "work"} <= slugs, f"topics not filed correctly: {slugs}"


def test_bare_string_facts_still_supported(mem, monkeypatch):
    import kotoba.core.memory as memory

    payload = {"user_name": None, "companion_name": None, "facts": ["Loves matcha lattes"]}
    import kotoba.core.llm as _llm; monkeypatch.setattr(_llm, "utility_extract", _fake_extract(payload))

    asyncio.run(memory.extract_and_save_memory("matcha is the best", db=None))
    assert any("matcha" in f.lower() for f in mem.existing_facts())


def test_empty_and_malformed_facts_do_not_crash(mem, monkeypatch):
    import kotoba.core.memory as memory

    payload = {"facts": [None, 123, {"topic": "x"}, {"fact": "  "}, {}]}  # all junk
    import kotoba.core.llm as _llm; monkeypatch.setattr(_llm, "utility_extract", _fake_extract(payload))
    # Must not raise and must save nothing.
    asyncio.run(memory.extract_and_save_memory("noise", db=None))
    assert mem.existing_facts() == []
