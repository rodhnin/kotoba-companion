"""The CLI's seam flush is a FULL leak flush on purpose — never `partial=True`.

`Session._drain` handles FLUSH_SENTINEL (the tool boundary) with `leak.flush()`, not the spoken
chain's `flush(partial=True)`. The partial hold exists so TTS never SPEAKS half a JSON; the
terminal speaks nothing, and holding is measurably worse here: the kept fragment merges with the
next iteration's first words, a truncated recipient completes against them, and the strip eats
real reply text. This file pins both halves of that judgment — the full flush guarantees post-tool
words always survive intact, and the merge really does eat text if partial is used instead. If the
second pin ever fails, the filter changed and the seam decision deserves re-judging."""
from __future__ import annotations

import asyncio

from kotoba.cli.session import Session
from kotoba.core import stream as sse

POST_TOOL = "Listo: hay 14 grados."


class _Stub:
    _echo = Session._echo
    _face = Session._face


def _drain(chunks: list) -> tuple[list[str], str]:
    async def main():
        q: asyncio.Queue = asyncio.Queue()
        for c in chunks:
            q.put_nowait(c)
        q.put_nowait(sse.DONE_SENTINEL)
        said: list[str] = []
        echoed: list[str] = []
        final = await Session._drain(_Stub(), q, said, on_text=echoed.append)
        return echoed, final

    return asyncio.run(main())


def test_cut_recipient_leak_is_stripped_at_the_seam_and_eats_nothing():
    echoed, final = _drain(
        ['Voy a buscarlo. {"query": "tokio"}to=functions.web_', sse.FLUSH_SENTINEL, POST_TOOL]
    )
    assert POST_TOOL in final, f"post-tool text was eaten: {final!r}"
    assert "to=functions" not in final, f"the leak survived the seam: {final!r}"


def test_bare_json_prints_at_the_boundary_it_happened_not_glued_at_the_end():
    """The documented cost: a bare-JSON fragment (no recipient yet, so the regex cannot strip it)
    shows at the tool boundary. Held instead, the SAME bytes resurface at end of turn glued to the
    reply's first words — never a net removal, only a worse placement."""
    echoed, final = _drain(
        ['Voy a buscarlo. ', '{"query": "tokio"}', sse.FLUSH_SENTINEL, POST_TOOL]
    )
    assert POST_TOOL in final, f"post-tool text was eaten: {final!r}"
    assert '{"query": "tokio"}' in echoed, "the fragment must surface at the seam, where it happened"
    assert echoed[-1] == POST_TOOL, "post-tool text must arrive on its own, not glued to the fragment"


def test_the_rejected_alternative_still_eats_real_text():
    """Why the seam is not `partial=True`: the held cut-recipient fragment completes against the
    next iteration's first word and the strip removes it. The day this stops reproducing, the
    filter changed — re-judge the seam decision instead of deleting this test."""
    f = sse.ToolCallLeakFilter()
    f.feed('Voy a buscarlo. {"query": "tokio"}to=functions.web_')
    assert f.flush(partial=True) == "", "the partial hold must keep a possible leak"
    merged = f.feed(POST_TOOL) + f.flush()
    assert "Listo" not in merged, (
        f"holding no longer eats the first post-tool word ({merged!r}) — the CLI seam "
        "decision rests on this and should be revisited"
    )
