"""The card that asks: it must read whole at every width, and it must never cut a command in half."""
from __future__ import annotations

import io
import os
import time

from rich.cells import cell_len

from kotoba.cli.render.caps import Caps
from kotoba.cli.render.cards import (
    Approval, Confirm, answered, approval_rows, blast_radius, confirm_rows, info_row, receipt_row,
    setting_row,
)
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_ASCII, GLYPHS_UNICODE, build_console

INTENT = "I want to clear the old build cache — it's the biggest thing in there"
CMD = "rm -rf ~/.kotoba/tmp/build"
BLAST = ("~/.kotoba/tmp/build    1.4 GB, 812 files",
         "nothing outside ~/.kotoba/tmp is touched")
WIDTHS = (40, 64, 100, 200)
CONSEQUENCE = ("that turns the gate off — I'd be able to run things on this machine "
               "without asking you")


def caps_for(width: int, *, unicode: bool = True, color: str = "none") -> Caps:
    return Caps(color=color, background="dark", unicode=unicode, interactive=color != "none",
                width=width, g=dict(GLYPHS_UNICODE if unicode else GLYPHS_ASCII))


def budget(caps: Caps) -> int:
    """What the app hands the card: the measure it draws into, from column 0."""
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=False))
    return screen.w - screen.gutter


def painted(caps: Caps, rows) -> str:
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf, force_terminal=caps.interactive or None),
                    portrait=Portrait(caps, wanted=False))
    for row in rows:
        screen.row(row)
    return buf.getvalue()


def card(**kw) -> Approval:
    return Approval(CMD, "recursive-delete", "rm", INTENT, BLAST, **kw)


def test_the_card_fits_every_width_and_nothing_on_it_is_ever_ellipsised():
    for width in WIDTHS:
        caps = caps_for(width)
        room = budget(caps)
        out = painted(caps, approval_rows(caps, card(show_why=True), room))
        assert [line for line in out.split("\n") if cell_len(line) > room] == []
        assert "…" not in out and "..." not in out


def test_no_row_of_either_card_runs_off_any_terminal_from_thirty_columns_up():
    for width in range(30, 201):
        for unicode in (True, False):
            caps = caps_for(width, unicode=unicode)
            room = budget(caps)
            drawn = (approval_rows(caps, card(show_why=True), room)
                     + approval_rows(caps, card(held=True), room)
                     + confirm_rows(caps, Confirm("sandbox local → none", CONSEQUENCE), room))
            over = [line for line in painted(caps, drawn).split("\n") if cell_len(line) > room]
            assert over == [], (width, unicode, over)


def test_the_headline_is_her_intent_and_the_classification_waits_for_the_question_mark():
    caps = caps_for(100)
    closed = painted(caps, approval_rows(caps, card(), budget(caps)))
    opened = painted(caps, approval_rows(caps, card(show_why=True), budget(caps)))
    assert "clear the old build cache" in closed
    assert "recursive delete" not in closed
    assert "recursive delete" in opened and "1.4 GB, 812 files" in opened


def test_the_question_mark_panel_says_what_always_will_persist_by_family_not_by_command():
    caps = caps_for(100)
    out = painted(caps, approval_rows(caps, card(show_why=True), budget(caps)))
    assert "a means yes to every rm from now on, not just this one" in out


def test_the_blast_lines_keep_their_column_when_they_fit_and_wrap_only_when_they_cannot():
    wide = painted(caps_for(100), approval_rows(caps_for(100), card(show_why=True), budget(caps_for(100))))
    assert "~/.kotoba/tmp/build    1.4 GB, 812 files" in wide
    narrow = painted(caps_for(40), approval_rows(caps_for(40), card(show_why=True), budget(caps_for(40))))
    assert "1.4 GB," in narrow and "812" in narrow


def test_the_key_rail_offers_exactly_y_a_n_and_the_question_mark():
    """All four, in order, however many rows they take: the rail breaks BETWEEN keys, so a window that
    cannot hold them on one line spends a second one rather than dropping the key that explains the
    other three."""
    for width in WIDTHS:
        caps = caps_for(width)
        drawn = painted(caps, approval_rows(caps, card(), budget(caps))).replace(caps.g["rail"], " ")
        rail = " ".join(line for line in drawn.split("\n")
                        if any(f" {k} " in f" {line} " for k in ("y", "a", "n", "?")))
        assert [word for word in rail.split() if len(word) == 1] == ["y", "a", "n", "?"], width


def test_the_key_rail_is_one_line_wherever_the_window_has_room_for_it():
    """The keys are ONE menu. They were being fitted against `INNER_MAX`, which is a PROSE measure —
    how much text a person tracks across one line — so on a 200-column terminal a 70-cell rail broke
    in two and `?` sat alone on a row of its own with 130 columns to spare. The measure is the rail's,
    `width - RAIL`, and the only thing allowed to split the menu is a window that cannot hold it.

    Both key rails are covered: `y a n ?`, where "always" is offered, and `y t n ?`, where only this
    exact command can be allowed."""
    asked = (Approval(CMD, "", "execute_code"),
             Approval(CMD, "", "npm", can_always=False, can_always_exact=True))
    for width in (80, 100, 120, 160, 200):
        caps = caps_for(width)
        room = budget(caps)
        for drawn in asked:
            rails = [r.plain for r in approval_rows(caps, drawn, room)
                     if " go ahead" in r.plain or "what it touches" in r.plain]
            assert len(rails) == 1, (width, drawn.family, rails)
            assert rails[0].rstrip().endswith("what it touches"), rails[0]
        two = confirm_rows(caps, Confirm("sandbox local → none", CONSEQUENCE), room)
        keys = [r.plain for r in two if "change it" in r.plain or "leave it as it is" in r.plain]
        assert len(keys) == 1, (width, keys)
    at_120 = caps_for(120)
    five = [r.plain for r in approval_rows(
        at_120, Approval(CMD, "", "rm", can_always=True, can_always_exact=True), budget(at_120))
        if " go ahead" in r.plain or "what it touches" in r.plain]
    assert len(five) == 1, "five keys fit on one rail from 120 columns up"


def test_when_the_rail_truly_cannot_fit_it_is_the_question_mark_that_moves():
    """`?` explains the other three and is the only key nobody has to press, so it is the one that
    goes to a second row — never `y`, never `n`. Five keys at eighty columns is the one card in the
    product that runs out of rail."""
    caps = caps_for(80)
    room = budget(caps)
    five = Approval(CMD, "", "rm", can_always=True, can_always_exact=True)
    rails = [r.plain.strip(caps.g["rail"] + " ") for r in approval_rows(caps, five, room)
             if " go ahead" in r.plain or "what it touches" in r.plain]
    assert len(rails) == 2, rails
    assert rails[0].startswith("y go ahead") and rails[0].rstrip().endswith("n no"), rails[0]
    assert rails[1] == "? what it touches", rails[1]


def test_a_card_the_gate_will_not_persist_offers_no_a_and_makes_no_promise_about_it():
    """`can_always=False` is the backend saying an `a` would persist nothing — a dangerous command, or
    a compound whose first-token family does not name what runs. A key whose promise the gate would
    refuse may not be drawn, the `?` panel may not describe it, and the flash may not ask for it."""
    caps = caps_for(100)
    room = budget(caps)
    out = painted(caps, approval_rows(caps, card(show_why=True, can_always=False), room))
    assert "always allow" not in out and "a means yes to every" not in out
    drawn = painted(caps, approval_rows(caps, card(can_always=False), room)).replace(caps.g["rail"], " ")
    rail = " ".join(line for line in drawn.split("\n")
                    if any(f" {k} " in f" {line} " for k in ("y", "n", "?")))
    assert [word for word in rail.split() if len(word) == 1] == ["y", "n", "?"]
    flash = painted(caps, approval_rows(
        caps, card(can_always=False, flash_until=time.monotonic() + 5), room))
    assert "y, n or ?" in flash and "y, a, n" not in flash


def test_the_card_without_a_still_fits_every_width():
    for width in WIDTHS:
        caps = caps_for(width)
        room = budget(caps)
        out = painted(caps, approval_rows(caps, card(show_why=True, can_always=False), room))
        assert [line for line in out.split("\n") if cell_len(line) > room] == []
        assert "always" not in out


def test_the_held_card_says_it_waited_and_draws_no_countdown():
    caps = caps_for(100)
    out = painted(caps, approval_rows(caps, card(held=True), budget(caps)))
    assert "I asked while you were away" in out
    assert "180" not in out and "3:00" not in out


def test_a_wrong_key_flashes_the_four_it_wants_and_the_confirm_flashes_only_two():
    caps = caps_for(100)
    flash = painted(caps, approval_rows(caps, card(flash_until=time.monotonic() + 5), budget(caps)))
    assert "that key isn't one of them — y, a, n or ?" in flash
    two = Confirm("sandbox local → none", CONSEQUENCE, flash_until=time.monotonic() + 5)
    assert "y or n" in painted(caps, confirm_rows(caps, two, budget(caps)))


def test_the_answered_card_collapses_to_one_row_and_then_to_one_receipt():
    caps = caps_for(100)
    label, decision = answered("y")
    assert (label, decision) == ("y — yes, go ahead", "yes, go ahead")
    assert answered("a", "rm")[0] == "a — always allow rm"
    pressed = painted(caps, approval_rows(caps, card(state="pressed", answer=label), budget(caps)))
    assert pressed.count("\n") == 1 and "ANSWERED" in pressed and "yes, go ahead" in pressed
    assert painted(caps, [receipt_row(caps, CMD, decision)]).strip() == f"? {CMD}  → yes, go ahead"


def test_a_twelve_line_command_is_clamped_to_five_rows_and_keeps_its_rail():
    caps = caps_for(100)
    long = Approval("\n".join(f"line {n} of it" for n in range(12)), "", "npm", INTENT)
    out = painted(caps, approval_rows(caps, long, budget(caps)))
    assert "line 4 of it" in out and "line 5 of it" not in out
    assert all(line.startswith(caps.g["rail"]) for line in out.split("\n") if "line " in line)
    assert out.rstrip().endswith(caps.g["under"])


def test_the_rows_the_card_holds_back_are_counted_on_it_and_never_dropped_in_silence():
    """The one thing this surface may not do. `execute_code` sends its whole snippet as the label, so a
    card ending on `for name in os.listdir(home):` with the `shutil.rmtree` under it unseen is a person
    granting a command that was never on the screen."""
    caps = caps_for(100)
    code = ("run Python:\nimport os, shutil\nhome = os.path.expanduser('~')\n"
            "print('tidying', home)\nfor name in os.listdir(home):\n    pass\n"
            "shutil.rmtree(os.path.join(home, 'Documents'))\nprint('done')")
    drawn = Approval(code, "", "execute_code")
    out = painted(caps, approval_rows(caps, drawn, budget(caps)))
    assert "shutil.rmtree" not in out, "the fixture must overflow, or this proves nothing"
    assert "+3 more lines" in out, out

    drawn.show_why = True
    opened = painted(caps, approval_rows(caps, drawn, budget(caps)))
    assert "shutil.rmtree(os.path.join(home, 'Documents'))" in opened and "print('done')" in opened
    assert "more line" not in opened


def test_one_held_row_is_counted_in_the_singular():
    """A pin, not a fix. `+1 more lines` on a safety card is the plural
    that tells the reader the count is generated and nobody read it."""
    caps = caps_for(100)
    six = Approval("\n".join(f"line {n}" for n in range(6)), "", "python")
    out = painted(caps, approval_rows(caps, six, budget(caps)))
    assert "+1 more line " in out and "+1 more lines" not in out, out
    seven = Approval("\n".join(f"line {n}" for n in range(7)), "", "python")
    assert "+2 more lines" in painted(caps, approval_rows(caps, seven, budget(caps)))


def test_the_row_budget_counts_rows_and_not_lines():
    """It used to count lines, which is no budget at all: a 1,700-character ONE-liner was drawn whole
    down 37 rows while a six-line snippet lost its sixth in silence."""
    caps = caps_for(100)
    one = "curl -sSL https://example.com/x " + " ".join(f"opt{n}=value{n}" for n in range(120))
    rows = approval_rows(caps, Approval(one, "", "curl"), budget(caps))
    body = [r.plain for r in rows if "opt" in r.plain or "more line" in r.plain]
    assert len(body) == 6 and body[-1].endswith("? shows more of it"), body[-1]


def test_a_card_that_is_not_a_command_is_drawn_whole():
    """An empty family is the wire saying this card is not a command (the MCP install ask). Those four
    sentences are ours, bounded at the source, and unreadable with a line missing."""
    caps = caps_for(64)
    said = "\n".join(f"a sentence of ours, number {n}, long enough to wrap once" for n in range(8))
    out = painted(caps, approval_rows(caps, Approval(said, "", ""), budget(caps)))
    for n in range(8):
        assert f"number {n}," in out, n
    assert "more line" not in out


def test_the_notice_names_the_question_mark_only_when_it_would_show_more():
    """A short window leaves both budgets at CMD_ROWS. A gate that names a key which changes nothing is
    teaching the person to distrust the rail."""
    long = Approval("\n".join(f"line {n}" for n in range(40)), "", "python")
    tall = caps_for(100)
    tall.height = 40
    short = caps_for(100)
    short.height = 12
    assert "? shows more of it" in painted(tall, approval_rows(tall, long, budget(tall)))
    assert "? shows more of it" not in painted(short, approval_rows(short, long, budget(short)))
    assert "not drawn here" in painted(short, approval_rows(short, long, budget(short)))


def test_the_receipt_counts_the_lines_it_has_no_room_to_repeat():
    """One row, forever, in the transcript. `run Python:` on its own is a record of a person approving
    a colon."""
    caps = caps_for(100)
    one = painted(caps, [receipt_row(caps, CMD, "yes, go ahead")]).strip()
    assert one == f"? {CMD}  {caps.g['arrow']} yes, go ahead"
    many = painted(caps, [receipt_row(caps, "run Python:\nimport os\nos.remove(x)", "yes, go ahead")])
    assert "run Python:" in many and "(+2 lines)" in many


def test_the_ascii_ladder_emits_no_character_above_seven_bits():
    for width in WIDTHS:
        caps = caps_for(width, unicode=False)
        room = budget(caps)
        out = (painted(caps, approval_rows(caps, card(show_why=True, held=True), room))
               + painted(caps, confirm_rows(caps, Confirm("sandbox local → none", CONSEQUENCE), room)))
        assert [ch for ch in out if ord(ch) > 127] == []


def test_the_confirm_asks_the_consequence_and_offers_no_third_way_out():
    caps = caps_for(100)
    out = painted(caps, confirm_rows(caps, Confirm("sandbox local → none", CONSEQUENCE), budget(caps)))
    assert "are you sure" not in out.lower() and "force" not in out.lower()
    assert "turns the gate off" in out
    assert "y change it" in out and "n leave it as it is" in out


def test_the_blast_radius_measures_a_real_tree_and_a_real_file(tmp_path, monkeypatch):
    """The dialect follows the MACHINE here, against the suite-wide pin, because the paths in the
    command are the ones this test just built on it: parsed POSIX-style on Windows their separators
    are eaten, every target resolves to nothing, and the card measures an empty tree."""
    monkeypatch.setattr("kotoba.core.sandbox.local.shell_is_windows", lambda: os.name == "nt")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.bin").write_bytes(b"x" * 2048)
    (tmp_path / "b.bin").write_bytes(b"y" * 1024)
    tree, = blast_radius(f"rm -rf {tmp_path}")
    assert tree.endswith("3.0 KB, 2 files")
    one, = blast_radius(f"cat {tmp_path / 'b.bin'}")
    assert one.endswith("1.0 KB, 1 file")


def test_the_walk_is_bounded_so_a_huge_tree_can_never_hang_the_card(tmp_path, monkeypatch):
    monkeypatch.setattr("kotoba.core.sandbox.local.shell_is_windows", lambda: os.name == "nt")
    for n in range(60):
        leaf = tmp_path / f"d{n}"
        leaf.mkdir()
        (leaf / "f").write_bytes(b"z" * 16)
    started = time.monotonic()
    line, = blast_radius(f"rm -rf {tmp_path}", entries=8, seconds=5.0)
    assert time.monotonic() - started < 2.0
    assert "at least" in line or "too big to count" in line


def test_a_command_that_names_nothing_on_disk_gets_no_blast_lines():
    assert blast_radius("npm run build") == ()
    assert blast_radius('rm -rf "unterminated') == ()
    assert blast_radius(f"rm -rf {os.sep}nope{os.sep}missing") == ()


def test_a_read_only_setting_has_no_third_column_and_the_others_line_up():
    caps = caps_for(100)
    settable = setting_row(caps, "sandbox", "local", "she asks before running", "local | none | docker", 100)
    readonly = info_row(caps, "session", "3f2a91", "started 4m ago", 100)
    assert settable.plain.rstrip().endswith("local | none | docker")
    assert readonly.plain.index("3f2a91") == settable.plain.index("local")



def test_a_python_snippet_keeps_its_indentation_on_the_card():
    """Indentation in an approval card is safety information: `wrap` rejoins on single spaces, and a
    four-space body drawn on the `for` line's own column showed a flatter program than the one that
    would run (a real screenshot, 2 lines, both at column one)."""
    snippet = Approval("run Python:\nfor i in range(1, 6):\n    print(i)\n", "", "execute_code")
    rows = [r.plain for r in approval_rows(caps_for(100), snippet, 96)]
    assert any(r.endswith("for i in range(1, 6):") for r in rows)
    assert any(r.endswith("      print(i)") for r in rows), "the body keeps its four spaces"


def test_a_wrapped_indented_line_continues_at_its_own_depth():
    deep = Approval("run Python:\n    " + "x = 1 and 2 and 3 and 4 " * 6, "", "execute_code")
    rows = [r.plain for r in approval_rows(caps_for(80), deep, 76)]
    body = [r for r in rows if "x = 1" in r or r.rstrip().endswith(("and", "2", "3", "4"))]
    assert len(body) >= 2 and all("     " in r[:9] for r in body[:2])


def test_trailing_blank_lines_of_a_snippet_draw_no_dead_rows():
    """The blank row is not part of what runs; under the code it read as dead air above the keys."""
    snippet = Approval("run Python:\nprint(1)\n\n\n", "", "execute_code")
    rows = [r.plain for r in approval_rows(caps_for(100), snippet, 96)]
    at = next(i for i, r in enumerate(rows) if r.endswith("print(1)"))
    assert rows[at + 1].strip("█ ▀"), "the key rail follows the code directly"


def test_advisory_sentences_never_end_on_an_orphan_word():
    """`needs you` and `one` alone on the last row of the `?` panel is the typesetting accident a
    safety card cannot afford — the balancing wrap pulls words down until the stub carries weight."""
    asked = Approval("run Python:\nprint(1)", "", "execute_code",
                     can_always=True, can_always_exact=True, show_why=True)
    rows = [r.plain.strip("█▄▀ ").strip() for r in approval_rows(caps_for(100), asked, 96)]
    advisory = [r for r in rows if r and "print(1)" not in r and not r.startswith(("y ", "t "))]
    for row in advisory:
        words = row.split()
        assert not (len(words) == 1 and len(row) < 62 // 3 and row not in ("", "NEEDS YOU")), row
    flat = " ".join(rows)
    assert "plain read needs you" in flat and "not just this one" in flat
