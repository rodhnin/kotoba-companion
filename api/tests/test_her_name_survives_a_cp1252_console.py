"""cp1252 cannot hold her own name, and Windows hands the CLI exactly that console by default.

The reply had already been paid for when `print` raised, so the cost was real and the answer was lost.
Redirection to a file picks the same encoding, so this is not only about a visible terminal.
"""
from __future__ import annotations

import os
import subprocess
import sys

KANA = "言葉"


def _in_a_cp1252_console(body: str) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    return subprocess.run(
        [sys.executable, "-c", body], env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=60,
    )


def test_the_cli_prints_her_name_through_a_cp1252_console():
    done = _in_a_cp1252_console(
        f"from kotoba.cli.__main__ import _speak_utf8; _speak_utf8(); print({KANA!r})")
    assert done.returncode == 0, done.stderr
    assert KANA in done.stdout


def test_the_same_console_kills_a_print_left_alone():
    """The instrument, shown failing: without the fix this is what every kana reply did."""
    done = _in_a_cp1252_console(f"print({KANA!r})")
    assert done.returncode != 0, "cp1252 accepted kana; this test no longer proves anything"
    assert "UnicodeEncodeError" in done.stderr
