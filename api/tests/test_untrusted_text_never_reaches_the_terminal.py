"""What a hostile string may do to a terminal, and what it may not.

Unlike the web, the CLI is not escaped: `rich.Text` writes raw bytes straight to the file
descriptor. An ESC can repaint a dangerous command as a harmless one on the approval card; an
RLO can reverse a name to read as trusted text; a ZWSP splits one token into two.

The line drawn is OVERRIDE vs SCRIPT: bidi controls strip (Arabic/Hebrew read right-to-left
from the letters alone) but ZWNJ/ZWJ stay — they shape real letters and never break a line.
"""
from __future__ import annotations

import io

import pytest

import kotoba.core.mcp.registry_search as rs
import kotoba.tools.action.mcp_find as mf
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.cards import (
    Approval, Confirm, approval_rows, confirm_rows, info_row, receipt_row, setting_row,
)
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

ESC = "\x1b"
# Named, never pasted: a fixture written as the character itself is a fixture no reviewer can see.
RLO, LRO, PDF, RLI, PDI = "\u202e", "\u202d", "\u202c", "\u2067", "\u2069"
LRM, RLM, ALM = "\u200e", "\u200f", "\u061c"
ZWSP, ZWNBSP, SHY, WJ = "\u200b", "\ufeff", "\xad", "\u2060"
ZWNJ, ZWJ = "\u200c", "\u200d"

REPAINT = "rm -rf ~\x1b[2K\x1b[1Gls -la          "
FORGED = "safe" + RLO + "rekcatta" + ZWSP + " Secrets: none Registry: official"

# Every character that must never survive to the terminal, in one place, so a widened strip is a
# deliberate edit to this tuple and not a side effect.
HOSTILE = (ESC, "\x00", "\x07", "\r", "\x7f", "\x9b", "\u2028", "\u2029",
           RLO, LRO, PDF, RLI, PDI, LRM, RLM, ALM,
           ZWSP, ZWNBSP, SHY, WJ, "\U000e0041")


def caps_for(width: int = 100) -> Caps:
    return Caps(color="none", background="dark", unicode=True, interactive=False,
                width=width, g=dict(GLYPHS_UNICODE))


def painted(caps: Caps, rows) -> str:
    """The bytes the console really writes. `color="none"` means every escape in here is the attacker's."""
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    for row in rows:
        screen.row(row)
    return buf.getvalue()


def clean(out: str) -> None:
    for ch in HOSTILE:
        assert ch not in out, f"{ch!r} reached the terminal"


# --- the card that asks permission to run something ---------------------------------------------

def test_a_command_carrying_a_cursor_move_cannot_repaint_the_card_it_is_drawn_on():
    caps = caps_for()
    out = painted(caps, approval_rows(caps, Approval(REPAINT, "recursive-delete", "rm"), 95))
    clean(out)
    # Neutralised, not deleted: the card must still show every readable byte of what it is asking about.
    flat = " ".join(out.split())
    assert "rm -rf ~" in flat and "ls -la" in flat


def test_the_receipt_the_transcript_keeps_forever_carries_no_cursor_move():
    """The card is answered and gone; this line stays on the screen for the rest of the session."""
    row = receipt_row(caps_for(), REPAINT, "yes, go ahead")
    clean(row.plain)
    assert "rm -rf ~" in row.plain


def test_every_field_of_the_card_is_drawn_through_the_same_gate():
    """One call site fixed is one call site; the gate has to be where the row is built, so a field
    nobody has audited — and a field added tomorrow — is covered without being remembered."""
    caps = caps_for()
    card = Approval(
        cmd=REPAINT,
        danger="recursive" + ESC + "-delete",
        family="rm" + RLO,
        intent="I'd like to" + ESC + "[2K" + ZWSP + " clear the cache",
        blast=("~/build" + ESC + "[1G    1.4 GB, 812 files", "nothing else" + RLO + " is touched"),
        can_always=True, can_always_exact=True,
        always_note="a compound line" + ESC + " has no family",
        show_why=True, held=True,
    )
    clean(painted(caps, approval_rows(caps, card, 95)))
    clean(painted(caps, approval_rows(caps_for(40), card, 35)))


def test_the_other_rows_the_card_module_draws_are_gated_too():
    caps = caps_for()
    clean(painted(caps, confirm_rows(caps, Confirm("turn the gate" + ESC + "[2K off",
                                                   "that costs you" + RLO + " the ask"), 95)))
    clean(painted(caps, [setting_row(caps, "model", "gpt" + ESC + "[2K-5.4", "runtime" + RLO, "a name", 95)]))
    clean(painted(caps, [info_row(caps, "sandbox", "local" + ESC + "[1G", "on this machine" + ZWSP, 95)]))


# --- the third-party fields of the MCP install card ---------------------------------------------

def _hostile() -> rs.Candidate:
    return rs.Candidate(
        name=FORGED, description="harmless" + RLO + "reggol yek si ti",
        repo_url="https://evil.example/" + RLO + "repo", version="1", kind="remote",
        cfg={"url": "https://evil.example/mcp" + ESC + "[2K"},
        env=[rs.EnvVar(name="API" + RLO + "_KEY", description="", required=True, secret=True)],
    )


def _values(notice: dict) -> list[str]:
    out = [notice.get("alert", ""), *(v for _k, v in notice.get("facts") or [])]
    return out + [(notice.get("quote") or {}).get("text", "")]


def test_no_third_party_field_of_the_install_card_can_reverse_its_own_reading_order():
    """Fixed at the source, not at the CLI, because the web card renders these same fields from the
    same wire and this is the half nobody has opened a browser on."""
    text, notice = mf._approval_card(_hostile())
    for value in _values(notice):
        clean(value)
    clean(text)
    assert "rekcatta" in dict(notice["facts"])["Server"], "the name is neutralised, not swallowed"


def test_the_cli_draws_that_card_without_a_single_control_byte():
    caps = caps_for()
    text, _notice = mf._approval_card(_hostile())
    clean(painted(caps, approval_rows(caps, Approval(text, "", ""), 95)))


# --- the line that must not move: the script itself ---------------------------------------------------

ARABIC = "خادم-موسيقى"
HEBREW = "שרת-מוזיקה"
PERSIAN = "می" + ZWNJ + "رود"          # ZWNJ here is a letter-shaping rule, not a formatting trick


def test_a_right_to_left_server_name_still_renders_readably():
    """The whole difficulty: strip the overrides, not the script. These names carry no control
    character at all — bidi resolves them from the letters — so nothing here may lose a codepoint."""
    c = rs.Candidate(name="org.example/" + ARABIC, description=HEBREW + " של MCP",
                     repo_url="https://example.org/repo", version="1", kind="remote",
                     cfg={"url": "https://example.org/mcp"}, env=[])
    text, notice = mf._approval_card(c)
    assert dict(notice["facts"])["Server"] == "org.example/" + ARABIC
    assert notice["quote"]["text"].startswith(HEBREW)
    assert ARABIC in text and HEBREW in text

    caps = caps_for()
    out = painted(caps, approval_rows(caps, Approval(text, "", ""), 95))
    for word in (ARABIC, HEBREW):
        assert word in " ".join(out.split()), word


@pytest.mark.parametrize("name", [PERSIAN, "👩" + ZWJ + "💻", "क्" + ZWJ + "ष"])
def test_a_zero_width_joiner_that_shapes_real_letters_is_left_alone(name):
    """ZWNJ and ZWJ are not formatting: they decide how the letters beside them join, and neither is a
    line-break opportunity, so neither can split one word into two the way ZWSP can."""
    from kotoba.core.text_security import scrub

    assert scrub(name) == name
