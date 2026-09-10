"""Her colours, her glyphs, and the one Console every module prints through.

Colour is information here, never decoration: the coral sigil, the nameplate tinted by her mood and the
kaomoji carry the only hues, and prose keeps the terminal's own foreground. The ramps drop
truecolor -> 256 -> 16 -> none, and on a tty `none` is `no_color=True`, never `color_system=None`,
which would take bold and reverse with it and flatten every chip — the case that must still read.

Every glyph below is cmap-verified in JetBrains Mono NF, CaskaydiaCove NF and Noto CJK. `--ascii` and
"this stream cannot encode that" are two questions: `fold` answers the first, `_degrade` the second.
"""
from __future__ import annotations

import sys

from rich.console import Console
from rich.theme import Theme

from kotoba.cli.render.ascii_fold import fold  # noqa: F401  (re-export)

RAMPS = {
    "truecolor": {
        "dark": {"coral": "#ff5a3c", "grape": "#8f74ff", "mint": "#14c79a",
                 "sun": "#ffb22e", "live": "#ff3b5c"},
        "light": {"coral": "#c73a1e", "grape": "#5a34cf", "mint": "#00805a",
                  "sun": "#9a5c00", "live": "#c8093a"},
        "mid": {"coral": "#e13c21", "grape": "#7d60f6", "mint": "#008b63",
                "sun": "#b16800", "live": "#eb214e"},
    },
    "256": {
        "dark": {"coral": "color(202)", "grape": "color(105)", "mint": "color(36)",
                 "sun": "color(214)", "live": "color(203)"},
        "light": {"coral": "color(124)", "grape": "color(56)", "mint": "color(29)",
                  "sun": "color(94)", "live": "color(161)"},
        "mid": {"coral": "color(160)", "grape": "color(99)", "mint": "color(29)",
                "sun": "color(130)", "live": "color(197)"},
    },
    "16": {
        "dark": {"coral": "bright_red", "grape": "bright_blue", "mint": "cyan",
                 "sun": "bright_yellow", "live": "bright_magenta"},
        "light": {"coral": "red", "grape": "blue", "mint": "green",
                  "sun": "yellow", "live": "magenta"},
    },
}
RAMPS["16"]["mid"] = RAMPS["16"]["dark"]

CHIPS = {
    "truecolor": {"chip.coral": "bold #ffffff on #ff5a3c", "chip.grape": "bold #ffffff on #6c4ce0",
                  "chip.mint": "bold #211a2e on #14c79a", "chip.sun": "bold #211a2e on #ffb22e",
                  "chip.live": "bold #ffffff on #ff3b5c", "chip.ink": "bold #fbf1e3 on #211a2e"},
    "256": {"chip.coral": "bold color(231) on color(202)", "chip.grape": "bold color(231) on color(99)",
            "chip.mint": "bold color(235) on color(36)", "chip.sun": "bold color(235) on color(214)",
            "chip.live": "bold color(231) on color(197)", "chip.ink": "bold color(230) on color(235)"},
    "16": {"chip.coral": "bold white on magenta", "chip.grape": "bold white on blue",
           "chip.mint": "bold black on green", "chip.sun": "bold black on yellow",
           "chip.live": "bold white on red", "chip.ink": "bold reverse"},
}
INK_ON_DARK = {"truecolor": "bold #211a2e on #cfc5b8",
               "256": "bold color(235) on color(250)", "16": "bold reverse"}
# The WORKING chip in prompt_toolkit's own vocabulary — `chip.grape` above, said the other way. It is
# an inline style and not a `class:` because the style map lives with the prompt, and one
# fragment of grape is not worth a second palette to keep in step.
PT_WORK_CHIP = {"truecolor": "bg:#6c4ce0 #ffffff bold", "256": "bg:#875fff #ffffff bold",
                "16": "bg:ansiblue ansiwhite bold", "none": "reverse"}
SHADOW_HEX = {"coral": "#ff5a3c", "grape": "#6c4ce0", "mint": "#14c79a",
              "sun": "#ffb22e", "live": "#ff3b5c", "ink": "#8d8598"}

GLYPHS_UNICODE = {
    "sigil": "言", "prompt": "›", "ok": "✓", "fail": "×", "cut": "◇", "ask": "?",
    "rule": "─", "bullet": "·", "arrow": "→", "cont": "│", "dot": "●", "ring": "○",
    "caret": "▌", "shadow": "▌", "pip": "▪", "give": "▸", "edge_l": "▏", "edge_r": "▕",
    "rail": "█", "enter": "⏎", "under": "▀", "take": "◂", "tab": "▄",
    "box": "□", "elbow": "└",
    "spin": "▁▂▃▄▅▆▇▆▅▄▃▂", "think": "◦▪●",
}
GLYPHS_ASCII = {
    "sigil": "K", "prompt": ">", "ok": "+", "fail": "x", "cut": "~", "ask": "?",
    "rule": "-", "bullet": ".", "arrow": "->", "cont": "|", "dot": "*", "ring": "o",
    "caret": "_", "shadow": " ", "pip": "*", "give": ">", "edge_l": "[", "edge_r": "]",
    "rail": "|", "enter": "<-", "under": "-", "take": "<", "tab": "_",
    "box": "[", "elbow": "+",
    "spin": ".oOo", "think": ",:*",
}


#: Not-given, distinct from the `None` rich reads as "paint nothing" — a caller handing on another
#: console's answer must be able to hand on that one too.
_ASK = object()


def _degrade(file) -> None:
    """A stream that cannot encode her prose must substitute a character, never raise one.

    `LC_ALL=C` with Python's UTF-8 mode off is an ascii stdout with `errors="surrogateescape"`, and the
    first `é` she writes ends the session on a UnicodeEncodeError — measured. `replace` is what puts the
    familiar `?` there, and it is decided by what the STREAM can carry, so `--ascii` on a UTF-8 terminal
    is untouched by it. Asked once, at the Console the whole session prints through."""
    stream = sys.stdout if file is None else file
    codeset = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
    if not codeset or "utf" in codeset:
        return
    if getattr(stream, "errors", "") in ("replace", "backslashreplace", "xmlcharrefreplace"):
        return
    try:
        stream.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def build_console(caps, file=None, force_terminal: bool | None = None,
                  color_system: str | None = _ASK) -> Console:
    """One Console for the whole session, and `file` is left None on purpose: rich re-reads `sys.stdout`
    on every print, which is the only way a print from another task reaches prompt_toolkit's proxy
    instead of landing on top of a live prompt. Pinning `file=sys.stdout` here would look identical.

    `highlight=False` is not a preference either: rich's auto-highlighter paints numbers, paths and
    quoted strings inside HER SENTENCES, which is colour on prose. `markup=False` is load-bearing too —
    rich reads `[y/N/a]` as a style tag and deletes it, so the approval prompt asked for a `rm -rf`
    with its keys invisible. `emoji=False` closes the twin hatch: rich expands `:tada:`-style shortcodes
    on plain strings, and while every print here passes a renderable today, that is one f-string away."""
    _degrade(file)
    styles = _plain_styles(caps) if caps.color == "none" else _colour_styles(caps)
    return Console(
        theme=Theme(styles, inherit=True),
        # `auto` asks the TERMINAL, which is right in the product and wrong for a test of the theme:
        # a Windows console answers 16 colours, so a truecolor style rendered as `101m` and a pinned
        # assertion failed against a palette nobody chose.
        color_system=(("auto" if caps.interactive else None) if color_system is _ASK
                      else color_system),
        no_color=caps.color == "none",
        highlight=False, markup=False, emoji=False, soft_wrap=False, file=file,
        force_terminal=force_terminal,
    )


def _colour_styles(caps) -> dict[str, str]:
    ramp = RAMPS[caps.color][caps.background]
    styles = {
        "coral": ramp["coral"], "name": f"bold {ramp['coral']}", "chrome": "dim", "hard": "bold",
        "mint": ramp["mint"], "live": ramp["live"], "sun": ramp["sun"], "grape": ramp["grape"],
        "face.coral": ramp["coral"], "face.grape": ramp["grape"], "face.sun": ramp["sun"],
        "face.live": ramp["live"], "face.muted": "dim", "face.edge": "",
    }
    styles.update({"d." + slot: f"dim {hue}" for slot, hue in ramp.items()})
    styles.update(CHIPS[caps.color])
    if caps.background != "light":
        styles["chip.ink"] = INK_ON_DARK[caps.color]
    styles.update(_shadows(caps))
    styles.update(_prose_styles(ramp["coral"]))
    return styles


def _plain_styles(caps) -> dict[str, str]:
    styles = {k: "" for k in ("coral", "mint", "live", "sun", "grape", "face.coral", "face.grape",
                              "face.sun", "face.live", "face.edge")}
    styles.update({"name": "bold", "chrome": "dim", "hard": "bold", "face.muted": "dim"})
    styles.update({k: ("bold reverse" if caps.interactive else "") for k in CHIPS["truecolor"]})
    styles.update({"shadow." + slot: "dim" for slot in SHADOW_HEX})
    styles.update({"d." + slot: "dim" for slot in SHADOW_HEX})
    styles.update(_prose_styles(""))
    return styles


def _prose_styles(accent: str) -> dict[str, str]:
    """Her sentences keep the terminal's own foreground. Rich tints headings, quotes and list bullets by
    default, and a paragraph with three hues in it stops reading as one voice."""
    flat = {f"markdown.h{n}": "bold" for n in range(1, 8)}
    flat.update({
        "markdown.paragraph": "", "markdown.text": "", "markdown.emph": "italic",
        "markdown.strong": "bold", "markdown.code": "bold", "markdown.block_quote": "dim",
        "markdown.item": "", "markdown.item.bullet": accent or "", "markdown.item.number": "dim",
        "markdown.hr": "dim", "markdown.link": "italic", "markdown.link_url": "dim",
        "markdown.h1.border": "dim",
    })
    return flat


def _shadows(caps) -> dict[str, str]:
    """The one cell under a chip that makes it read as a sticker: its own hue, dropped towards the
    background rather than greyed, or the sticker looks like it is peeling off."""
    out = {}
    k = 0.52 if caps.background != "light" else 0.62
    for slot, hexv in SHADOW_HEX.items():
        if caps.color == "truecolor":
            rgb = tuple(int(hexv[i:i + 2], 16) for i in (1, 3, 5))
            mixed = tuple(int(0x21 + (c - 0x21) * k) for c in rgb)
            out["shadow." + slot] = "#%02x%02x%02x" % mixed
        elif caps.color == "256":
            out["shadow." + slot] = "color(238)" if caps.background != "light" else "color(240)"
        else:
            out["shadow." + slot] = "dim"
    return out
