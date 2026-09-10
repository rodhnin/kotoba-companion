"""Session captures: the TRANSIENT per-session log of work screenshots + view_capture re-open. Distinct
from durable visual memory."""
from __future__ import annotations

import asyncio
import base64

import kotoba.core.session_captures as sc

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def setup_function():
    sc._log.clear()


def test_record_dedupes_by_file():
    sc.record("s1", "screenshot-1.png", "search results")
    sc.record("s1", "screenshot-2.png", "a post")
    sc.record("s1", "screenshot-1.png", "updated")  # same file → replace
    es = sc.entries("s1")
    assert len(es) == 2 and es[-1]["caption"] == "updated"


def test_prompt_block_lists_or_empty():
    assert sc.prompt_block("none") == ""
    sc.record("s2", "screenshot-3.png", "an angry rant")
    block = sc.prompt_block("s2")
    assert "RECENT CAPTURES" in block and "screenshot-3.png" in block and "view_capture" in block


class _Client:
    """Counts the requests a caption attempt actually spends."""

    def __init__(self):
        self.calls = 0

        async def create(**kw):
            self.calls += 1
            return type("R", (), {"output_text": "a search results page"})()

        self.responses = type("Responses", (), {"create": staticmethod(create)})()


def test_a_capped_pacing_wait_drops_the_caption_instead_of_spending_the_request(monkeypatch):
    """The fourth call site with this shape. `throttle` now RETURNS the seconds still owed — a positive number is the
    pacer saying the budget is still below the floor — and this call site ignored it, so a nice-to-have
    caption was fired into a window the pacer had just proved insufficient: a near-certain 429, a round
    trip, and TPM taken from the live turn that took the screenshot. Nothing awaits this coroutine
    (it is fired with create_task), so unlike the main loop it has the option `throttle` names for a
    background caller: drop the work."""
    import kotoba.core.llm as llm
    import kotoba.core.ratelimit as rl

    monkeypatch.setattr(llm, "model_name", lambda *a, **k: "gpt-5.4-mini")
    monkeypatch.setattr(llm, "model_call_kwargs", lambda *a, **k: {})

    async def owing(*a, **k):
        return 31.0

    monkeypatch.setattr(rl, "throttle", owing)
    client = _Client()
    assert asyncio.run(sc.caption_image(client, "data:image/png;base64,xx")) == ""
    assert client.calls == 0, "sent a doomed request the pacer had just refused to clear"


def test_a_clear_budget_still_captions(monkeypatch):
    """The other half: 0.0 owed means clear to send, and the enrichment must not have been paced away
    into never running."""
    import kotoba.core.llm as llm
    import kotoba.core.ratelimit as rl

    monkeypatch.setattr(llm, "model_name", lambda *a, **k: "gpt-5.4-mini")
    monkeypatch.setattr(llm, "model_call_kwargs", lambda *a, **k: {})

    async def clear(*a, **k):
        return 0.0

    monkeypatch.setattr(rl, "throttle", clear)
    client = _Client()
    assert asyncio.run(sc.caption_image(client, "data:image/png;base64,xx")) == "a search results page"
    assert client.calls == 1


def test_view_capture_reopens_saved_image(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path))
    (tmp_path / "screenshot-9.png").write_bytes(_PNG)
    import kotoba.tools.builtin.view_capture as vc

    class _Ctx:
        session_id = "s3"

    res = asyncio.run(vc.execute({"file": "screenshot-9.png"}, _Ctx()))
    assert res is not None and res.images and res.images[0].startswith("data:image/")
    assert asyncio.run(vc.execute({"file": "../../etc/passwd"}, _Ctx())) is None
    assert asyncio.run(vc.execute({"file": "nope.png"}, _Ctx())) is None
