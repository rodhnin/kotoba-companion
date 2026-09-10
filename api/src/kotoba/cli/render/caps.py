"""What this terminal can actually do, measured once at launch.

Every answer is asked, not assumed: OSC 11 for the background, DA1 for sixel, TIOCGWINSZ then
XTWINOPS 16 for a cell's pixel size, and nl_langinfo for the locale — `sys.stdout.encoding` reports
utf-8 under `LC_ALL=C` because Python's UTF-8 mode is on, and a C locale would still emit 言.

Every probe runs raw with TCSANOW, never `tty.setraw`'s TCSAFLUSH default, which DISCARDS what the user
already typed; what arrives during one comes back as `leftover`. The terminal layer is imported softly
because a bare `import termios` made first run on Windows fall back to plain text with rich installed.
"""
from __future__ import annotations

import os
import re
import select
import sys
from dataclasses import dataclass, field

try:
    import termios
    import tty
except ModuleNotFoundError:     # Windows: no raw mode to ask through, and nothing that needs one
    termios = tty = None

from rich.console import Console

from kotoba.cli.render.theme import GLYPHS_ASCII, GLYPHS_UNICODE, fold

CPR = re.compile(r"\x1b\[(\d+);(\d+)R")
CELL = re.compile(r"\x1b\[6;(\d+);(\d+)t")
# Every shape a terminal answers in, once its ESC has been stripped: OSC 11, DA1, CPR, XTWINOPS.
REPORT = re.compile(r"\]11;rgb:[0-9a-fA-F/]*|\[\?[0-9;]*c|\[[0-9]+;[0-9]+R|\[[0-9;]+t")


@dataclass
class Caps:
    color: str = "none"          # truecolor | 256 | 16 | none
    background: str = "mid"      # dark | light | mid
    unicode: bool = False
    encodes_unicode: bool = False    # see `t` — `unicode` is about WIDTHS, this one about bytes
    interactive: bool = False
    reduced_motion: bool = False
    sixel: bool = False
    fps: int = 12                # 4 over SSH: every frame is a round trip there
    width: int = 80
    height: int = 24
    cell: tuple[int, int] = (10, 21)
    g: dict[str, str] = field(default_factory=lambda: dict(GLYPHS_ASCII))
    leftover: str = ""

    def sync_size(self) -> None:
        """A terminal that has not been told its size yet answers ZERO rather than failing — `docker
        run -t` before the client sends one, a fresh pane, some CI. Copied over the default it divides
        by zero two screens later, and `kotoba setup` dies on a traceback at first run."""
        try:
            size = os.get_terminal_size(sys.__stdout__.fileno())
        except (OSError, ValueError, AttributeError):
            return
        if size.columns > 0:
            self.width = size.columns
        if size.lines > 0:
            self.height = size.lines

    def t(self, s: str) -> str:
        """Our copy in ASCII, and only the glyphs of it. An --ascii flag that still emits U+2014 is
        simply broken; one that turns her `aquí` into `aqu?` is worse (`theme.fold`). This runs over her
        prose and her file names too, so the fold has to be the table and nothing else.

        `unicode` is False for two different reasons and `encodes_unicode` is what tells them apart:
        seven bits were ASKED for (`--ascii`, or a stream that cannot carry them), or the terminal is
        UTF-8 and only its ambiguous WIDTHS are wrong. The second one keeps `ascii_fold.WIDE`."""
        return s if self.unicode else fold(s, keep_wide=self.encodes_unicode)


def detect(*, plain: bool = False, ascii_only: bool = False, calm: bool = False) -> Caps:
    """Ask the terminal everything, in one raw-mode window, and keep what the user typed through it.

    The colour question is asked of STDOUT, which is where her colour goes. Asked of stderr — the file
    rich builds a Console on when `stderr=True` — `kotoba 2>notes.log` came back `none`: no palette, and
    no portrait either, because the tier ladder needs a colour terminal to start on.

    The sigil survives an ambiguous-width terminal on its own, and `encodes_unicode` is what carries
    that as far as the wire: put back into `g` alone it was folded straight to `K` again by the trim
    every printed row passes through. 言 is unambiguously Wide, which is the whole reason it may stay
    where ω may not, and `--ascii` is the other question and still takes it."""
    for stale in ("COLUMNS", "LINES"):
        os.environ.pop(stale, None)
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    probe = Console()
    color = {"truecolor": "truecolor", "256": "256", "eight_bit": "256",
             "standard": "16", "windows": "16"}.get(probe.color_system or "", "none")
    if plain or os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        color = "none"
    unicode_ok = _unicode_capable() and not ascii_only
    drawable = unicode_ok

    leftover = ""
    background = "mid"
    cell = (10, 21)
    if interactive:
        background, leftover = _background()
    if interactive and unicode_ok:
        wide, typed = _ambiguous_wide()
        leftover += typed
        drawable = not wide
    sixel = False
    if interactive:
        sixel, typed = _graphics()
        leftover += typed
    if interactive and color != "none" and (sixel or color == "truecolor"):
        cell, typed = _cell()
        leftover += typed

    caps = Caps(
        color=color, background=background, unicode=drawable, encodes_unicode=unicode_ok,
        interactive=interactive,
        reduced_motion=calm or bool(os.environ.get("KOTOBA_REDUCED_MOTION")),
        fps=4 if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY") else 12,
        sixel=sixel, cell=cell,
        g=dict(GLYPHS_UNICODE if drawable else GLYPHS_ASCII), leftover=leftover,
    )
    if unicode_ok:
        caps.g["sigil"] = "言"
    caps.sync_size()
    return caps


def cursor_pos(timeout: float = 0.12) -> tuple[int, int, str]:
    """(0-based row, 0-based column, whatever was typed while we asked). -1,-1 when nothing answered."""
    resp, typed = _query("\x1b[6n", CPR, timeout)
    m = CPR.search(resp)
    if not m:
        return -1, -1, typed
    return int(m.group(1)) - 1, int(m.group(2)) - 1, typed


def _query(question: str, terminator: re.Pattern, timeout: float) -> tuple[str, str]:
    if termios is None or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return "", ""
    try:
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
    except (termios.error, ValueError, OSError):
        return "", ""
    buf = ""
    try:
        tty.setraw(fd, termios.TCSANOW)
        sys.stdout.write(question)
        sys.stdout.flush()
        while len(buf) < 96:
            if not select.select([fd], [], [], timeout)[0]:
                break
            buf += os.read(fd, 32).decode("utf-8", "replace")
            if terminator.search(buf):
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    m = terminator.search(buf)
    if not m:
        return "", _typing(buf)
    return m.group(0), _typing(buf[:m.start()] + buf[m.end():])


def _background(timeout: float = 0.12) -> tuple[str, str]:
    """Her palette flips on a light terminal, and OSC 11 is how that is asked. The override is here for
    the one caller that cannot answer it: a pty under a screenshot harness, where the alternative is
    that the light ramp can never be looked at."""
    forced = os.environ.get("KOTOBA_FORCE_BACKGROUND", "").strip().lower()
    if forced in ("dark", "light", "mid"):
        return forced, ""
    resp, typed = _query("\x1b]11;?\x07",
                         re.compile(r"\x1b\]11;[^\x07\x1b]*(\x07|\x1b\\)"), timeout)
    m = re.search(r"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", resp)
    if not m:
        return "mid", typed
    r, g, b = (int(x[:2], 16) / 255 for x in m.groups())
    return ("light" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5 else "dark"), typed


def _ambiguous_wide(timeout: float = 0.15) -> tuple[bool, str]:
    """Her kaomoji is built out of ambiguous-width characters. A terminal that gives them two cells
    each draws a face twice as wide as anything measuring it, so on that one she goes ASCII."""
    probe = "ω･●▌─"
    resp, typed = _query("\r" + probe + "\x1b[6n", CPR, timeout)
    sys.stdout.write("\r\x1b[2K")
    sys.stdout.flush()
    m = CPR.search(resp)
    if not m:
        return False, typed
    return int(m.group(2)) - 1 > len(probe), typed


def _graphics(timeout: float = 0.12) -> tuple[bool, str]:
    if os.environ.get("KOTOBA_NO_GRAPHICS"):
        return False, ""
    resp, typed = _query("\x1b[c", re.compile(r"\x1b\[\?([0-9;]+)c"), timeout)
    m = re.search(r"\x1b\[\?([0-9;]+)c", resp)
    return (bool(m) and "4" in m.group(1).split(";")), typed


def _cell() -> tuple[tuple[int, int], str]:
    """((px wide, px tall), whatever was typed while we asked). The cell decides how many columns a
    sixel of hers covers, so a wrong one draws her over the text she is standing beside.

    tmux reports no pixel size at all through TIOCGWINSZ — measured, and it is the one place the sixel
    tier is claimed with nothing to size it — but it does answer XTWINOPS 16 with the real cell of the
    terminal it is running in. That question is only asked when the ioctl came back empty AND some
    portrait tier could use the answer, so nothing that will only ever draw a kaomoji pays for it.

    The ioctl is reached on a truecolor terminal with no sixel, which Windows Terminal is — and there
    `import fcntl` raises, so the import sits inside the guard with the failures it belongs to."""
    import struct

    try:
        import fcntl

        rows, cols, xpix, ypix = struct.unpack(
            "HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8))
        if xpix and ypix and rows and cols:
            return (max(1, xpix // cols), max(1, ypix // rows)), ""
    except (ImportError, AttributeError, OSError, ValueError, struct.error):
        pass
    return _cell_query()


def _cell_query(timeout: float = 0.12) -> tuple[tuple[int, int], str]:
    resp, typed = _query("\x1b[16t", CELL, timeout)
    m = CELL.search(resp)
    if not m:
        return (10, 21), typed
    tall, wide = int(m.group(1)), int(m.group(2))
    if not (2 <= wide <= 64 and 2 <= tall <= 128):
        return (10, 21), typed
    return (wide, tall), typed


def _typing(buf: str) -> str:
    """What the user typed through a probe: printable, and with the terminal's own answers taken out.

    A reply that misses its timeout — 130 ms of SSH is enough — is still in the buffer when the next
    question reads, and comes back here as typing. The ESC in front of it is gone by then, so
    `]11;rgb:2121/1a1a/2e2e` was landing in her input box as if somebody had typed it. Filtering at the
    one place every answer is read means a probe added later is covered by having been asked."""
    return REPORT.sub("", "".join(c for c in buf if c.isprintable()))


def _unicode_capable() -> bool:
    import locale

    try:
        codeset = locale.nl_langinfo(locale.CODESET)
    except (AttributeError, ValueError):
        codeset = sys.stdout.encoding or "ascii"
    if codeset:
        return "utf" in codeset.lower().replace("-", "")
    return "utf" in (sys.stdout.encoding or "ascii").lower()
