"""One sentence naming two preferences in Spanish produced three remembered entries: the loop saved
the two facts in Spanish, and a proactive extractor then added a combined English restatement,
because keyword de-dup cannot match across languages.

The extractor is now told what is already remembered — including this turn's own saves — and
instructed to skip facts already covered; both writers are pinned to atomic English facts so
write-time de-dup can net whatever is left. These tests cover the plumbing and the contracts; the
semantic skip itself is the model's job."""
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


def _fake_extract(payload: dict, captured: list[str]):
    content = json.dumps(payload)

    async def _extract(prompt, *, max_output_tokens=512):
        captured.append(prompt)
        return content

    return _extract


def _run(monkeypatch, msg: str, payload: dict) -> list[str]:
    import kotoba.core.llm as llm
    import kotoba.core.memory as memory

    captured: list[str] = []
    monkeypatch.setattr(llm, "utility_extract", _fake_extract(payload, captured))
    asyncio.run(memory.extract_and_save_memory(msg, db=None))
    return captured


def test_extractor_prompt_carries_facts_saved_this_turn(mem, monkeypatch):
    """The turn's memory_write calls land first, in Spanish; the extractor must SEE them in its
    prompt, or it re-extracts the same facts in English and the store grows a third entry."""
    assert mem.append_fact("Le gusta el té de jazmín", "preferences") is True
    assert mem.append_fact("Su color favorito es el índigo", "preferences") is True
    captured = _run(
        monkeypatch,
        "recuerda: mi bebida favorita es el té de jazmín y mi color el índigo",
        {"user_name": None, "companion_name": None, "facts": []},
    )
    prompt = captured[0]
    assert "ALREADY REMEMBERED" in prompt
    assert "Le gusta el té de jazmín" in prompt
    assert "Su color favorito es el índigo" in prompt
    assert len(mem.existing_facts()) == 2  # no third combined English restatement


def test_extractor_prompt_on_empty_store(mem, monkeypatch):
    captured = _run(
        monkeypatch,
        "mi bebida favorita es el té de jazmín y mi color el índigo",
        {"facts": [{"fact": "Prefers jasmine tea", "topic": "preferences"},
                   {"fact": "Favorite color is indigo", "topic": "preferences"}]},
    )
    assert "(nothing yet)" in captured[0]
    facts = mem.existing_facts()
    assert "Prefers jasmine tea" in facts
    assert "Favorite color is indigo" in facts
    assert len(facts) == 2  # two genuinely distinct facts both kept, atomically


def test_new_fact_still_saved_alongside_known_ones(mem, monkeypatch):
    mem.append_fact("Prefers jasmine tea", "preferences")
    _run(monkeypatch, "I work as a security engineer",
         {"facts": [{"fact": "Works as a security engineer", "topic": "work"}]})
    facts = mem.existing_facts()
    assert "Works as a security engineer" in facts
    assert len(facts) == 2


def test_same_language_rewording_collapses_on_write(mem):
    """With both writers pinned to English, the fuzzy keyword de-dup is the deterministic net: a
    reworded fact is refused, a genuinely different one is kept."""
    assert mem.append_fact("Prefers jasmine tea", "preferences") is True
    assert mem.append_fact("Likes jasmine tea", "drinks") is False
    assert mem.append_fact("Favorite color is indigo", "preferences") is True
    assert len(mem.existing_facts()) == 2


def test_contracts_pin_english_and_atomic():
    import kotoba.core.memory as memory
    from kotoba.tools.builtin import memory_write

    assert "{known}" in memory._EXTRACT_PROMPT
    assert "ENGLISH" in memory._EXTRACT_PROMPT and "ATOMIC" in memory._EXTRACT_PROMPT
    desc = memory_write.SCHEMA["description"]
    assert "ENGLISH" in desc and "ONE atomic fact" in desc


def test_extractor_is_shown_the_topics_already_in_use(mem, monkeypatch):
    """`Invent whatever topic best groups each item (free-form)` is what this prompt asked for, and on
    the live store it got 78 topics for 175 facts — 35 of those names are siblings of a bigger one
    ("reminder" beside "reminders", six spellings of "research"). The extractor calls append_fact
    directly with no tools, so nothing but the prompt can tell it what drawers exist."""
    mem.append_fact("Wants a reminder to pay rent", "reminders")
    mem.append_fact("Prefers dark mode", "preferences")
    captured = _run(monkeypatch, "anything", {"facts": []})
    prompt = captured[0]
    assert "ALREADY IN USE" in prompt
    assert "reminders" in prompt and "preferences" in prompt


def test_the_topic_list_is_capped_not_the_whole_store(mem, monkeypatch):
    for i in range(30):
        mem.append_fact(f"alpha{i} beta{i} gamma{i}", f"topic{i:02d}")
    captured = _run(monkeypatch, "anything", {"facts": []})
    assert "+18 more topics" in captured[0]
    assert "topic29" not in captured[0]  # bounded: it grew past a thousand chars unbounded


def test_an_empty_store_still_formats(mem, monkeypatch):
    captured = _run(monkeypatch, "anything", {"facts": []})
    assert "(none yet)" in captured[0] and "{topics}" not in captured[0]
