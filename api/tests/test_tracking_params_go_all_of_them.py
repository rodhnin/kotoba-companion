"""Two tracking parameters side by side, and only the first went.

The pattern consumes the `&` that ends the parameter it removes, so the scanner resumes past the
separator the NEXT one needed to match. A link she posts is clickable, so what rides in its query
string is what the reader's browser sends on.
"""
from __future__ import annotations

import pytest

from kotoba.discord import text


@pytest.mark.parametrize("dirty,clean", [
    ("https://x.test/?utm_source=q&ref=z&k=1", "https://x.test/?k=1"),
    ("https://x.test/?k=1&utm_source=q&utm_medium=m", "https://x.test/?k=1"),
    ("https://x.test/?utm_source=q&utm_medium=m&utm_campaign=c", "https://x.test/"),
    ("https://x.test/?k=1&utm_source=q", "https://x.test/?k=1"),
    ("https://x.test/?utm_source=q", "https://x.test/"),
])
def test_every_tracking_parameter_goes(dirty, clean):
    got = text.tidy_links(dirty)
    assert got == clean, f"{dirty} -> {got}"


def test_an_ordinary_query_is_left_alone():
    url = "https://x.test/?q=hello&page=2"
    assert text.tidy_links(url) == url
