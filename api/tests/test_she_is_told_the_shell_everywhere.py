"""The shell she is taught is the shell that runs — in every prompt, not just the companion one.

Work mode and delegate build their own developer prompts, so the platform block never reached them,
and they are where `shell` is used most. The tool description was worse: it stated `sh -c`
unconditionally, on every turn, next to the function definition, contradicting the block outright.
"""
from __future__ import annotations

import inspect
from unittest.mock import patch

from kotoba.core import work_runner
from kotoba.soul.prompt import platform_note
from kotoba.tools.action import delegate, shell


def _windows():
    return patch("kotoba.core.sandbox.local.shell_is_windows", return_value=True)


def test_the_tool_description_never_names_one_shell_as_the_shell():
    """It is read next to the function definition, so it outweighs anything the prompt says."""
    said = shell.SCHEMA["parameters"]["properties"]["command"]["description"]
    assert "ALREADY run through `sh -c`" not in said
    assert "sh -c" in said and "PowerShell" in said, "both, or it is wrong on one platform"


def test_no_posix_only_idiom_is_offered_as_the_way_to_do_something():
    """`app file >/dev/null 2>&1 &` is a syntax error in PowerShell 5.1."""
    whole = str(shell.SCHEMA)
    assert ">/dev/null 2>&1 &" not in whole


def test_the_note_is_silent_on_posix_and_speaks_on_windows():
    assert platform_note() == ""
    with _windows():
        note = platform_note()
    assert "PowerShell" in note and "backslashes" in note


def test_work_mode_and_delegate_actually_CARRY_the_note():
    """The half that matters: a helper nobody calls proves only that it compiles. Both build their
    own prompt, so neither inherits the companion one."""
    for module in (work_runner, delegate):
        src = inspect.getsource(module)
        assert "platform_note()" in src, (
            f"{module.__name__} builds its own developer prompt and never adds the platform block, "
            "so on Windows it teaches POSIX in the mode that runs the most commands")
