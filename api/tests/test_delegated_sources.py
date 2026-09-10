"""A delegated research report must cite the URLs its helper fetched.

delegate() runs the helper on a fresh ctx.child() and returns only its text, so the helper's sources are
lost unless they are merged back — the parent then writes the report with nothing to cite.
"""
from __future__ import annotations

import pytest

from kotoba.core import citations


class Ctx:
    """Minimal stand-in for ToolContext's source-carrying surface."""

    def __init__(self):
        self._sources: dict[str, str] = {}

    def child(self, _sub_id):
        return Ctx()


def test_helper_sources_reach_the_parent():
    parent, child = Ctx(), Ctx()
    citations.collect_from_annotation(child, {"type": "url_citation",
                                              "url": "https://sw.kovidgoyal.net/kitty/graphics-protocol/",
                                              "title": "kitty graphics protocol"})
    assert parent._sources == {}, "precondition: the parent never searched"

    added = citations.merge_from_child(parent, child)

    assert added == 1
    assert parent._sources["https://sw.kovidgoyal.net/kitty/graphics-protocol/"] == "kitty graphics protocol"


def test_merge_keeps_the_better_title_and_does_not_double_count():
    parent, child = Ctx(), Ctx()
    parent._sources["https://example.test/a"] = ""            # captured earlier with no title
    citations.collect_from_annotation(child, {"type": "url_citation", "url": "https://example.test/a",
                                              "title": "A real title"})
    citations.collect_from_annotation(child, {"type": "url_citation", "url": "https://example.test/b",
                                              "title": "B"})

    added = citations.merge_from_child(parent, child)

    assert added == 1                                          # only /b is new
    assert parent._sources["https://example.test/a"] == "A real title"
    assert parent._sources["https://example.test/b"] == "B"


@pytest.mark.parametrize("child", [None, Ctx()])
def test_merge_is_a_no_op_when_there_is_nothing_to_lift(child):
    parent = Ctx()
    assert citations.merge_from_child(parent, child) == 0
    assert parent._sources == {}


def test_merge_never_lifts_into_itself():
    ctx = Ctx()
    citations.collect_from_annotation(ctx, {"type": "url_citation", "url": "https://example.test/x",
                                            "title": "X"})
    assert citations.merge_from_child(ctx, ctx) == 0
    assert list(ctx._sources) == ["https://example.test/x"]


def test_merged_sources_are_offered_to_the_model_on_the_next_iteration():
    """The point of merging: pending_note must surface the helper's URLs so the parent can cite them."""
    parent, child = Ctx(), Ctx()
    citations.collect_from_annotation(child, {"type": "url_citation", "url": "https://example.test/doc",
                                              "title": "The doc"})
    citations.merge_from_child(parent, child)

    note = citations.pending_note(parent)

    assert note and "https://example.test/doc" in note
    assert citations.pending_note(parent) is None, "already shown; must not repeat"
