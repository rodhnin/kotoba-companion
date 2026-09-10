"""Asked for the LATEST of something, she searched for a year she remembered, not the real one.

Asked who won the last Ballon d'Or, she searched with "2024" baked in and answered "Rodri, 2024" with
citations — a confidently cited stale answer, worse than none. Fix: no year in a "latest / last /
current" query, read from one constant shared by the companion prompt and the work loop. A/B on that
constant, n=10 per arm: guidance removed, 10/10 queries carried a year, 3/10 the stale 2024, 1/10 wrong;
guidance present, 3/10 carried a year (always current), 0/10 carried 2024, 10/10 correct. Residual: ~30%
still name the current year, not a failure since the answer stays right. Do not weaken the constant
without re-running that A/B."""
from __future__ import annotations

import kotoba.core.tool_guidance as tg
from kotoba.soul.prompt import build_system_prompt


def _companion_prompt() -> str:
    soul = {"name": "Kotoba", "personality": "warm", "address_style": "name",
            "emotional_rules": "", "quirks": ""}
    return build_system_prompt(soul, "", [], session_id="s")


def test_guidance_says_dont_put_a_year_in_a_latest_query():
    g = tg.SEARCH_RECENCY_GUIDANCE.lower()
    assert "do not put a year" in g
    assert "latest" in g and "current" in g
    assert "only when the user named one" in g   # a user-supplied year is still honored


def test_companion_prompt_carries_it_verbatim_from_the_single_source():
    """The failing turn was a COMPANION turn, and `guidance_for()` returns "" there.

    So the companion prompt has to embed the constant itself. A paraphrase would be free to drift away
    from the wording the A/B above actually measured."""
    assert tg.SEARCH_RECENCY_GUIDANCE in _companion_prompt()


def test_work_loop_gets_it_when_web_search_is_offered():
    with_search = tg.guidance_for({"web_search", "write_file"}, "work")
    without_search = tg.guidance_for({"write_file"}, "work")
    assert tg.SEARCH_RECENCY_GUIDANCE in with_search
    assert tg.SEARCH_RECENCY_GUIDANCE not in without_search


def test_builtin_web_search_is_visible_to_the_guidance_gate():
    """web_search's schema is {"type": "web_search"} with NO "name", so the loop's old
    `{t.get("name") for t in ...}` set contained None and this guidance would never have shipped in work
    mode. The loop now falls back to "type" — keep that true."""
    from kotoba.tools.registry import discover, schemas_for

    discover()
    offered = schemas_for("work", {"read", "write", "exec", "network"})
    names = {t.get("name") or t.get("type") for t in offered}
    assert "web_search" in names
    assert None not in names
    assert tg.SEARCH_RECENCY_GUIDANCE in tg.guidance_for(names, "work")
