"""What this operating system can and cannot give her terminal.

One module, because two things read the answer and a disagreement between them is the whole defect:
`__main__` decides whether to open her terminal at all, and `kotoba doctor` explains why it did not.
Two copies of that list would eventually say different things on the same machine.

The question is asked of the IMPORT SYSTEM, not of `sys.platform`. `termios` and `tty` are what the
terminal is built on — raw mode, cbreak, TCSANOW — and their absence is the real condition; a name
comparison would also have to be kept in step with every platform that turns out to lack them.
"""
from __future__ import annotations

import importlib.util
import platform

#: The POSIX terminal layer. Windows ships neither, which is why her interactive UI does not run there.
TERMINAL_MODULES = ("termios", "tty")


def missing_terminal_modules() -> list[str]:
    return [name for name in TERMINAL_MODULES if not _present(name)]


def terminal_ui_available() -> bool:
    """Whether `kotoba` with no arguments can open her terminal on this machine.

    This is not the `cli` extra: `prompt_toolkit`, `rich` and `pillow` install perfectly on Windows,
    so an extras check reports everything present while nothing can draw. Whoever asks this must ask
    the other question too — the two fail for different reasons and want different sentences."""
    return not missing_terminal_modules()


def ansi_available() -> bool:
    """Whether writing an escape code paints a colour instead of printing itself.

    Everywhere but Windows, yes. A Windows console understands the codes but arrives with them turned
    OFF, so the answer is also the act of asking: the flag is set here, once, and a console that
    refuses it gets plain text rather than `←[32mok` printed at somebody on their first run. Windows
    Terminal enables it already; the classic console does not, and `kotoba doctor` is exactly the
    command somebody runs there when nothing else works."""
    global _ANSI
    if _ANSI is None:
        _ANSI = _enable_ansi()
    return _ANSI


_ANSI: bool | None = None


def _enable_ansi() -> bool:
    import os

    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def describe() -> str:
    """This machine, in the words a bug report needs: `Windows 11 (AMD64), Python 3.12.4`."""
    return (f"{platform.system() or 'unknown'} {platform.release()} ({platform.machine()}), "
            f"Python {platform.python_version()}")


def _present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False
