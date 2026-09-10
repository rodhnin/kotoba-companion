"""The one screen where a person takes responsibility for something irreversible.

A rail with a tab, never a frame: three sides shatter at 60 columns. Everything wraps and nothing is
ellipsised — this surface must read at every width. The card may never show LESS than what will run
without saying so: what the row budget holds back is counted and named, and `?` raises it to the window.
No countdown is drawn though the 180 s are real: a clock ticking over an `rm -rf` is pressure the motion
budget also will not pay for. `Confirm` asks the CONSEQUENCE, never "are you sure", and has no `--force`
twin: a flag typed reflexively is a gate already passed. Every row is `Safe`, never `rich.Text` — a
security boundary: a control byte glued to `rm -rf ~` repaints a harmless command over the dangerous one.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from rich.cells import cell_len
from rich.text import Text

from kotoba.cli.render import paths
from kotoba.cli.render.safe import Safe
from kotoba.cli.render.text import wrap
from kotoba.core import command_line
from kotoba.core.text_security import scrub

PRESS_MS = int(os.environ.get("KOTOBA_PRESS_MS") or 120)
INNER_MAX = 62
AIRY = 56
RAIL = 5
CMD_ROWS = 5
CARD_CHROME = 14
WALK_ENTRIES = 20_000
WALK_SECONDS = 0.35
BLAST_PATHS = 4
NAME_COL = 20  # the listing rows' first column — the measure every caller sizes its second one against
COL_MIN = 6    # the least of any one column that still says something
EDGE = 2       # the right margin a listing row is fitted inside, not merely held under


@dataclass
class Approval:
    """`a` and `t` are two grants of different width and never one key whose reach the reader has to
    infer: `a` saves the FAMILY (every `npm` from now on), `t` saves THIS LINE and nothing adjacent to
    it. Either can be off on its own, and `t` is the only one on offer over a compound or an interpreter
    command — where an empty rail with no explanation read, to the person it was built for, as the
    product being broken. `always_note` is the backend's one sentence for why `a` is missing."""

    cmd: str
    danger: str
    family: str
    intent: str = ""
    blast: tuple = ()
    can_always: bool = True
    can_always_exact: bool = False
    always_note: str = ""
    state: str = "open"
    answer: str = ""
    show_why: bool = False
    flash_until: float = 0.0
    held: bool = False
    sandbox: str = "local"

    @property
    def title(self) -> str:
        """The fixed line, unless a caller hand-curated an intent — never her streamed prose, which the
        person is already reading two rows above the card."""
        return self.intent or "I'd like to run something on your machine"

    @property
    def why(self) -> str:
        if self.sandbox == "docker":
            if self.danger:
                return (f"it's a {self.danger.replace('-', ' ')} — a container doesn't make that "
                        "any less yours to say yes to")
            return "it's code that could change things, and I ask before that even in a container"
        if self.danger:
            return (f"it's a {self.danger.replace('-', ' ')}, and the sandbox is "
                    "local — that combination is yours to say yes to")
        return "the sandbox is local, so anything that isn't a plain read needs you"


@dataclass
class Confirm:
    head: str
    why: str
    yes: str = "change it"
    no: str = "leave it as it is"
    did: str = "changed"
    didnt: str = "left as it was"
    state: str = "open"
    answer: str = ""
    flash_until: float = 0.0


def approval_rows(caps, card: Approval, width: int) -> list[Text]:
    """The card, whole, at any width. `width` is the column budget it may occupy: the rail, its two
    spaces and the shadow come off the top, and the body wraps into the `width - RAIL` that is left."""
    inner = _inner(width)
    if card.state == "pressed":
        return [_pressed(caps, card.answer)]

    rows = [Safe(caps.g["tab"] * 12, style="sun")]
    rows += _headline(caps, card.title, inner)

    airy = inner >= AIRY
    if airy:
        rows.append(_rail(caps, Safe("")))
    rows += _command_rows(caps, card, inner)

    if card.show_why:
        if airy:
            rows.append(_rail(caps, Safe("")))
        rows += _why_rows(caps, card, inner)
    if card.held:
        if airy:
            rows.append(_rail(caps, Safe("")))
        for chunk in _balanced(caps,
                "I asked while you were away, so I've been holding it — I'll give it three minutes "
                "and then leave it alone", inner):
            rows.append(_rail(caps, Safe(chunk, style="chrome")))

    rows += _key_rows(caps, card, inner, width)
    return rows + [_shadow(caps, rows, width)]


def confirm_rows(caps, card: Confirm, width: int) -> list[Text]:
    """The approval's rail with two keys instead of four, and no bell: an approval arrives while she
    works, this one answers something you just typed. The headline is the change; the body is what it
    costs you."""
    inner = _inner(width)
    if card.state == "pressed":
        return [_pressed(caps, card.answer)]

    rows = [Safe(caps.g["tab"] * 12, style="sun")]
    rows += _headline(caps, card.head, inner)

    airy = inner >= AIRY
    if airy:
        rows.append(_rail(caps, Safe("")))
    for chunk in _balanced(caps, card.why, inner):
        rows.append(_rail(caps, Safe(chunk, style="chrome")))
    if airy:
        rows.append(_rail(caps, Safe("")))

    rows += _key_rows(caps, card, inner, width)
    return rows + [_shadow(caps, rows, width)]


def receipt_row(caps, subject: str, decision: str) -> Text:
    """What the card collapses to once it is answered. A yes leaves a receipt exactly as a no does —
    the transcript has to say what was allowed, not only what was refused.

    It is one row, so a multi-line subject keeps its first line and COUNTS the rest. `execute_code`
    sends its whole snippet as the subject, and a receipt reading `run Python:` alone is a transcript
    saying a person approved a colon."""
    lines = subject.split("\n")
    rest = len(lines) - 1
    row = Safe()
    row.append(caps.g["ask"], style="sun")
    row.append(" " + lines[0], style="chrome")
    if rest:
        row.append(caps.t(f"  (+{rest} line{'' if rest == 1 else 's'})"), style="chrome")
    row.append(f"  {caps.g['arrow']} ", style="chrome")
    row.append(_drawn(caps, decision), style="hard")
    return row


def answered(key: str, family: str = "") -> tuple[str, str]:
    """(the pressed row's label, the receipt's decision) for one keypress, so the two never drift.
    Matched exactly: `key in "yY"` is a substring test, and the empty string passes it."""
    if key in ("y", "Y"):
        return "y — yes, go ahead", "yes, go ahead"
    if key in ("a", "A"):
        return f"a — always allow {family}", f"always — every {family} from now on"
    if key in ("t", "T"):
        return "t — always allow just this line", "always — this exact line from now on"
    return "n — no", "no"


def setting_row(caps, key: str, value: str, note: str, take: str, width: int) -> Text:
    """One settable key: name, effective value, and what /set will take. A read-only row has no third
    column — the absence IS the signal, which is how the web shows them too.

    Laid out by `_columns`, so the row cannot outgrow the window it was measured for. It shared the
    `max(2, …)` floor `info_row` describes, and overflowed at every width from 30 to 43.

    The note rides in the value's column and only when it fits there, so it is a bonus and never a claim
    on the layout — sizing the name column against a note that was going to be dropped anyway is what
    collapsed `reasoning_effort` to sixteen cells on an 80-column screen."""
    key, value, take = _drawn(caps, key), _drawn(caps, value), _drawn(caps, take)
    note_tail = ("  " if value else "") + _drawn(caps, f"({note})") if note else ""
    room, name, mid, take = _columns(caps, key, cell_len(value), take, width)
    row = Safe()
    row.append(name, style="hard")
    cell = Safe(_fitted(caps, value, mid))
    if note_tail and cell.cell_len + cell_len(note_tail) <= mid:
        cell.append(note_tail, style="chrome")
    row.append_text(cell)
    if take:
        row.append(" " * (room - row.cell_len - cell_len(take)) + take, style="chrome")
    return row


def info_row(caps, left: str, right: str = "", tail: str = "", width: int = 80) -> Text:
    """Name, value, metric — and never one cell wider than the measure it was given.

    A `max(2, …)` gap floor stopped shrinking and let the row grow past the window: a `/sessions` row
    measured 39 cells inside a 36-column terminal, and every width from 30 to 51 had at least one row
    over. `_columns` spends the deficit instead, so the gap before the metric is what is left over by
    construction rather than a floor that stops giving. Measured in CELLS throughout: `f"{left:<20}"`
    padded by CHARACTER, so a Japanese name filled the field in ten characters and started the value
    column nine cells right of every other row's."""
    room, name, mid, tail = _columns(caps, _drawn(caps, left), cell_len(_drawn(caps, right)),
                                     _drawn(caps, tail), width)
    row = Safe()
    row.append(name, style="hard" if right else "chrome")
    if right:
        row.append(_fitted(caps, _drawn(caps, right), mid))
    if tail:
        row.append(" " * (room - row.cell_len - cell_len(tail)) + tail, style="chrome")
    return row


def _columns(caps, name: str, wants: int, third: str, width: int) -> tuple[int, str, int, str]:
    """The three columns of a listing row, fitted to `width`: (the room, the name block, the value's
    cells, the third column). Every measure in cells, and their sum is `room` or less by construction —
    which is the whole repair, since the old rows ended in `max(2, …)` and simply grew when two cells
    were more than the window had left.

    Nothing that fits today moves: `NAME_COL` is claimed whenever the value and the third column do not
    need those cells, which at any ordinary width they do not, so an 80-column `/settings` is cell for
    cell what it was. Under pressure the name column gives up its PADDING first — it is the only column
    made of it — and each of the three keeps `COL_MIN`, because a row that silently drops its value
    reads as a setting that is unset rather than as a window that is narrow."""
    room = max(0, width - EDGE)
    third = _fitted(caps, third, max(COL_MIN, room - 2 * COL_MIN - EDGE))
    keep = (EDGE + cell_len(third)) if third else 0
    col = min(NAME_COL, max(COL_MIN, room - keep - min(wants, COL_MIN)))
    name = _column(caps, name, col, col if wants else max(COL_MIN, room - keep))
    return room, name, max(0, room - keep - cell_len(name)), third


def _column(caps, text: str, col: int, room: int) -> str:
    """`text` and the gap after it: `col` cells when the name is inside them, one cell of gap when it is
    not, and never wider than `room`.

    The gap is a FLOOR, never the remainder of a subtraction. `f"{text:<20}"` had nothing left to give
    the moment a name reached the width of its field, so a twenty-one-character name and the value
    beside it came out as one word — the defect `rows._role` had in the other file, in the other
    column."""
    text = _fitted(caps, text, max(0, room - 1))
    return text + " " * max(1, min(col - cell_len(text), room - cell_len(text)))


def _fitted(caps, text: str, room: int) -> str:
    """`text` in at most `room` CELLS, marked when it was cut. Cells rather than characters because a
    kaomoji and a CJK filename are the two places a character count silently doubles.

    A path gives up its middle before anything is cut off its end (`render/paths`). Nothing on the CARD
    reaches here — the command wraps and is never ellipsised — so this is the flush-left listings, where
    a saved grant or a setting can carry a path whose tail is its whole identity."""
    if room <= 0:
        return ""
    if cell_len(text) <= room:
        return text
    short = paths.shorten(text, room, caps.unicode)
    if short:
        return short
    mark = "…" if caps.unicode else "~"
    while text and cell_len(text) + cell_len(mark) > room:
        text = text[:-1]
    return text + mark


def blast_radius(command: str, *, entries: int = WALK_ENTRIES,
                 seconds: float = WALK_SECONDS) -> tuple[str, ...]:
    """What the command actually touches: real sizes, real counts, absolute paths.

    The walk stops at `entries` or `seconds`, whichever comes first, and says `at least` when it did —
    a number it did not finish counting would be a lie on the one card that must not tell any. A walk
    that was cut before it reached a single file says that instead of reporting an empty tree."""
    out = []
    for path in _targets(command):
        try:
            stat = path.lstat()
        except OSError:
            continue
        if path.is_dir() and not path.is_symlink():
            size, files, whole = _walk(path, entries, seconds)
        else:
            size, files, whole = stat.st_size, 1, True
        if not whole and not files:
            out.append(f"{_shown(path)}    too big to count in the moment you have")
            continue
        least = "" if whole else "at least "
        out.append(f"{_shown(path)}    {least}{_size(size)}, "
                   f"{files} file{'' if files == 1 else 's'}")
    return tuple(out)


def _inner(width: int) -> int:
    """The card's BODY measure: `INNER_MAX` is how much prose a person tracks across one line, and it
    caps the body only. The key rail is a menu, not prose, and is measured against `width - RAIL`."""
    return max(12, min(width - RAIL, INNER_MAX))


def _drawn(caps, text: str) -> str:
    """Third-party text, MEASURED and drawn as the same string.

    `Safe` is the net under every row, but it cannot be the only gate: a control character measures zero
    cells and the space it becomes measures one, so scrubbing after `wrap` and `cell_len` have run is how
    a card overflows the width it was just fitted to. Anything that gets measured is scrubbed here first."""
    return caps.t(scrub(text))


def _rail(caps, body: Text, colour: str = "sun") -> Text:
    row = Safe()
    row.append(caps.g["rail"], style=colour)
    row.append("  ")
    row.append_text(body)
    return row


def _plate(caps, label: str, slot: str) -> Text:
    plate = Safe()
    plate.append(f" {label} ", style=f"chip.{slot}")
    if caps.color != "none":
        plate.append(caps.g["shadow"], style=f"shadow.{slot}")
    return plate


def _headline(caps, title: str, inner: int) -> list[Text]:
    """The chip and what she wants beside it, wrapping under itself. The plate is 12 cells wide with its
    shadow, so a continuation row is indented past it rather than starting under the rail."""
    head = Safe()
    head.append_text(_plate(caps, "NEEDS YOU", "sun"))
    head.append(" ")
    wrapped = _balanced(caps, title, max(8, inner - 13))
    head.append(wrapped[0], style="hard")
    return [_rail(caps, head)] + [_rail(caps, Safe(" " * 12 + extra, style="hard"))
                                  for extra in wrapped[1:]]


def _command_rows(caps, card: Approval, inner: int) -> list[Text]:
    """What will run, and — when the window cannot hold all of it — how much is not on the screen.

    The budget is ROWS, never lines: `cmd.split("\\n")[:5]` drew a 1,733-character one-liner whole down
    37 rows while a six-line snippet lost its sixth line in silence. So what does not fit is COUNTED and
    named, and `?` raises the budget to the window — the notice pointing at `?` only when pressing it
    would really show more, since naming a key that changes nothing teaches distrust of the rail. Only a
    card that NAMES A COMMAND FAMILY is budgeted: an empty family is the wire saying this is not a
    command at all, and bounding is for text of unknown length somebody else authored. Leading
    whitespace is safety information, not layout — `wrap` rejoins on single spaces, so a snippet's
    indented body drew a flatter program than the one that would run."""
    lines: list[str] = []
    for line in scrub(card.cmd, newlines=True).split("\n"):
        lines += _lead_wrap(line, inner)
    while lines and not lines[-1].strip():
        lines.pop()
    if not getattr(card, "family", ""):
        return [_rail(caps, Safe(chunk)) for chunk in lines]
    opened = max(CMD_ROWS, caps.height - CARD_CHROME)
    room = opened if card.show_why else CMD_ROWS
    if len(lines) <= room:
        return [_rail(caps, Safe(chunk)) for chunk in lines]
    held = len(lines) - room
    tally = f"+{held} more line{'' if held == 1 else 's'}"
    said = f"{tally} — ? shows more of it" if opened > room else f"{tally}, not drawn here"
    return ([_rail(caps, Safe(chunk)) for chunk in lines[:room]]
            + [_rail(caps, Safe(chunk, style="sun")) for chunk in wrap(caps.t(said), inner)])


def _lead_wrap(line: str, width: int) -> list[str]:
    """`wrap`, with the line's own leading spaces held onto — first row and continuations alike, so a
    wrapped statement stays at the depth it runs at. An indent deeper than the card can honour is
    clamped to leave eight cells of code rather than pushed past the rail."""
    pad = line[:len(line) - len(line.lstrip(" "))]
    body = line[len(pad):]
    if not body:
        return [""]
    pad = pad[:max(0, width - 8)]
    return [pad + chunk for chunk in wrap(body, max(8, width - len(pad)))]


def _balanced(caps, text: str, inner: int) -> list[str]:
    """An advisory sentence, wrapped without a stub for a last row: a lone `one` or `needs you` under
    a full line reads as a typesetting accident on the one card that has to read calmly. Words are
    pulled down from the row above until the last one carries a third of the measure — only the final
    break moves, so the rows above keep the shape `wrap` gave them."""
    lines = wrap(_drawn(caps, text), inner)
    while len(lines) >= 2 and cell_len(lines[-1]) < inner // 3 and " " in lines[-2]:
        head, _, word = lines[-2].rpartition(" ")
        if not head or cell_len(f"{word} {lines[-1]}") > inner:
            break
        lines[-2:] = [head, f"{word} {lines[-1]}"]
    return lines


def _pressed(caps, answer: str) -> Text:
    row = Safe(" ")
    row.append(caps.g["rail"] + "  ", style="chrome")
    row.append_text(_plate(caps, "ANSWERED", "ink"))
    row.append("  " + _drawn(caps, answer), style="hard")
    return row


def _why_rows(caps, card: Approval, inner: int) -> list[Text]:
    rows = [_rail(caps, Safe(chunk, style="chrome"))
            for chunk in _balanced(caps, card.why, inner)]
    for line in card.blast:
        line = _drawn(caps, line)
        # A blast line that fits goes out untouched: `wrap` rejoins on single spaces and would
        # collapse the column that lines the sizes up under each other.
        for chunk in ([line] if cell_len(line) <= inner else wrap(line, inner)):
            rows.append(_rail(caps, Safe(chunk, style="chrome")))
    for line in _grant_lines(card):
        for chunk in _balanced(caps, line, inner):
            rows.append(_rail(caps, Safe(chunk, style="chrome")))
    return rows


def _grant_lines(card: Approval) -> list[str]:
    """What each standing-permission key really buys, and — when the broad one is missing — why.

    A key that is simply absent is indistinguishable from a key that is broken, which is exactly how the
    withheld `a` was read. The reason comes off the wire (`core/approval.always_note`) rather than being
    written here, so this panel and the web card cannot tell the person two different stories.

    A dangerous command is the one case the note is dropped: `why`, two rows above, already names the
    danger, and the web card carries the same sentence only because it has no `why` to say it in."""
    lines = []
    if card.can_always:
        lines.append(f"a means yes to every {card.family} from now on, not just this one")
    elif card.always_note and not card.danger:
        lines.append(card.always_note)
    if card.can_always_exact:
        lines.append("t means yes to this exact line from now on, and to nothing else")
    return lines


def _key_rows(caps, card, inner: int, width: int) -> list[Text]:
    """The key rail, or the flash that replaces it. A key that is not one of them is DISCARDED: "never
    swallow a keystroke" must not apply to a safety gate, or junk typed at the prompt becomes a pending
    message.

    The keys are ONE menu, measured against the RAIL's room (`width - RAIL`) and never `inner`, a PROSE
    measure that broke a 70-cell rail onto two rows on a 200-column terminal — a menu that splits with
    the window half empty reads as two menus. Below `AIRY` the labels take their short twins, and where
    the window cannot hold them the rail breaks BETWEEN keys rather than overflowing; `?` moves first,
    since it explains the other three and is the only one nobody has to press. The flash names the keys
    THIS card offers, never a fixed list: naming a key it never drew teaches distrust of the rail."""
    two = isinstance(card, Confirm)
    wide = inner >= AIRY
    if two:
        labels = [("y", card.yes), ("n", card.no)]
    else:
        labels = [("y", "go ahead" if wide else "yes")]
        if card.can_always:
            labels.append(("a", f"always allow {card.family}" if wide else "always"))
        if card.can_always_exact:
            labels.append(("t", "always allow just this line" if wide else "just this"))
        labels += [("n", "no"), ("?", "what it touches" if wide else "more")]
    if time.monotonic() < card.flash_until:
        # The prototype's `b.live`, spelt in the palette this package ships: base hue, bold stacked on.
        offered = [key for key, _ in labels]
        flash = Safe(caps.t("that key isn't one of them — "
                            + ", ".join(offered[:-1]) + " or " + offered[-1]),
                     style="live")
        flash.stylize("hard")
        return [_rail(caps, flash, "live")]
    gap = "   " if wide or two else "  "
    return [_rail(caps, line) for line in _menu_lines(caps, labels, gap, max(12, width - RAIL))]


def _menu(caps, labels: list[tuple[str, str]], gap: str) -> Text:
    """The keys as one line. The gap goes BETWEEN them and never after the last one: a trailing gap is
    three cells of nothing that the fit has to pay for, and paying for it is what pushed `?` down."""
    line = Safe()
    for key, label in labels:
        if line.cell_len:
            line.append(gap, style="chrome")
        line.append(key, style="hard")
        line.append(f" {_drawn(caps, label)}", style="chrome")
    return line


def _menu_lines(caps, labels: list[tuple[str, str]], gap: str, room: int) -> list[Text]:
    """One line wherever it fits, `?` alone on a second where it does not, and only then a plain break
    between keys — which is the order of how much each key costs the person to lose off the first row.

    The `?` rung is a GUARD, not a behaviour: swept over every rail this card can build (both label
    widths, both gaps, `a`/`t` present or withheld, nine family lengths, every room from 12 to 240 —
    16,488 cases) the greedy break below already produces exactly that split, 0 differences. It stays
    because the guarantee is worth stating in code rather than inferring from an arithmetic accident,
    and it must not be re-measured as a bug the day a label gets longer."""
    whole = _menu(caps, labels, gap)
    if whole.cell_len <= room:
        return [whole]
    if len(labels) > 1 and labels[-1][0] == "?":
        head = _menu(caps, labels[:-1], gap)
        if head.cell_len <= room:
            return [head, _menu(caps, labels[-1:], gap)]
    lines, run = [], [labels[0]]
    for pair in labels[1:]:
        if _menu(caps, run + [pair], gap).cell_len > room:
            lines.append(_menu(caps, run, gap))
            run = [pair]
        else:
            run.append(pair)
    return lines + [_menu(caps, run, gap)]


def _shadow(caps, rows: list[Text], width: int) -> Text:
    under = min(max(row.cell_len for row in rows), width - 2)
    return Safe(" " + caps.g["under"] * max(0, under), style="shadow.sun")


def _targets(command: str) -> list[Path]:
    parts = command_line.split(command)
    seen, out = set(), []
    for token in parts[1:]:
        if token.startswith("-") or token in seen:
            continue
        seen.add(token)
        path = Path(os.path.abspath(Path(token).expanduser()))
        if path.is_symlink() or path.exists():
            out.append(path)
        if len(out) >= BLAST_PATHS:
            break
    return out


def _walk(root: Path, entries: int, seconds: float) -> tuple[int, int, bool]:
    size = files = seen = 0
    deadline = time.monotonic() + seconds
    for parent, dirs, names in os.walk(root):
        for name in names:
            try:
                size += os.lstat(os.path.join(parent, name)).st_size
                files += 1
            except OSError:
                pass
        seen += len(names) + len(dirs)
        if seen >= entries or time.monotonic() >= deadline:
            return size, files, False
    return size, files, True


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" or n >= 10 else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.0f} TB"


def _shown(path: Path) -> str:
    """Kept as this module's name for it; `render/paths` is where every path is written now."""
    return paths.shown(path)
