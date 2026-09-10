"""The block splitter is the only thing standing between a stream and an append-only transcript."""
from __future__ import annotations

from kotoba.cli.render.markdown import Blocks


def feed_all(chunks: list[str]) -> list[str]:
    blocks = Blocks()
    out: list[str] = []
    for chunk in chunks:
        out += blocks.feed(chunk)
    return out + blocks.flush()


def test_a_paragraph_is_emitted_as_soon_as_the_blank_line_after_it_lands():
    blocks = Blocks()
    assert blocks.feed("Hello there.\n") == []
    assert blocks.feed("\nAnd more.") == ["Hello there."]
    assert blocks.flush() == ["And more."]


def test_a_line_split_across_chunks_is_never_split_into_two_blocks():
    assert feed_all(["Hel", "lo th", "ere.\n\nNext."]) == ["Hello there.", "Next."]


def test_a_fence_containing_blank_lines_stays_one_block_with_its_blanks_intact():
    stream = "```python\ndef f():\n\n    return 1\n\n```\n\nafter\n"
    assert feed_all([stream]) == ["```python\ndef f():\n\n    return 1\n\n```", "after"]


def test_a_fence_is_its_own_block_even_when_prose_runs_straight_into_it():
    assert feed_all(["here:\n```sh\nls\n```\n"]) == ["here:", "```sh\nls\n```"]


def test_a_nested_list_is_one_block_however_deep_it_goes():
    stream = "- one\n  - nested\n    - deeper\n- two\n\nafter\n"
    assert feed_all([stream]) == ["- one\n  - nested\n    - deeper\n- two", "after"]


def test_a_blank_line_between_list_items_does_not_end_the_list():
    assert feed_all(["- one\n\n- two\n\n\nnot a list\n"]) == ["- one\n\n- two", "not a list"]


def test_an_indented_continuation_after_a_blank_line_stays_with_its_item():
    assert feed_all(["1. step\n\n   still the step\n\nprose\n"]) == \
        ["1. step\n\n   still the step", "prose"]


def test_a_fence_left_unclosed_at_the_end_of_the_stream_is_closed_and_emitted():
    assert feed_all(["```python\nx = 1\n"]) == ["```python\nx = 1\n```"]


def test_an_unclosed_fence_swallows_the_blank_lines_that_would_otherwise_split_it():
    assert feed_all(["```\none\n\ntwo\n\nthree"]) == ["```\none\n\ntwo\n\nthree\n```"]


def test_a_fence_indented_under_a_list_item_belongs_to_the_item_and_not_to_a_new_block():
    stream = "- run it:\n  ```sh\n  ls\n  ```\n"
    assert feed_all([stream]) == ["- run it:\n  ```sh\n  ls\n  ```"]


def test_a_tilde_fence_is_closed_only_by_tildes():
    assert feed_all(["~~~\n```\nstill code\n~~~\n"]) == ["~~~\n```\nstill code\n~~~"]


def test_leading_blank_lines_never_become_an_empty_block():
    assert feed_all(["\n\n\nHello.\n"]) == ["Hello."]


def test_partial_reports_exactly_what_is_still_in_flight():
    blocks = Blocks()
    blocks.feed("Hello there.\n\nAnd a se")
    assert blocks.partial == "And a se"
    blocks.feed("cond one.")
    assert blocks.partial == "And a second one."


def test_flushing_twice_does_not_repeat_the_last_block():
    blocks = Blocks()
    blocks.feed("only this")
    assert blocks.flush() == ["only this"]
    assert blocks.flush() == []
