"""CodeFenceFilter + tag stripping — she SPEAKS, so code blocks and (on non-v3) audio tags must never
reach TTS. Regression for the prod bug where she dictated a ```python ...``` block aloud."""
from __future__ import annotations

import importlib
import os

import pytest

import kotoba.core.stream as stream


@pytest.fixture(autouse=True)
def _restore_stream():
    """This module writes the env and reloads core.stream. Without putting both back, every later test in
    the process saw a rebound module object and a KOTOBA_EXPRESSIVE nobody set."""
    before = os.environ.get("KOTOBA_EXPRESSIVE")
    yield
    if before is None:
        os.environ.pop("KOTOBA_EXPRESSIVE", None)
    else:
        os.environ["KOTOBA_EXPRESSIVE"] = before
    importlib.reload(stream)


def _pipeline(text: str, expressive: bool, char_stream: bool = True):
    os.environ["KOTOBA_EXPRESSIVE"] = "true" if expressive else "false"
    importlib.reload(stream)
    cf, tf, pf = stream.CodeFenceFilter(), stream.AudioTagFilter(), stream.ForbiddenPhraseFilter()
    units = list(text) if char_stream else [text]
    out = []
    for u in units:
        out.append(pf.feed(tf.feed(cf.feed(u))))
    out.append(pf.feed(tf.feed(cf.flush())))
    out.append(pf.feed(tf.flush()))
    out.append(pf.flush())
    return "".join(out)


CODE_REPLY = "Here is your scraper! ```python\nimport requests\nprint(1)\n``` Done, want me to run it?"


def test_code_block_stripped_text_after_kept():
    out = _pipeline(CODE_REPLY, expressive=False)
    assert "import requests" not in out and "```" not in out
    assert "Here is your scraper!" in out
    assert "Done, want me to run it?" in out  # text AFTER the fence must survive


def test_code_block_stripped_in_big_chunks():
    out = _pipeline(CODE_REPLY, expressive=False, char_stream=False)
    assert "import requests" not in out and "Done, want me to run it?" in out


def test_unclosed_fence_suppresses_code():
    out = _pipeline("text before ```python\nstuff that never closes", expressive=False)
    assert "stuff that never closes" not in out and "text before" in out


def test_inline_backticks_removed():
    assert "`" not in _pipeline("the `print` function", expressive=False)


def test_tags_stripped_when_not_v3_but_kept_when_v3():
    off = _pipeline("[happily] Hi there!", expressive=False)
    on = _pipeline("[happily] Hi there!", expressive=True)
    assert "[happily]" not in off and "Hi there!" in off   # non-v3 → no tag spoken
    assert "[happily]" in on                               # v3 → tag kept for performance
