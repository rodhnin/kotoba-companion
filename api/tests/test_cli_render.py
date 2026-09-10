"""What the terminal is allowed to receive: the degradation ladder, the colour discipline, the gap."""
from __future__ import annotations

import io
import re
import time

from rich.cells import cell_len

from kotoba.cli import state
from kotoba.cli.events_bridge import Step
from kotoba.cli.render import rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.header import COLW, header_rows, rule
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.text import Unwrapped, url_row
from kotoba.cli.render.theme import GLYPHS_ASCII, GLYPHS_UNICODE, build_console

SGR = re.compile(r"\x1b\[([0-9;]*)m")
REPLY = "Hello there.\n\n```python\ndef f():\n    return 1\n```\n\n- one\n  - nested"
STATS = [("MODEL", "gpt-5.4-mini · OpenAI", 2),
         ("WORK", "/home/jordan/.kotoba/files · 214 files", 3),
         ("VOICE", "local · expressive", 5),
         ("TOOLS", "31 ready · 3 skills", 4),
         ("MEM", "55 topics · 7.8k turns", 6),
         ("KEYS", "/help · @file · alt-enter", 1)]


def caps_for(*, color: str, unicode: bool, interactive: bool, width: int = 76) -> Caps:
    return Caps(color=color, background="dark", unicode=unicode, interactive=interactive,
                width=width, g=dict(GLYPHS_UNICODE if unicode else GLYPHS_ASCII))


_RICH_SYSTEM = {"truecolor": "truecolor", "256": "256", "16": "standard"}


def painted(caps: Caps, draw) -> str:
    buf = io.StringIO()
    # rich's vocabulary is standard|256|truecolor|windows, not the ramp's own names — handing it "16"
    # raised a KeyError from inside rich instead of failing an assertion.
    console = build_console(caps, file=buf, force_terminal=caps.interactive or None,
                            color_system=_RICH_SYSTEM.get(caps.color))
    screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
    draw(screen)
    return buf.getvalue()


def plate_of(caps: Caps):
    return Screen(caps, console=build_console(caps, file=io.StringIO()),
                  portrait=Portrait(caps, wanted=False)).plate()


def grid_of(caps: Caps, width: int, note: str = "") -> list:
    """The stat rows alone — the plate, the underline and the tagline lead, the clause trails."""
    labels = tuple(label for label, _, _ in STATS)
    return [row for row in header_rows(caps, plate_of(caps), width, STATS, note)
            if row.plain.startswith(labels)]


def full_reply(screen: Screen) -> None:
    screen.header(STATS, "local sandbox")
    for block in REPLY.split("\n\n"):
        screen.say(block)
    screen.footer(4.2, 2)


def test_a_piped_transcript_carries_no_escape_byte_at_all():
    out = painted(caps_for(color="none", unicode=True, interactive=False), full_reply)
    assert "\x1b" not in out


def test_nothing_printed_ever_ends_in_whitespace():
    for caps in (caps_for(color="none", unicode=True, interactive=False),
                 caps_for(color="truecolor", unicode=True, interactive=True)):
        out = painted(caps, full_reply)
        assert [line for line in out.split("\n") if line != line.rstrip()] == []


def test_ascii_mode_emits_no_character_above_seven_bits_including_richs_own_bullet():
    out = painted(caps_for(color="none", unicode=False, interactive=False), full_reply)
    assert [ch for ch in out if ord(ch) > 127] == []


def test_the_alternate_screen_is_never_entered_by_anything_that_draws():
    out = painted(caps_for(color="truecolor", unicode=True, interactive=True), full_reply)
    assert "\x1b[?1049h" not in out and "\x1b[3J" not in out


def test_the_measuring_console_cannot_paint_the_visible_one_at_another_depth():
    """The property, measured in BYTES rather than in labels.

    rich renders a Style's escape once and keeps it ON the object, and Styles are shared by content, so
    whichever console paints first fixes the depth for both. Asserted as `_off.color_system ==
    console.color_system` with the visible one pinned to truecolor, the old code passed: the conftest
    claims COLORTERM=truecolor, so the measuring console auto-detected truecolor as well and the two
    labels agreed while the bug sat there. Pinned to 256, the truecolor codes leak and are visible."""
    caps = caps_for(color="truecolor", unicode=True, interactive=True)
    buf = io.StringIO()
    console = build_console(caps, file=buf, force_terminal=True, color_system="256")
    screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
    screen._off.print(screen.plate(face=False))     # the measuring console paints FIRST
    console.print(screen.plate(face=False))

    painted = buf.getvalue()
    assert "48;5;" in painted, f"the visible console stopped painting at its own depth: {painted!r}"
    assert "48;2;" not in painted, (
        f"truecolor from the off-screen console reached the 256 one: {painted!r}")


def test_the_measuring_console_never_decides_the_visible_one_s_palette():
    """rich renders a Style's escape ONCE and keeps it on the object, and Styles are shared by content,
    so whichever console paints first fixes the colour depth for every console after it. `Screen` keeps
    a second, off-screen console to measure with; asked separately it answers off a redirected handle,
    which on Windows reads as no truecolor — it painted her nameplate at 256 and the real console then
    reused the cached codes. Two consoles, one palette."""
    caps = caps_for(color="truecolor", unicode=True, interactive=True)
    screen = Screen(caps, console=build_console(caps, file=io.StringIO(), force_terminal=True,
                                                color_system="truecolor"),
                    portrait=Portrait(caps, wanted=False))

    assert screen._off.color_system == screen.console.color_system == "truecolor"


def test_her_prose_is_never_coloured_and_her_nameplate_always_is():
    caps = caps_for(color="truecolor", unicode=True, interactive=True)
    out = painted(caps, lambda s: s.say("Hello there, this is a plain sentence."))
    plate, prose = [line for line in out.split("\n") if line.strip()][:2]
    assert "48;2;" in plate                       # the chip's own background: identity, so colour
    assert [code for code in SGR.findall(prose) if ";" in code or code.startswith("3")] == []


def test_her_voice_resuming_after_a_machine_row_gets_a_fresh_nameplate_on_a_clear_row():
    """CONFIRMED GOOD — a pin, not a fix. Announce, then the tool row at column 0, then a
    closing paragraph: committed bare at the gutter that paragraph read as a fragment of nobody's, so
    the resumed block re-anchors under its own plate. The turn has to read as ONE unit, and a second
    plate on a clear row is what makes it read that way rather than as two."""
    caps = caps_for(color="none", unicode=True, interactive=False)

    def turn(screen):
        screen.say("Right, I'll check the build.", last=True)
        screen.row(rows.tool_row(caps, Step("1", "shell", "npm run build", "ok"), 70, 1.9))
        screen.say("It passed — nothing to fix.", last=True)

    lines = painted(caps, turn).split("\n")
    plate = plate_of(caps).plain.strip()
    plates = [i for i, line in enumerate(lines) if line.strip().startswith(plate)]
    body = [line for line in lines if line.strip()]
    assert body[0].strip().startswith(plate) and body[-1].strip() == "It passed — nothing to fix."
    assert len(plates) == 2, body
    at = plates[1]
    assert not lines[at - 1].strip(), "the resumed plate lands on a clear row"
    assert "npm run build" in lines[at - 2], "and directly under the machine row it resumes from"


def test_a_terminal_that_cannot_draw_her_art_gets_no_portrait_and_still_gets_a_face():
    caps = caps_for(color="none", unicode=True, interactive=False)
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=True))
    assert screen.portrait.mode == "none"
    assert screen.gutter == 3
    assert screen.plate().plain.endswith("( ･ω･ )")


def test_a_narrow_terminal_refuses_the_portrait_rather_than_mushing_it():
    caps = caps_for(color="truecolor", unicode=True, interactive=True, width=48)
    assert Portrait(caps, wanted=True).mode == "none"


def test_the_chip_column_does_not_move_when_a_tool_lands():
    caps = caps_for(color="none", unicode=True, interactive=False)
    running = rows.tool_row(caps, Step("1", "shell", "pytest -q", "running"), 70, 1.6).plain
    landed = rows.tool_row(caps, Step("1", "shell", "pytest -q", "ok"), 70, 1.9).plain
    assert running.index("pytest") == landed.index("pytest")
    assert running.rstrip().endswith("1.6s") and landed.rstrip().endswith("1.9s")


def test_a_deferred_step_reads_as_waiting_on_you_and_never_as_a_failure():
    caps = caps_for(color="none", unicode=True, interactive=False)
    row = rows.tool_row(caps, Step("1", "shell", "rm -rf build", "pending"), 70).plain
    assert row.rstrip().endswith("needs you")
    assert not row.startswith(caps.g["fail"])


def test_an_interrupted_step_is_not_drawn_as_a_failure_either():
    caps = caps_for(color="none", unicode=True, interactive=False)
    row = rows.tool_row(caps, Step("1", "code", "measure.py", "interrupted"), 70).plain
    assert row.startswith(caps.g["cut"])


def test_her_block_is_followed_by_exactly_one_blank_row_however_late_it_is_spent():
    caps = caps_for(color="none", unicode=True, interactive=False)
    out = painted(caps, lambda s: (s.say("One."), s.say("Two."), s.say("Three."), s.gap()))
    assert "\n\n\n" not in out
    assert out.endswith("   One.\n\n   Two.\n\n   Three.\n\n")


def test_a_long_action_is_shortened_rather_than_allowed_to_wrap():
    caps = caps_for(color="none", unicode=True, interactive=False)
    row = rows.tool_row(caps, Step("1", "web", "x" * 400, "running"), 70, 2.0).plain
    assert len(row) <= 70


def test_the_live_region_is_bounded_by_the_window_so_it_can_always_be_erased_again():
    """A region taller than the window cannot be erased: the rows it wants back have already scrolled
    away. Her block is committed paragraph by paragraph, so what the region holds is only what is still
    arriving — and `reserve` gives up a row for every one that lands."""
    caps = caps_for(color="truecolor", unicode=True, interactive=True)
    caps.height = 24
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf, force_terminal=True),
                    portrait=Portrait(caps, wanted=False))
    screen.reserve = caps.height
    for _ in range(40):
        screen.say("a paragraph of hers, committed the moment its blank line arrived")
    assert 1 <= screen.reserve <= caps.height


def test_the_rule_spans_the_whole_terminal_because_chrome_that_stops_halfway_reads_as_a_bug():
    assert len(rule(caps_for(color="none", unicode=True, interactive=False, width=100)).plain) == 100


def test_every_column_of_the_header_starts_on_the_same_cell_however_long_the_facts_are():
    for width, cols in ((64, 2), (80, 2), (126, 3), (200, 3)):
        caps = caps_for(color="none", unicode=True, interactive=False, width=width)
        pitch = min(width // cols, COLW)
        for row in grid_of(caps, width):
            for label, _, _ in STATS:
                if label in row.plain:
                    assert row.plain.index(label) % pitch == 0


def test_the_grid_reads_down_a_column_and_not_across_a_row():
    caps = caps_for(color="none", unicode=True, interactive=False, width=126)
    grid = [row.plain for row in grid_of(caps, 126)]
    assert [row.split()[0] for row in grid] == ["MODEL", "WORK"]
    assert grid[0].split()[0] == "MODEL" and "VOICE" in grid[0] and "MEM" in grid[0]


def test_no_row_of_the_header_ever_wraps_at_any_width_it_was_measured_for():
    for width in (40, 46, 64, 80, 126, 200):
        for caps in (caps_for(color="none", unicode=False, interactive=False, width=width),
                     caps_for(color="truecolor", unicode=True, interactive=True, width=width)):
            # Printed, not measured: the ASCII fold happens on the way out, and `…` leaves as `..`.
            buf = io.StringIO()
            console = build_console(caps, file=buf, force_terminal=caps.interactive or None)
            console.width = width
            screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
            screen.header(STATS, "local sandbox")
            for line in SGR.sub("", buf.getvalue()).split("\n"):
                assert cell_len(line) <= width


def test_a_transcript_nobody_is_watching_never_claims_to_be_live():
    piped = caps_for(color="none", unicode=True, interactive=False, width=100)
    assert "LIVE" not in "\n".join(r.plain for r in header_rows(piped, plate_of(piped), 100, STATS))
    live = caps_for(color="none", unicode=True, interactive=True, width=100)
    assert "LIVE" in "\n".join(r.plain for r in header_rows(live, plate_of(live), 100, STATS))


def test_a_narrow_terminal_drops_the_decorative_facts_and_keeps_the_ones_that_teach():
    for width, kept in ((60, ["MODEL", "WORK", "TOOLS", "KEYS"]),
                        (40, ["MODEL", "WORK", "KEYS"])):
        caps = caps_for(color="none", unicode=True, interactive=False, width=width)
        assert [row.plain.split()[0] for row in grid_of(caps, width)] == kept


WIDE = "ワイドな日本語のゴールがここにある" * 4


class FakeCard:
    def __init__(self, label: str, family: str | None = None) -> None:
        self.label, self.family = label, family


def a_tool(verb: str, arg: str, kind: str, detail: str = "", elapsed: float = 2.0) -> state.Tool:
    now = time.monotonic()
    return state.Tool(verb, arg, started=now - elapsed, state=kind, detail=detail, stopped=now)


def a_helper(role: str, goal: str, kind: str, steps: int = 2) -> state.Helper:
    now = time.monotonic()
    return state.Helper("w", role, goal, state=kind, steps=[("web_search 'x'", True)] * steps,
                        started=now - 3.5, stopped=now if kind != "running" else 0.0)


def every_row(caps: Caps, width: int, wide: bool = True) -> list:
    """One of every row this module can draw, at its most awkward: overlong Latin, wide CJK, each
    state, each chip, and a helper roster with a peek open.

    `wide=False` swaps the CJK content for ASCII, which is how the fold ladder asks its question of the
    CHROME alone: her own characters are not ours to replace."""
    wide_text = WIDE if wide else "a wide goal written in plain ASCII " * 2
    spin = rows.Spin(caps, acc=3.0)
    out = []
    for kind in ("running", "pending", "ok", "failed", "refused", "interrupted", "unknown"):
        for action, detail in (("pytest -q", "3 passed"), ("x" * 400, "y" * 400),
                               (wide_text, wide_text)):
            out.append(rows.tool_row(caps, Step("1", "shell", action, kind, detail), width, 1.6))
            out.append(rows.tool_text(caps, a_tool("web", action, kind, detail), width, spin=spin))
    for kind in ("running", "ok", "failed", "interrupted"):
        work = state.Work(wide_text if kind == "ok" else "x" * 300, n=3, state=kind,
                          t0=time.monotonic() - 90, stopped=time.monotonic(), gift_ns=[1, 2, 3])
        out.append(rows.work_row(caps, work, width, opening=kind == "running"))
    for chip in ("ready", "saved", "link", "sent", "due"):
        for target, note in (("a.md", "a short note"), ("https://x.example/" + "p" * 300, "n" * 300),
                             (wide_text, wide_text)):
            out += rows.gift_rows(caps, state.Gift(chip, target, note), 7, width)
    line_up = [a_helper("research", wide_text, "running"), a_helper("web", "x" * 300, "queued"),
               a_helper("code", "write the probe", "ok"), a_helper("web", wide_text, "failed"),
               a_helper("research", "x" * 300, "interrupted")]
    out += rows.roster_rows(caps, line_up, width, spin=spin, peeked=1)
    out += rows.roster_rows(caps, line_up, width, spin=spin, folded=True)
    for card in (FakeCard("rm -rf " + "x" * 300, "rm"),
                 FakeCard("run Python:\n" + wide_text, "execute_code"),
                 FakeCard("Install the X MCP server (npx y) and connect it?", "")):
        out.append(rows.approval_row(caps, card, width))
    return out


def test_a_landed_row_says_what_came_back_and_not_merely_that_it_did():
    caps = caps_for(color="none", unicode=True, interactive=False)
    step = Step("1", "web", 'web_search "live2d lipsync"', "ok", "7 results")
    assert "· 7 results" in rows.tool_row(caps, step, 76, 1.4).plain


def test_a_failed_row_shows_its_error_and_paints_that_part_alone():
    caps = caps_for(color="truecolor", unicode=True, interactive=True)
    row = rows.tool_row(caps, Step("1", "shell", "python measure.py", "failed", "exit 1"), 76, 2.1)
    assert "· exit 1" in row.plain
    assert {row.plain[s.start:s.end]: s.style for s in row.spans}[" · exit 1"] == "live"


def test_an_approval_wears_the_chip_of_the_tool_that_asked_and_never_the_generic_one():
    # command_family() returns the FIRST TOKEN, so no card ever carries family == "command".
    caps = caps_for(color="none", unicode=True, interactive=False)
    for card, label in ((FakeCard("rm -rf ~/.kotoba/tmp", "rm"), "BASH"),
                        (FakeCard("npm install", "npm"), "BASH"),
                        (FakeCard("run Python:\nimport os", "execute_code"), "CODE"),
                        (FakeCard("Install the “notion” MCP server (npx -y x) and connect it?", ""),
                         "MCP")):
        assert label in rows.approval_row(caps, card, 76).plain


def test_an_approval_never_becomes_two_rows_because_the_snippet_had_newlines_in_it():
    caps = caps_for(color="none", unicode=True, interactive=False)
    card = FakeCard("run Python:\nimport shutil\nshutil.rmtree('/tmp/x')", "execute_code")
    assert "\n" not in rows.approval_row(caps, card, 76).plain


def test_no_row_this_module_draws_ever_exceeds_the_terminal_it_was_measured_for():
    for unicode_ok in (True, False):
        for color in ("truecolor", "none"):
            for width in (40, 64, 100, 200):
                caps = caps_for(color=color, unicode=unicode_ok, interactive=True, width=width)
                for row in every_row(caps, width):
                    # A URL takes its own unwrapped row on purpose: the terminal breaks it, not us.
                    assert isinstance(row, Unwrapped) or cell_len(row.plain) <= width


def test_the_ascii_ladder_reaches_every_row_and_not_only_her_prose():
    """--ascii folds the CHROME and leaves the content alone.

    Every glyph this module draws is in the table, so a row built out of ASCII content is 7-bit all
    through — rich's own bullet included. What she WROTE is not chrome: the catch-all that used to sit
    under the table replaced every remaining character with `?`, and a Spanish greeting reading
    `¡Qué gusto verte por aquí!` came out as `?Qu? gusto verte por aqu?!`. A terminal that cannot ENCODE
    those bytes is answered at the stream instead (`theme._degrade`)."""
    caps = caps_for(color="none", unicode=False, interactive=False)
    plain = "".join(row.plain for row in every_row(caps, 100, wide=False))
    assert [ch for ch in plain if ord(ch) > 127] == []
    drawn = "".join(row.plain for row in every_row(caps, 100))
    assert WIDE[:8] in drawn, "her goal, her argument and her file name reach the terminal as she wrote them"


def test_the_pulse_counts_seconds_so_the_same_ramp_reads_alike_at_any_repaint_rate():
    caps = caps_for(color="none", unicode=True, interactive=True)
    slow, fast = rows.Spin(caps), rows.Spin(caps)
    for spin, ticks in ((slow, 1), (fast, 6)):
        for _ in range(ticks):
            spin.at = time.monotonic() - 0.24 / ticks
            spin.tick()
    assert abs(slow.acc - fast.acc) < 0.05


def test_her_mood_multiplies_the_pulse_rather_than_leaving_it_flat():
    caps = caps_for(color="none", unicode=True, interactive=True)
    paces = []
    for mood in ("sleepy", "neutral", "surprised"):
        spin = rows.Spin(caps)
        spin.at = time.monotonic() - 0.2
        spin.tick(mood)
        paces.append(spin.acc)
    assert paces == sorted(paces) and paces[0] < paces[-1]


def test_a_calm_terminal_gets_one_frame_forever_and_a_committed_row_never_gets_one_at_all():
    caps = caps_for(color="none", unicode=True, interactive=True)
    caps.reduced_motion = True
    spin = rows.Spin(caps)
    assert len({spin.spinner(2.0) for spin.acc in (0.0, 3.0, 7.0, 11.0)}) == 1
    committed = rows.tool_text(caps, a_tool("shell", "pytest -q", "running"), 76, still=True).plain
    assert committed.startswith(caps.g["give"])


def test_a_gift_row_never_cuts_a_url_and_never_breaks_one_across_two_rows():
    caps = caps_for(color="none", unicode=True, interactive=False)
    url = "https://nextjs.org/docs/app/api-reference/next-config-js/rewrites?a=1&b=2#anchor"
    drawn = rows.gift_rows(caps, state.Gift("link", url, "the rewrites page"), 2, 60)
    assert [row.plain for row in drawn if isinstance(row, Unwrapped)] == [url]
    assert not any("…" in row.plain for row in drawn if isinstance(row, Unwrapped))


def test_the_line_up_keeps_its_height_and_its_order_while_a_helper_finishes():
    caps = caps_for(color="none", unicode=True, interactive=False)
    line_up = [a_helper("research", "read both pages", "running"),
               a_helper("web", "find the issue", "running"),
               a_helper("code", "write the probe", "queued")]
    before = [row.plain for row in rows.roster_rows(caps, line_up, 76)]
    line_up[0].state, line_up[0].stopped = "ok", time.monotonic()
    after = [row.plain for row in rows.roster_rows(caps, line_up, 76)]
    assert len(before) == len(after) == 3
    assert after[0].startswith(caps.g["ok"]) and "read both pages" in after[0]
    # The rank keeps its row: a helper that greys in place is the whole point of a fixed line-up.
    assert [row[2:9] for row in before] == [row[2:9] for row in after]


def test_a_short_window_folds_the_line_up_instead_of_eating_the_screen_with_it():
    caps = caps_for(color="none", unicode=True, interactive=False)
    caps.height = 12
    line_up = [a_helper("research", "read both pages", "running"), a_helper("web", "x", "ok")]
    folded = rows.roster_rows(caps, line_up, 76)
    assert len(folded) == 1 and "1 on stage" in folded[0].plain


def test_every_verb_she_narrates_belongs_to_a_tool_that_would_otherwise_say_nothing():
    from kotoba.tools.registry import registry

    known = registry()
    assert [name for name in rows.PEEK if name not in known] == []
    assert [name for name in rows.PEEK
            if getattr(known[name], "risk", None) in ("write", "exec", "network")] == []


def test_the_chip_table_covers_every_kind_a_row_can_ask_it_for():
    caps = caps_for(color="none", unicode=True, interactive=False)
    for kind in ("work", "saved", "ready", "link", "sent", "due"):
        assert rows.chip(caps, kind).plain.strip() != "TOOL"


def _held_screen(irows: int = 5) -> tuple[Screen, io.StringIO]:
    """A Screen whose portrait claims the inline tier, so `say` stages instead of printing."""
    caps = caps_for(color="truecolor", unicode=True, interactive=True, width=120)
    buf = io.StringIO()
    box = Portrait(caps, wanted=False)
    box.mode, box.cols, box.rows, box.icols, box.irows = "sixel", 12, 5, 12, irows
    screen = Screen(caps, console=build_console(caps, file=buf, force_terminal=True), portrait=box)
    return screen, buf


def test_a_one_line_opener_waits_for_the_paragraph_that_fills_her_art():
    """A four-row face over a one-line opener left two dead rows mid-utterance, so one reply read as
    three messages. Held, the next paragraph lands in them."""
    screen, buf = _held_screen(irows=4)
    screen.say("Hola.")
    assert buf.getvalue() == "", "a one-line opener must not commit on its own"
    assert screen.held == "Hola."
    screen.say("Y aqui va el resto, que ya llena las filas que su cara ocupa a la izquierda.")
    out = buf.getvalue()
    assert "Hola." in out and "el resto" in out, out
    assert screen.held == ""


def test_nothing_she_said_is_lost_when_the_turn_ends_mid_hold():
    screen, buf = _held_screen()
    screen.say("Solo esto.")
    assert buf.getvalue() == ""
    screen.end_turn()
    assert "Solo esto." in buf.getvalue()
    assert screen.held == ""


def test_without_the_inline_tier_a_block_is_printed_at_once():
    """Half-blocks cost nine rows for one face, so they never earn a per-reply portrait — and with no
    art beside her there is nothing to fill and nothing to wait for."""
    caps = caps_for(color="truecolor", unicode=True, interactive=True, width=120)
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf, force_terminal=True),
                    portrait=Portrait(caps, wanted=False))
    screen.say("Hola.")
    assert "Hola." in buf.getvalue()
    assert screen.held == ""


def _reply(screen: Screen, emotion: str, block: str) -> str:
    """One turn the way the backend runs it: the prompt's bar has pinned her neutral, the tag she opens
    with names the mood before a single character reaches the screen (`_face`), and the live
    region paints THAT face the first frame she speaks. Returns the payload the region wrote."""
    screen.begin_turn()
    screen.face.set("neutral", instant=True)
    screen.face.set(emotion, instant=True)
    first, rows_tall, payload = screen.face_frame(0)
    screen.erase_span = (rows_tall + 2, frozenset(range(first, first + rows_tall)), screen.gutter)
    screen.say(block, last=True)
    screen.flush_held()
    screen.end_turn()
    return payload


def test_every_reply_gets_the_face_she_settled_on_and_only_one_of_it():
    """Four replies, four faces, one sixel each. A mood that only exists once the reply has streamed
    freezes the art on the mood she STARTED in — the kaomoji moves and the portrait never does — and
    catching up afterwards costs a second sixel and the full-width erase that takes the first one off,
    which is the flash. Chosen from her tag before the first frame, neither happens."""
    screen, buf = _held_screen(irows=4)
    screen.portrait._sixel = lambda emotion, scale: (f"<SIXEL {emotion}>", 12, 4)
    drawn = [_reply(screen, emotion, "Una respuesta suya, con dos filas para llenar la cara de al lado.")
             for emotion in ("happy", "affectionate", "excited", "sad")]
    assert drawn == [f"<SIXEL {e}>" for e in ("happy", "affectionate", "excited", "sad")], drawn
    assert "<SIXEL" not in buf.getvalue(), "the committed block keeps the one the region already painted"


def test_a_mood_she_never_left_still_costs_exactly_one_sixel():
    """The live paint is the whole cost when it is already her face: the committed block walks over it
    and writes none of its own. A second one is what a mood arriving late buys, and only then."""
    screen, buf = _held_screen(irows=4)
    screen.portrait._sixel = lambda emotion, scale: (f"<SIXEL {emotion}>", 12, 4)
    screen.begin_turn()
    # Her mood lands INSIDE the turn — the tag she opens with, or a tool's focus. `begin_turn` clears
    # what the last reply left, so setting it before that boundary would be setting it for nobody.
    screen.face.set("happy", instant=True)
    first, rows_tall, payload = screen.face_frame(0)
    screen.erase_span = (rows_tall + 2, frozenset(range(first, first + rows_tall)), screen.gutter)
    screen.say("Dos filas de texto suyo, las que su cara ocupa a la izquierda del bloque.", last=True)
    screen.flush_held()
    assert payload == "<SIXEL happy>"
    assert "<SIXEL" not in buf.getvalue()


def test_a_url_too_wide_for_her_measure_takes_a_row_of_its_own_and_keeps_its_label():
    """RE-RECORDED: this URL is 101 cells against a 90-column window, so it no longer carries her
    three-cell gutter — the indent is the renderer's and a URL with no cells to spare does not pay for
    it (`render/text.url_row`). What the test was always about is unchanged: whole, on a row of its
    own, with the label left in the sentence."""
    url = "https://www.forbes.com/sites/maryroeloffs/2026/06/22/x-down-outage-reports-surge?utm_source=openai"
    caps = caps_for(color="none", unicode=True, interactive=False, width=90)
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    screen.say(f"Se cayo el lunes ([Forbes]({url})). Duro dos horas.", last=True)
    screen.flush_held()
    out = buf.getvalue()
    assert "((" not in out
    assert "Se cayo el lunes (Forbes). Duro dos horas." in out
    assert f"\n{url}\n" in out, "the URL must land whole, on a row of its own"
    assert cell_len(url) > caps.width, "and this one has no room for her gutter on top"


def test_a_url_that_fits_stays_in_the_sentence_and_one_inside_a_fence_is_code():
    from kotoba.cli.render.markdown import lift_urls

    assert lift_urls("mira https://x.example/a ya", 40) == ("mira https://x.example/a ya", [])
    fenced = "```\ncurl https://x.example/" + "p" * 90 + "\n```"
    assert lift_urls(fenced, 40) == (fenced, [])


def test_her_prose_is_scrubbed_before_the_terminal_draws_it():
    """Rich strips a carriage return and nothing else, so an erase-line escape or a bidi override in her
    reply reached the terminal and rewrote what the user believed they had read. Cards were already
    scrubbed; the streamed prose was not. Invisibles are written as escapes on purpose."""
    from kotoba.cli.render.markdown import prose

    class _Caps:
        background = "dark"

        def t(self, s):
            return s

    out = prose("The file is ‮gnp.evil‬ and \x1b[2K\rhidden.", _Caps()).markup
    assert "\x1b" not in out and "\r" not in out
    assert "‮" not in out and "‬" not in out
    assert "gnp.evil" in out and "hidden." in out       # every readable byte survives


def test_scrubbing_prose_keeps_the_newlines_the_splitter_needs():
    from kotoba.cli.render.markdown import prose

    class _Caps:
        background = "dark"

        def t(self, s):
            return s

    assert prose("one\n\ntwo", _Caps()).markup.count("\n") == 2


EVIL = "SAFE\x1b[2K\rEVIL‮DERUCSBO"


def drawn(renderables, width: int = 80) -> str:
    caps = caps_for(color="none", unicode=True, interactive=False, width=width)
    buf = io.StringIO()
    console = build_console(caps, file=buf)
    for r in (renderables if isinstance(renderables, list) else [renderables]):
        console.print(r)
    return buf.getvalue()


def test_no_row_carries_an_escape_or_a_bidi_override_to_the_terminal():
    """The prose scrub was one of the surfaces, not the surface. A gift's target, a tool's argument,
    an approval's label, a plan step and the band under the box are all strings somebody else wrote,
    and rich hands an ESC straight to the file descriptor (it strips 7, 8, 11, 12 and 13, and nothing
    else). Every one of these leaked before the rows were built out of `Safe`."""
    caps = caps_for(color="none", unicode=True, interactive=False, width=80)

    class Gift:
        kind, target, note, tail = "file", EVIL, EVIL, ""

    class Step:
        kind, action, result, state, note = "shell", EVIL, EVIL, "ok", ""

    class Card:
        family, label = "command", EVIL

    class Work:
        state, n, goal, elapsed, gift_ns = "ok", 1, EVIL, 1.0, []

    class Helper:
        goal, role, state, steps, elapsed = EVIL, EVIL, "running", [("a", True)], 1.0

    plan = {"status": "open", "title": EVIL,
            "tasks": [{"order": 1, "status": "active", "text": EVIL}]}

    for name, painted in (
        ("gift", drawn(rows.gift_rows(caps, Gift(), 1, 80))),
        ("tool", drawn(rows.tool_row(caps, Step(), 80, 1.0))),
        ("approval", drawn(rows.approval_row(caps, Card(), 80))),
        ("work", drawn(rows.work_row(caps, Work(), 80, opening=True))),
        ("helper", drawn(rows.helper_text(caps, Helper(), 1, 80))),
        ("plan", drawn(rows.plan_rows(caps, plan, 80))),
        ("url", drawn(Unwrapped(EVIL))),
    ):
        assert "\x1b" not in painted, name
        assert "‮" not in painted, name
        assert "EVIL" in painted and "SAFE" in painted, f"{name} dropped a readable byte"


def test_a_scrubbed_row_still_fits_the_width_it_was_measured_for():
    """The scrub has to run BEFORE the measure. A C0 byte is zero cells and the space it becomes is
    one, so a menu row scrubbed after its fit was drawn four columns past the border around it."""
    from prompt_toolkit.completion import Completion

    from kotoba.cli.render import footer

    caps = caps_for(color="none", unicode=True, interactive=False, width=80)
    painted = drawn(footer.menu_rows(caps, [Completion("", 0, display=EVIL, display_meta=EVIL)], 0, 3))
    assert "\x1b" not in painted and "‮" not in painted
    assert max(len(line) for line in painted.rstrip("\n").split("\n")) <= 80


def test_the_machine_line_quotes_a_target_back_without_quoting_its_escapes():
    """`screen.chrome` is half quotation: `/open` prints the target of a gift and its refusals print
    the path it would not open, and both of those are strings the model wrote."""
    caps = caps_for(color="none", unicode=True, interactive=False, width=80)
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    screen.chrome(EVIL)
    screen.commit_user(EVIL)
    out = buf.getvalue()
    assert "\x1b" not in out and "‮" not in out
    assert "EVIL" in out


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[78]")


def curtain_rows(width: int, **over) -> list[str]:
    """Every frame of the boot curtain, as the terminal receives it. The region redraws in place, so
    the frames are split on the carriage return that separates them."""
    kw = dict(color="truecolor", unicode=True, interactive=True, width=width)
    calm = over.pop("reduced_motion", False)
    kw.update(over)
    caps = caps_for(**kw)
    caps.reduced_motion = calm
    caps.height = 30
    buf = io.StringIO()
    console = build_console(caps, file=buf, force_terminal=True)
    console.width, console.height = caps.width, caps.height
    screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
    slept, time.sleep = time.sleep, lambda s: None
    try:
        screen.curtain()
    finally:
        time.sleep = slept
    text = ANSI.sub("", buf.getvalue()).replace("\r", "\n")
    return [line for line in text.split("\n") if line.strip()]


def test_the_curtain_opens_from_the_middle_of_the_window_and_reaches_both_edges():
    """It was a fixed fifty-four cells pinned to the gutter — the whole width of the window it was
    designed on, and a stub shoved into the left corner of a wide one. The rule is centred on the
    window and spans it, and the dots are centred under the rule and not under a constant."""
    for width in (60, 100, 160, 200):
        drawn = curtain_rows(width)
        bars = [line for line in drawn if "▁" in line or "▄" in line]
        dots = [line for line in drawn if "●" in line or "○" in line]
        assert len(bars) >= 12 and dots, width
        for bar in bars:
            lead = len(bar) - len(bar.lstrip(" "))
            ink = cell_len(bar.strip())
            assert abs((lead + ink / 2) - width / 2) <= 0.5, (width, lead, ink)
        assert cell_len(bars[-1].strip()) == width - 2, "the open rule spans the window"
        assert cell_len(bars[0].strip()) < cell_len(bars[-1].strip()), "it opens, it does not appear"
        last = dots[-1]
        lead = len(last) - len(last.lstrip(" "))
        assert abs((lead + cell_len(last.strip()) / 2) - width / 2) <= 1.0, (width, lead)


def test_no_frame_of_the_curtain_is_wider_than_the_terminal_it_boots_in():
    for width in range(14, 201):
        over = [line for line in curtain_rows(width) if cell_len(line) > width]
        assert over == [], (width, over)


def test_a_window_too_narrow_to_hold_a_curtain_gets_no_curtain_and_no_crash():
    """Two end-caps and three dots do not fit under fourteen columns, and the boot is not the place to
    find that out."""
    assert curtain_rows(13) == []
    assert curtain_rows(6) == []


def test_a_terminal_that_cannot_draw_blocks_gets_no_curtain_at_all():
    """The four glyphs are written into `curtain` rather than taken from `caps.g`, and `▁` has no
    entry in the fold, so a terminal with no unicode was sent a screenful of them anyway — measured
    through a pty, 2,745 bytes above 0x7f at boot under `LC_ALL=C`, while the header and the bar
    around it folded correctly. `--plain` and `--calm` already leave with nothing; so does this."""
    assert curtain_rows(96, unicode=False) == []
    assert curtain_rows(96, color="none") == []
    assert curtain_rows(96, reduced_motion=True) == []
    assert curtain_rows(96, interactive=False) == []
    # The refusals above are only worth something if what they refuse is the thing that broke a
    # terminal with no unicode. Reading the ascii curtain proves nothing: there is no ascii curtain.
    drawn = "".join(curtain_rows(96))
    assert drawn and any(ord(c) > 127 for c in drawn)


SPANISH = "¡Holaaa, Jordan. Qué gusto verte por aquí!\n\n¿Qué te apetece hacer hoy?"


def test_ascii_folds_the_chrome_and_leaves_her_spanish_alone():
    """`kotoba --ascii` printed her Spanish greeting as `?Holaaa, Jordan. Qu? gusto verte por aqu?`. The
    flag is for a terminal that cannot DRAW our glyphs; her sentences are not our glyphs. (The fixture
    is a real greeting of hers: accents, inverted punctuation and all.)"""
    caps = caps_for(color="none", unicode=False, interactive=False, width=76)
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    screen.begin_turn()
    for i, block in enumerate(SPANISH.split("\n\n")):
        screen.say(block, last=i == 1)
    screen.flush_held()
    out = buf.getvalue()
    assert "¡Holaaa, Jordan. Qué gusto verte por aquí!" in out
    assert "¿Qué te apetece hacer hoy?" in out
    assert "言" not in out and "K KOTOBA" in out, "the sigil is chrome and still folds"


def test_the_sigil_kept_for_an_ambiguous_width_terminal_reaches_the_wire():
    """`caps.detect` put 言 back into `g` after choosing the ASCII table and a paragraph explained why.
    It never arrived: `Trim` folds every printed row, so the plate object said `言 KOTOBA` and the
    terminal got `K KOTOBA`. The override was unobservable — the paragraph described nothing."""
    caps = caps_for(color="none", unicode=False, interactive=False, width=76)
    caps.encodes_unicode = True     # UTF-8 stream, ω two cells wide — NOT `--ascii`
    caps.g["sigil"] = "言"
    out = painted(caps, lambda screen: screen.header([], live=False))
    assert "言 KOTOBA" in out, "the sigil the probe kept never reached the terminal"
    assert caps.t("→ una decisión") == "-> una decisión", "the ambiguous glyphs still fold"


def test_a_stream_that_cannot_encode_her_accents_substitutes_instead_of_raising():
    """`LC_ALL=C` with Python's UTF-8 mode off is an ascii stdout, and rich raises on the first `é`.
    That is the stream's question, not `--ascii`'s, and it is answered once at the Console."""
    caps = caps_for(color="none", unicode=False, interactive=False, width=76)
    raw = io.BytesIO()
    ascii_stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict", newline="")
    console = build_console(caps, file=ascii_stream)
    console.print("acento: é í ¿qué?")
    ascii_stream.flush()
    assert raw.getvalue() == b"acento: ? ? ?qu??\n"
    assert ascii_stream.errors == "replace", "decided by what the stream can carry, never by --ascii"


def test_a_browser_hop_is_never_reported_as_installing_a_tool():
    """`_step_kind` gives `mcp` to mcp_find, to mcp_install and to every `browser__` call alike, so the
    kind alone announced that she had wired herself a new tool — for 3m 24s of ordinary browsing."""
    assert rows.step_verb("mcp", "browser__browser_navigate") == "looking around the web…"
    assert rows.step_verb("mcp", "connect MCP: notion") == "wiring herself a new tool…"
    assert rows.step_verb("mcp", "search MCP registry: linear") == "looking for a tool…"
    assert rows.step_verb("mcp", "notion__search") == "using one of her tools…"
    assert rows.step_verb("web", "web_search x") == "looking that up…"


def test_the_jobs_verb_is_its_current_step_and_not_the_last_one_it_ever_had():
    job = state.Work("measure the pipeline", 1)
    assert rows.work_verb(job) == "getting going…"
    job.tools.append(state.Tool("web", "web_search 'first byte'"))
    assert rows.work_verb(job) == "looking that up…"
    job.tools[-1].state = "ok"
    assert rows.work_verb(job) == "on it…", "a search that landed is not one she is still running"
    job.helpers.append(state.Helper("h1", "research", "read both pages", state="running"))
    assert rows.work_verb(job) == "sending someone else to look…"


def test_a_url_keeps_her_gutter_only_while_the_window_can_afford_it():
    """The ElevenLabs key page is 43 cells and the wizard's gutter is three, so a forty-column window
    got a 46-cell row: the renderer's own cells added to a string already too long to be read off that
    screen. Column 0 is the most room there is to give it, and the wrap that is left is the terminal's
    own fold of one buffer line — never a newline of ours, which is what would end the selectable run."""
    url = "https://elevenlabs.io/app/settings/api-keys"
    assert cell_len(url) == 43
    assert url_row(3, url, 96).plain == "   " + url, "a wide window keeps her column"
    assert url_row(3, url, 46).plain == "   " + url, "exactly enough room still keeps it"
    assert url_row(3, url, 45).plain == url, "one cell short and the indent is what gives way"
    assert url_row(3, url, 40).plain == url
    assert "\n" not in url_row(3, url, 40).plain, "a newline is what would break the run"
