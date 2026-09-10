"""Reading a command line the way the shell that will run it reads one.

Three places asked this and each answered with its own POSIX `shlex.split`, which eats a Windows
path's separators: `cat C:\\Users\\me\\.ssh\\id_rsa` arrives as the single token `C:Usersme.sshid_rsa`.
The gate read that as a file inside the workspace and auto-approved it; the destructive card resolved
nothing and drew an empty blast radius. The platform decides the mode, and it is the sandbox's
platform, not this process's (`sandbox.local.shell_is_windows`).
"""
from __future__ import annotations

import shlex

_QUOTES = ('"', "'")


def split(command: str) -> list[str]:
    """The command's tokens, or `[]` when it cannot be read.

    POSIX is `shlex.split` unchanged. Windows uses the non-POSIX mode, which leaves a backslash alone
    because there it separates a path rather than escaping anything — and keeps the quotes inside the
    token, so they come off here. Empty is the safe answer: the gate reads it as grounds to ask.

    Two PowerShell spellings shlex will never model — the backtick escape, and a quote doubled inside a
    quoted run (`'it''s.txt'`), which it splits into three tokens. Both are refused rather than guessed
    at, because a card naming files a command never touches is worse than one naming none.
    """
    from kotoba.core.sandbox.local import shell_is_windows

    windows = shell_is_windows()
    if windows and (_doubled_quote(command) or "`" in command):
        # PowerShell's escape, which shlex has never heard of: it would hand back a token carrying the
        # backtick and split the escaped character off as another. Refusing is the answer this already
        # gives for anything it cannot read — a card drawing the wrong blast radius is worse than one
        # drawing none, and nothing on this platform auto-approves on these tokens anyway.
        return []
    try:
        parts = shlex.split(command, posix=not windows)
    except ValueError:
        return []
    return [_unquote(p) for p in parts] if windows else parts


def _doubled_quote(command: str) -> bool:
    """Whether a quote is doubled INSIDE a quoted run, which is how PowerShell escapes one.

    Not the same as an empty `''` argument, and the difference is one character of lookahead: inside a
    run, a quote followed by its twin escapes, and a quote followed by anything else closes."""
    i, n, run = 0, len(command), ""
    while i < n:
        c = command[i]
        if not run:
            if c in _QUOTES:
                run = c
        elif c == run:
            if i + 1 < n and command[i + 1] == run:
                return True
            run = ""
        i += 1
    return False


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in _QUOTES:
        return token[1:-1]
    return token
