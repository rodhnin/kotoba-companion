"""Two rules for every surface the CLI draws: it may lose nothing, and it may invent nothing.

Losing is the ordering trap — a control character costs zero cells and the space it becomes costs one,
so a row measured before it is scrubbed is wider than the column it was just fitted to, and what falls
off the end is text nobody chose to drop.

Inventing is the quieter side: `lift_urls` stripped every empty bracket pair and run of spaces while
tidying a too-wide URL, turning a code block into prose; `prose` appended a stray marker into a
four-backtick block it never wrote; `column` under-delivered by one cell, leaving a table row a cell
left of the rest."""
from __future__ import annotations

import io
from types import SimpleNamespace

from prompt_toolkit.buffer import Buffer
from rich.cells import cell_len

from kotoba.cli import state
from kotoba.cli.events_bridge import Step
from kotoba.cli.input.menu import Roster
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.markdown import Blocks, lift_urls, prose
from kotoba.cli.render.text import column, head, wrap
from kotoba.cli.render.theme import GLYPHS_ASCII, GLYPHS_UNICODE, build_console

WIDE = {
    "japanese": "日本語のファイル名がとても長いときにどうなるかを確かめる",
    "korean": "안녕하세요" * 12,
    "emoji": "🎌🌸🍡" * 20,
    "family": "👩‍👩‍👧‍👦" * 12,
    "combining": "é" * 40,
    "one token": "A" * 500,
    "path": "/home/jordan/" + "muy-largo-sin-espacios-" * 12 + "final.txt",
}
LONG_URL = "https://example.com/" + "a" * 60


def caps_for(width: int = 96, *, unicode: bool = True) -> Caps:
    return Caps(color="none", background="dark", unicode=unicode, interactive=True,
                width=width, height=40, g=dict(GLYPHS_UNICODE if unicode else GLYPHS_ASCII))


def rendered(text: str, width: int = 76) -> str:
    caps = caps_for(width)
    buf = io.StringIO()
    console = build_console(caps, file=buf)
    console.width = width
    console.print(prose(text, caps))
    return buf.getvalue()


# --- the measure ------------------------------------------------------------------------------------

def test_a_column_is_the_cell_count_it_says_it_is_in_every_script():
    for name, text in WIDE.items():
        for width in (12, 29, 30, 31, 42, 80):
            assert cell_len(column(text, width, True)) == width, (name, width)
            assert cell_len(column(text[:3], width, True)) == width, (name, width)


def test_the_mark_that_says_there_was_more_is_measured_into_the_width_not_added_to_it():
    for name, text in WIDE.items():
        for width in (8, 20, 21, 40):
            for unicode in (True, False):
                assert cell_len(head(text, width, unicode)) <= width, (name, width, unicode)


def test_wrapping_never_overruns_and_never_drops_a_character():
    for name, text in WIDE.items():
        for width in (12, 31, 62):
            lines = wrap(text, width)
            assert max(cell_len(line) for line in lines) <= width, (name, width)
            assert "".join(lines).replace(" ", "") == text.replace(" ", ""), (name, width)


# --- the menu, measured before it is scrubbed ---------------------------------------------------------

def _roster_rows(summary: str, width: int = 96):
    caps = caps_for(width)
    job = state.Work(goal="tidy the repo", n=1)
    helper = state.Helper(sid="h1", role="research", goal="find the flag", state="failed")
    helper.started, helper.stopped, helper.summary = 1.0, 4.0, summary
    job.helpers.append(helper)
    panel = Roster(SimpleNamespace(caps=caps, work=job))
    panel.stack = [("helper", "1")]
    buf = Buffer()
    assert panel.sync(buf)
    return caps, buf.complete_state.completions


def test_a_panel_row_is_scrubbed_before_it_is_measured_not_after():
    """Scrubbed afterwards, this summary grew ten cells past the column it had just been fitted to and
    `footer.menu_rows` ellipsised the overrun away — text nobody chose to drop."""
    said = "the flag was in{} src/config.py on line 40, and the second one was in tests"
    caps, clean = _roster_rows(said.format(""))
    _caps, dirty = _roster_rows(said.format("\x00" * 20))
    assert [c.display_text for c in clean] == [c.display_text for c in dirty]
    for one in dirty:
        assert cell_len(one.display_text) == max(24, caps.width - 16)
    drawn = footer.menu_rows(caps, dirty, 0, 20)
    assert "in tests" in drawn[-1].plain and "…" not in drawn[-1].plain


def test_a_rows_right_hand_tail_is_scrubbed_before_the_padding_is_counted():
    """`Safe` scrubs on the way to the terminal, which is one step too late for a string the padding
    arithmetic has already sized. Both tails here are third-party: a saved grant's family is the first
    token of a command the model wrote, and a reminder's `every` is her own phrasing."""
    caps = caps_for(90)
    width, nul = 78, "\x00" * 12
    tool = state.Tool(verb="shell", arg="df -h", detail="exit 0", state="ok",
                      note="you always allow" + nul + " npm")
    tool.stopped = tool.started + 2
    due = state.Gift("due", "water the plants", "", tail="every day at 9" + nul)
    drawn = [rows.tool_row(caps, Step("1", "shell", "df -h", "ok", "exit 0", note=tool.note),
                           width, elapsed=2.0, still=True),
             rows.tool_text(caps, tool, width, still=True),
             rows.tool_text(caps, tool, width, still=True, word="lapsed" + nul),
             rows.work_row(caps, state.Work(goal="g", n=1, state="ok"), width, word="done" + nul),
             *rows.gift_rows(caps, due, 1, width)]
    for row in drawn:
        assert cell_len(row.plain) <= width, row.plain


# --- prose: nothing invented --------------------------------------------------------------------------

def test_an_unclosed_fence_is_closed_by_the_parser_and_nothing_is_written_into_her_code():
    """A four-backtick fence used to collect a line of ``` it never had, because the repair counted
    three-backtick runs. The parser ends an unclosed fence at the end of its block on its own."""
    for marker in ("```", "~~~", "````"):
        held = Blocks()
        held.feed(f"{marker}python\ndef f():\n    return 1\n")
        out = rendered(held.partial)
        assert "def f():" in out and "return 1" in out
        assert "```" not in out and "~~~" not in out, marker


def test_lifting_a_url_leaves_the_rest_of_the_paragraph_exactly_as_she_wrote_it():
    block, urls = lift_urls(f"call f() after reading {LONG_URL}, then list[] it", 40)
    assert urls == [LONG_URL], urls
    assert "f()" in block and "list[]" in block
    assert "," in block, "the comma belongs to her sentence, not to the URL"


def test_lifting_a_url_does_not_unindent_the_code_block_under_it():
    block, urls = lift_urls(f"see {LONG_URL}\n\n    def f():\n        return 1", 40)
    assert urls == [LONG_URL]
    assert "    def f():" in block and "        return 1" in block


def test_a_url_short_enough_to_wrap_is_left_where_she_put_it():
    block, urls = lift_urls("see https://a.example/x now", 40)
    assert urls == [] and block == "see https://a.example/x now"


def test_a_reply_that_is_all_of_one_thing_still_renders(tmp_path):
    """A fence that never closes, a table wider than the window, a language Pygments has never heard of,
    and two thousand lines of it — none of these may raise, and none may run off the edge."""
    width = 76
    cases = [
        "```python\ndef f():\n    return 1",
        "```wenyan-lang\n吾有一數。曰三。名之曰「甲」。\n```",
        "| " + " | ".join(f"column{n}" for n in range(9)) + " |\n" + "|" + "---|" * 9 + "\n"
        + "| " + " | ".join(f"value{n}" * 3 for n in range(9)) + " |",
        "- one\n  - two\n    - three\n      - four",
        "```python\n" + "\n".join(f"x{n} = {n}" for n in range(2000)) + "\n```",
    ]
    for text in cases:
        out = rendered(text, width)
        assert [line for line in out.split("\n") if cell_len(line) > width] == [], text[:40]
