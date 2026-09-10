"""Her audio tags must never reach the printed transcript — and her face must still move.

The vocabulary tags (`[warmly]`) are eaten by `AudioTagFilter`, which hands the word to the face
BEFORE any text is released; both halves are pinned here against `Session._drain`, the CLI's one
render/commit path. `_HeadTags` is the second reader, for the tag the vocabulary cannot place: the
model coins near-vocabulary tags (`[calmly] Hola…` reached a real transcript), and the text
surface rightly keeps unknown brackets mid-prose — position is the only evidence left, so only the
bracket the reply OPENS with is judged, and only when it holds nothing but lowercase letters and does
not open a markdown link. `[1]` citations, `[ok]` mid-text, `list[int]` and `[Forbes](url)` — the
brackets she legitimately writes — are pinned to survive."""
from __future__ import annotations

import asyncio

from kotoba.cli.session import Session
from kotoba.core import stream as sse

O, SEP, C = chr(0xE200), chr(0xE202), chr(0xE201)


class _Stub:
    _echo = Session._echo
    _face = Session._face


def _drain(chunks: list, *, faces: bool = True) -> tuple[str, list[str]]:
    async def main():
        q: asyncio.Queue = asyncio.Queue()
        for c in chunks:
            q.put_nowait(c)
        q.put_nowait(sse.DONE_SENTINEL)
        worn: list[str] = []
        final = await Session._drain(_Stub(), q, [], on_text=None,
                                     on_face=worn.append if faces else None)
        return final, worn

    return asyncio.run(main())


def test_a_vocabulary_tag_is_never_printed_and_still_moves_her_face():
    final, worn = _drain(["[warmly] Hola, Jordan."])
    assert "[" not in final and final == "Hola, Jordan."
    assert worn == ["affectionate"], "the tag she wrote is the face the reply wears"


def test_a_coined_tag_at_the_head_is_never_printed_even_split_across_chunks():
    final, worn = _drain(["[cal", "mly] Hola, Jordan. Puedo hacer bastantes cosas."])
    assert final == "Hola, Jordan. Puedo hacer bastantes cosas."
    assert worn == [], "an unknown word is stripped, never guessed into a face"


def test_a_glued_tag_still_hands_its_known_words_to_the_face():
    final, worn = _drain(["[sad calmly] lo siento mucho."])
    assert final == "lo siento mucho."
    assert worn == ["sad"]


def test_the_brackets_she_legitimately_writes_all_survive():
    prose = "Ves [1] la cita, list[int] compila y [ok] queda dicho."
    final, _ = _drain([prose])
    assert final == prose
    link, _ = _drain(["[Forbes](https://x.com) lo cuenta."])
    assert link == "[Forbes](https://x.com) lo cuenta."


def test_a_reply_opening_with_a_link_survives_even_split_at_the_bracket():
    final, _ = _drain(["[Forbes]", "(https://x.com) lo cuenta."])
    assert final == "[Forbes](https://x.com) lo cuenta."


def test_a_reply_opening_with_a_citation_or_a_capitalised_bracket_survives():
    final, _ = _drain(["[1] la primera referencia."])
    assert final == "[1] la primera referencia."
    final, _ = _drain(["[TODO] arreglar esto."])
    assert final == "[TODO] arreglar esto."


def test_an_unclosed_head_bracket_is_literal_text_at_end_of_stream():
    final, _ = _drain(["[sin cerrar"])
    assert final == "[sin cerrar"


def test_a_coined_tag_mid_reply_is_left_alone():
    """The head is the only position judged: rewriting unknown brackets mid-prose is the recorded
    mistake that once turned every citation into `((url))`."""
    prose = "texto normal [calmly] en medio."
    final, _ = _drain([prose])
    assert final == prose


def test_a_reference_link_definition_at_the_head_keeps_its_label():
    """`[nota]: https://…` opens a reply as often as a link does, and its label is the half that
    carries the meaning. Position alone read it as a tag and dropped it, leaving the reply opening on
    a bare colon. A `:` after the bracket is markdown for exactly the same reason a `(` is, and she is
    told to write her tag as `[tag] text` — she never punctuates it."""
    final, worn = _drain(["[nota]: https://kotoba.ai/docs\nVer arriba."])
    assert final.startswith("[nota]: https://kotoba.ai/docs")
    assert worn == []
    split, _ = _drain(["[nota]", ": https://kotoba.ai/docs"])
    assert split == "[nota]: https://kotoba.ai/docs", "and the same when the colon is a chunk late"
    assert _drain(["[warmly] Hola."])[0] == "Hola.", "her own tag is untouched by it"


def test_a_web_search_citation_run_never_reaches_the_terminal_or_the_row_she_keeps():
    """OpenAI wraps its inline citations in Private-Use-Area runs. `strip_citation_markers` has always
    taken them off the spoken chain; this drain built its own filters and never called it, so a run
    reached the screen mid-sentence as `◀cite▌turn0search0▌turn0search1◀`. `_drain`'s return IS
    the row `ask()` writes to the database, so one strip settles both copies."""
    run = O + "cite" + SEP + "turn0search0" + SEP + "turn0search1" + C
    final, _ = _drain(["Es una adaptación local de Sesame Street." + run + " Se emitió durante años."])
    assert final == "Es una adaptación local de Sesame Street. Se emitió durante años."
    assert not [ch for ch in final if 0xE200 <= ord(ch) <= 0xE20F]


def test_a_citation_run_split_across_chunks_is_still_taken_whole():
    """Stripping per chunk would delete the two delimiters and leave `citeturn0search0` behind as
    prose — which is worse than the boxes, because it reads as something she wrote."""
    final, _ = _drain(["Lo confirmé." + O + "cite" + SEP + "turn0",
                       "search0" + C + " Sigue en pie."])
    assert final == "Lo confirmé. Sigue en pie."


def test_an_opening_marker_that_never_closes_gives_her_words_back():
    """A hold is not a licence to swallow the reply: past the cap the run is released with its PUA
    characters removed, because a renderer may lose nothing."""
    final, _ = _drain([O + "cite" + SEP + "x" * 400 + " y esto lo dijo ella."])
    assert final.endswith("y esto lo dijo ella.")
    assert not [ch for ch in final if 0xE200 <= ord(ch) <= 0xE20F]


def test_her_opening_face_tag_survives_a_citation_run_in_the_same_first_chunk():
    """`_HeadTags` gives its watch up on the first printable character, so the citation filter has to
    sit upstream of it or a marker at the head of a reply takes her face with it."""
    final, worn = _drain([O + "cite" + SEP + "turn0search0" + C + "[warmly] Hola, Jordan."])
    assert final == "Hola, Jordan." and worn == ["affectionate"]
