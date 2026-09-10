"""Cutting her stream into blocks that can be printed once and never revised.

Scrollback is append-only, so a renderer has to know when a run of text will not change again: a blank
line ends a paragraph, except inside a fence, where blank lines are code, and except between the items
of a loose list. An unclosed fence is closed here so it renders as code rather than leaking backticks.

A URL wider than her measure cannot survive inside a block — Rich folds a token with no break in it, so
a real newline lands inside the URL and kills it as one selectable run; it takes an unwrapped row of
its own. Rich's tables ELLIPSISE, so every column folds, and DROP `<br>`, so it becomes a hardbreak.
"""
from __future__ import annotations

import re

from kotoba.core.text_security import scrub
from rich.cells import cell_len
from rich.markdown import CodeBlock, Markdown, TableElement
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

FENCE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+")
LINK = re.compile(r"\[([^\]\n]*)\]\(\s*<?(https?://[^)>\s]+)>?[^)]*\)")
# The trailing punctuation is held out of the match by a lookahead: swallowed into the URL it makes a
# dead link, and dropped with it, it takes a comma out of her sentence.
BARE = re.compile(r"<?(https?://[^\s<>\[\]()]+?)>?(?=[.,;:!?]*(?:\s|$))")
MARK = "\x00"
GAP = re.compile(r"\(\s*\x00\s*\)|\[\s*\x00\s*\]|\x00")
RUN = re.compile(r"(?<=\S)[ \t]{2,}")
BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


class Blocks:
    """Feed it her stream, take finished blocks out. `partial` is what is still in flight."""

    def __init__(self) -> None:
        self._tail = ""
        self._lines: list[str] = []
        self._fence = ""
        self._blanks = 0

    def feed(self, chunk: str) -> list[str]:
        out: list[str] = []
        self._tail += chunk
        while "\n" in self._tail:
            line, self._tail = self._tail.split("\n", 1)
            self._line(line, out)
        return out

    def flush(self) -> list[str]:
        out: list[str] = []
        if self._tail:
            self._line(self._tail, out)
            self._tail = ""
        if self._fence:
            self._lines.append(self._fence)
            self._fence = ""
        self._emit(out)
        return out

    @property
    def partial(self) -> str:
        held = self._lines + [""] * self._blanks + ([self._tail] if self._tail else [])
        return "\n".join(held)

    def _line(self, line: str, out: list[str]) -> None:
        if self._fence:
            self._lines.append(line)
            if self._closes(line):
                self._fence = ""
                self._emit(out)
            return
        if not line.strip():
            # A paragraph is finished at its blank line and goes out THERE — waiting for the next
            # paragraph's first line would leave her last one unprinted until the turn ended. A list
            # cannot: the blank may be spacing between its items, and only the next line says which.
            if self._lines and not self._in_list:
                self._emit(out)
            elif self._lines:
                self._blanks += 1
            return
        marker = self._opens(line)
        if marker:
            self._emit(out)
            self._fence = marker
            self._lines = [line]
            return
        if self._blanks:
            if self._continues(line):
                self._lines += [""] * self._blanks
                self._blanks = 0
            else:
                self._emit(out)
        self._lines.append(line)

    def _opens(self, line: str) -> str:
        """A fence indented under an open list item is that item's code, not a new block."""
        m = FENCE.match(line)
        if not m or (self._in_list and m.group("indent")):
            return ""
        return m.group("marker")[0] * len(m.group("marker"))

    def _closes(self, line: str) -> bool:
        m = FENCE.match(line)
        return bool(m and not m.group("info").strip()
                    and m.group("marker")[0] == self._fence[0]
                    and len(m.group("marker")) >= len(self._fence))

    @property
    def _in_list(self) -> bool:
        return bool(self._lines) and bool(LIST_ITEM.match(self._lines[0]))

    def _continues(self, line: str) -> bool:
        """A blank line inside a list is spacing, not an ending — the list goes on if the next line is
        another item or an indented continuation of one."""
        return self._in_list and bool(LIST_ITEM.match(line) or line[:1] in (" ", "\t"))

    def _emit(self, out: list[str]) -> None:
        block = "\n".join(self._lines).strip("\n")
        self._lines, self._blanks = [], 0
        if block.strip():
            out.append(block)


class Fence(CodeBlock):
    """Rich pads a fenced block with a blank row above and below. On top of the row her paragraphs
    already get, that is two rows of air around three lines of code and the reply reads as three
    messages. Keep the column of inset — the code needs it — and drop the rows."""

    def __rich_console__(self, console, options):
        yield Syntax(str(self.text).rstrip(), self.lexer_name, theme=self.theme,
                     word_wrap=True, padding=(0, 1))


class Grid(TableElement):
    """Rich's table with every column set to FOLD: a cell that ellipsises has lost the text it was
    holding, and a folded one is only taller.

    Folded past its words it is only a mess: at sixty columns five columns of prices came out as
    `Facturaci` over `ón`, complete and unreadable. So the grid is MEASURED first — Rich's minimum is
    the layout in which no word breaks — and a window narrower than that gets each row as a block
    of `heading  value` lines instead, the way a nameplate folds to two rows under `ROSTER_MIN_W`
    (`rows.helper_rows`): the same text, taller, every word whole."""

    def __rich_console__(self, console, options):
        for table in super().__rich_console__(console, options):
            # The table's own measure, not `Measurement.get`: that one clamps the minimum to the
            # window, so a grid too narrow for its words could never be told from one that fits.
            fits = table.__rich_measure__(console, options).minimum <= options.max_width
            for column in table.columns:
                column.overflow = "fold"
            if fits:
                yield table
                return
            yield from self._stacked(options.max_width)

    def _stacked(self, width: int):
        heads = [c.content.copy() for c in self.header.row.cells] if self.header and self.header.row \
            else []
        for head in heads:
            head.stylize("markdown.table.header")
        col = min(max((cell_len(h.plain) for h in heads), default=1), max(1, width // 3))
        for n, row in enumerate(self.body.rows if self.body else []):
            block = Table.grid(padding=(0, 2))
            block.add_column(width=col, overflow="fold")
            block.add_column(overflow="fold")
            for i, cell in enumerate(row.cells):
                block.add_row(heads[i] if i < len(heads) else Text(""), cell.content)
            if n:
                yield Text("")
            yield block


class Prose(Markdown):
    elements = {**Markdown.elements, "fence": Fence, "code_block": Fence, "table_open": Grid}

    def __init__(self, markup: str, **kw) -> None:
        super().__init__(markup, **kw)
        for token in self.parsed:
            for child in token.children or ():
                if child.type == "html_inline" and BR.fullmatch(child.content.strip()):
                    child.type, child.tag, child.content = "hardbreak", "br", ""


def lift_urls(block: str, room: int) -> tuple[str, list[str]]:
    """(the block without the URLs too wide for `room`, those URLs in the order she wrote them).

    A link keeps its label and loses only its target; a bare one goes, and the empty bracket pair it
    was sitting in goes with it. Fenced code is left alone — a URL in a snippet is code, and code is
    quoted, never rearranged.

    Nothing else may change. Only the pair around the lifted URL's mark is taken, because a rule that
    deleted every empty pair turned her `f()` into `f` and her `list[]` into `list`; and only a run of
    spaces with text before it is closed up, because closing every run unindented the four-space code
    block two paragraphs down and stopped it being code."""
    if "```" in block or "http" not in block:
        return block, []
    out: list[str] = []

    def link(m):
        if cell_len(m.group(2)) <= room:
            return m.group(0)
        out.append(m.group(2))
        return m.group(1)

    def bare(m):
        if cell_len(m.group(1)) <= room:
            return m.group(0)
        out.append(m.group(1))
        return MARK

    text = BARE.sub(bare, LINK.sub(link, block.replace(MARK, " ")))
    if not out:
        return block, []
    text = RUN.sub(" ", GAP.sub("", text))
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n"), out


def prose(block: str, caps) -> Prose:
    """One block, ready to print.

    An unclosed fence needs no repair here: CommonMark ends one at the end of its containing block, and
    the parser Rich uses does exactly that — measured on a partial mid-fence for ``` and for ~~~ alike.
    Counting backticks and appending one did nothing for a three-backtick fence and, inside a FOUR-
    backtick one, wrote a line of ``` into her code that she never typed.

    Scrubbed like every card is: Rich strips a carriage return and nothing else, so an erase-line escape
    or a bidi override in her reply reaches the terminal and rewrites what the user believes they read.
    Newlines are kept — the block splitter needs them."""
    text = caps.t(scrub(block, newlines=True))
    theme = "ansi_light" if caps.background == "light" else "ansi_dark"
    return Prose(text, code_theme=theme, hyperlinks=False)
