"""Three places name her fourteen emotions, and nothing compared them.

CONTRIBUTING.md says they "must agree exactly" and `lib/expressions.ts` says its union "MUST equal"
the backend's — both stated as invariants, neither instrumented. They agree today, which is exactly
the state in which a drift lands unnoticed: the browser would drop a face the backend still emits.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from kotoba.core.emotions import VALID_EMOTIONS
from kotoba.soul.prompt import EMOTIONS

REPO = Path(__file__).resolve().parents[2]
TS = REPO / "lib" / "expressions.ts"

pytestmark = pytest.mark.skipif(not TS.is_file(),
                                reason="the frontend lives in the repository, not the wheel")


def _union_from_ts() -> set[str]:
    """The `Emotion` union, read as the browser's own contract rather than as a copy of ours."""
    src = TS.read_text(encoding="utf-8")
    body = src.split("export type Emotion", 1)[1].split(";", 1)[0]
    return set(re.findall(r'"([a-z]+)"', body))


def test_the_browser_union_equals_the_backend_list():
    ts, py = _union_from_ts(), set(VALID_EMOTIONS)
    assert ts == py, (
        f"the browser and the backend disagree — only in the backend: {sorted(py - ts)}; "
        f"only in the browser: {sorted(ts - py)}")


def test_the_prompt_names_exactly_those_emotions():
    told = {w.strip() for w in EMOTIONS.split(",") if w.strip()}
    assert told == set(VALID_EMOTIONS), (
        f"she is told a different list than the code accepts — only in the prompt: "
        f"{sorted(told - set(VALID_EMOTIONS))}; missing from it: {sorted(set(VALID_EMOTIONS) - told)}")
