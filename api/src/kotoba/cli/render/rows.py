"""The machine talking: one line per action she takes, at column 0, ending in a clock.

A row that is PRINTED wears the still `▸`: scrollback never repaints, so a committed pulse is an
animation that has stopped. An ending this build cannot place falls back to `?`, never the running `▸`:
it must not read as still going, or as one that worked. The pulse's phase accumulates in SECONDS, never
renders, or the ramp rides the repaint throttle (12/s at a tool, 4/s over ssh); `ENERGY` scales it by
mood, 0.35x asleep to 1.6x startled. Read-only tools emit no step frame — that is what `PEEK` is for,
since printing nothing while five of them run is indistinguishable from a hang. Every row is a `Safe`,
scrubbed BEFORE it is measured: a C0 byte costs zero cells and the space it becomes costs one.
"""
from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from rich.cells import cell_len
from rich.text import Text

from kotoba.cli.render import paths
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.kaomoji import FACES_ASCII, FACES_UNICODE
from kotoba.cli.render.safe import Safe
from kotoba.cli.render.text import Unwrapped, duration, fit, wrap
from kotoba.core.text_security import scrub

CHIPS = {
    "shell": ("BASH", "chip.mint"), "code": ("CODE", "chip.grape"),
    "web": ("WEB", "chip.live"), "file": ("FILE", "chip.ink"),
    "mcp": ("MCP", "chip.sun"), "skill": ("SKILL", "chip.grape"),
    "memory": ("MEM", "chip.grape"), "tool": ("TOOL", "chip.ink"),
    "plan": ("PLAN", "chip.grape"), "work": ("WORK", "chip.grape"),
    "saved": ("SAVED", "chip.coral"), "ready": ("READY", "chip.sun"),
    "link": ("LINK", "chip.grape"), "sent": ("SENT", "chip.coral"),
    "due": ("DUE", "chip.sun"),
}
STATE_GLYPH = {"ok": ("ok", "mint"), "failed": ("fail", "live"),
               "refused": ("rule", "chrome"), "interrupted": ("cut", "chrome"),
               "unknown": ("ask", "chrome"), "pending": ("give", "sun"),
               "running": ("give", "grape"), "queued": ("bullet", "chrome")}
TASK_GLYPH = {"done": ("ok", "mint"), "active": ("give", "grape"),
              "pending": ("bullet", "chrome"), "dropped": ("fail", "chrome")}
BAND_GLYPH = {"done": ("ok", "mint"), "active": ("give", "grape"),
              "pending": ("box", "chrome"), "dropped": ("fail", "chrome")}
BAND_STEPS = 3
BAND_TASKS = 5
WORK_TAIL = {"ok": "done", "failed": "failed", "interrupted": "stopped"}
SLOT = {"running": "grape", "queued": "ink", "ok": "mint", "failed": "live", "interrupted": "ink"}
CAMEO = {"running": "determined", "queued": "neutral", "ok": "happy",
         "failed": "sad", "interrupted": "embarrassed"}
ENERGY = {
    "excited": 1.5, "surprised": 1.6, "happy": 1.3, "determined": 1.1,
    "scared": 1.5, "angry": 1.1, "affectionate": 0.9, "thinking": 0.7,
    "confused": 0.8, "embarrassed": 0.8, "sad": 0.5, "crying": 0.45,
    "sleepy": 0.35, "neutral": 1.0,
}

PEEK = {
    "memory_recall": ("recalling…",),
    "session_search": ("looking back through what you told me…", "looking back…"),
    "web_extract": ("reading…",),
    "skill_list": ("checking what she knows how to do…", "checking her skills…"),
    "skill_view": ("reading one of her own notes…", "reading a skill…"),
    "clarify": ("working out what to ask you…", "asking…"),
    "todo": ("laying the steps out…", "planning…"),
    "ask_user": ("over to you…",),
    "view_capture": ("looking at that screenshot again…", "looking again…"),
    "recall_image": ("finding the picture she kept…", "remembering…"),
    "open_link": ("finding you the link…", "a link for you…"),
    "start_work": ("setting the long job going…", "setting to work…"),
    "cancel_work": ("calling it off…",),
    "read_file": ("reading the file…", "reading…"),
    "search_files": ("going through the workdir…", "searching…"),
    "activate_tools": ("loading the tools she just gained…", "loading…"),
    "ask_secret": ("asking you for one secret…", "asking…"),
    "get_credential": ("fetching a key by name, never its value…", "fetching a key…"),
    "request_credential": ("asking you to save a key…", "asking…"),
}
WORK_VERB = {"web": "looking that up…", "file": "writing it down…",
             "code": "running the numbers…", "shell": "at the terminal…",
             "mcp": "wiring herself a new tool…", "skill": "reading her notes…",
             "memory": "checking what she kept…", "tool": "on it…",
             "team": "sending someone else to look…", "": "getting going…",
             "browse": "looking around the web…", "find_tool": "looking for a tool…",
             "mcp_tool": "using one of her tools…"}
MCP_ACTION_VERB = (("browser__", "browse"), ("mcp_install", "mcp"), ("connect MCP", "mcp"),
                   ("mcp_find", "find_tool"), ("search MCP registry", "find_tool"))
HELD_VERB = {"shell": "at the terminal…", "code": "running the numbers…"}

ROSTER_MIN_W = 62   # the nameplate alone is thirty cells; under this the line-up and the plate fold
FOLD_ROWS = 20      # under this many rows the line-up and the band fold, and the bar drops its count
ROLE_COL = 9        # the rank's column: every toolset `delegate` can name is inside it (see `_role`)
ROLE_MAX = 20       # past this a rank has stopped being a name; the goal keeps the rest of the row
THINK_S = 0.25      # one beat of her breath, in seconds
GRANT_COL = 4       # the ordinal's column in `/approvals`: a handle, never a name
_MCP_ASK = re.compile(r"\bMCP (server|registry)\b")
FLATTEN = re.compile(r"\s*[\x00-\x1f\x7f][\s\x00-\x1f\x7f]*")


@dataclass
class Spin:
    """The pulse's phase, accumulated in SECONDS: `tick` is called once per frame by whatever owns the
    live region and `spinner` reads it, so the ramp cannot ride whatever the repaint throttle happens
    to be. Below 0.2 s a row shows the quiet bullet: an action that lands immediately should not flash."""

    caps: Caps
    hz: float = 12.0
    acc: float = 0.0
    at: float = 0.0

    def tick(self, mood: str = "neutral", hz: float | None = None) -> None:
        """Advance the meter one frame's worth; `hz` is the SURFACE's sampling rate, never a speed knob.

        The meter fills at `ENERGY[mood] * self.hz`, 12 a second at neutral; a four-frame table is a
        swell already and turned three times a second there — a strobe — so a table no longer than the
        beat's fills at `BEAT_HZ`. A sampler faster than the meter shows every frame in order; one no
        faster cannot, a fraction of a frame per draw being a stall or a skip and more than one a jump.
        So a surface below the beat (SSH's 4, `--calm`'s 2) gets the SWELL, one whole `stride` per draw
        snapped to a swell step, or it reads as the ramp at an offset. A lower rate wants fewer frames,
        not slower ones: below ~4 Hz no table reads as motion. At or above the beat the whole ramp is
        kept, and `hz=None` is a test or a still."""
        now = time.monotonic()
        dt = min(0.25, max(0.0, now - (self.at or now)))
        self.at = now
        table = len(self.caps.g["spin"])
        speed = ENERGY.get(mood, 1.0) * (self.hz if table > len(BEAT_FRAMES) else float(BEAT_HZ))
        if hz is not None and hz <= speed:
            stride = self.stride() if hz < BEAT_HZ else 1
            self.acc = (int(self.acc) // stride + 1) * stride
        else:
            self.acc += speed * dt

    def stride(self) -> int:
        return stride_of(self.caps.g["spin"])

    def spinner(self, elapsed: float) -> str:
        if elapsed < 0.2:
            return self.caps.g["bullet"]
        frames = self.caps.g["spin"]
        if self.caps.reduced_motion:
            return frames[len(frames) // 3]
        return frames[int(self.acc) % len(frames)]

    def breath(self, period: float = THINK_S) -> str:
        """Her composing mark, off the wall clock and not off `acc`: a mood-free rhythm is the whole
        point of it, and `acc` is `ENERGY` by construction — scaling this by `ENERGY["thinking"]`
        (0.7) would be circular, and would make the state she spends most of a turn in the sluggish
        one. `period` is one beat: `THINK_S` at a turn's own sampling, the sampler's frame otherwise.

        The ramp shares no frame with `spin` in EITHER charset, and that is the whole point of the
        mark rather than a detail of it: the ASCII pair was `.oO` against `.oOo`, the spinner's own
        characters in the spinner's own column, so on a terminal with no colour to tell them apart
        "she is composing" and "a tool is running" drew the same glyph again."""
        frames = self.caps.g["think"]
        if self.caps.reduced_motion:
            return frames[0]
        return frames[int(time.monotonic() / period) % len(frames)]


def stride_of(frames: str) -> int:
    """Ramp frames per swell step: 3 for the twelve-frame ramp, 1 for `.oOo`, which IS a swell. The
    one arithmetic under `swell` and `Spin.stride`, so the table a test reads and the steps the meter
    takes cannot be two."""
    return max(1, len(frames) // len(BEAT_FRAMES))


def swell(frames: str) -> str:
    """The spinner's ramp at the beat's four steps — `▁▄▇▄` out of `▁▂▃▄▅▆▇▆▅▄▃▂`, `.oOo` as it is.

    Not a new table: the ramp is a seven-level triangle and `BEAT_FRAMES` is low-mid-high-mid, so
    the swell is every third frame of it, and the ASCII ramp was drawn as one already. Being a
    subset of the ramp it keeps the ramp's promise to `Spin.breath` — no glyph shared, in either
    charset. `Spin.tick` never reads this table: it strides the meter by `stride_of` and `spinner`
    lands on exactly these frames."""
    return frames[::stride_of(frames)]


def chip(caps, kind: str, *, filled: bool = True) -> Text:
    label, style = CHIPS.get(kind, CHIPS["tool"])
    slot = style.split(".", 1)[1]
    out = Safe()
    if filled:
        out.append(f" {label:<5} ", style=style)
    elif not caps.unicode:
        out.append(f"[{label:<5}]", style=slot)
    else:
        out.append(caps.g["edge_l"], style=slot)
        out.append(f"{label:<5}", style=slot)
        out.append(caps.g["edge_r"], style=slot)
    return out


def still_glyph(caps, hue: str = "grape") -> Text:
    """The mark a running row wears once it is committed: the same "this one is out" `▸` the opening WORK
    bracket and a gift row already carry, so nothing new is invented for it."""
    return Safe(caps.g["give"], style=hue)


def breath_mark(caps, spin: "Spin | None", period: float = THINK_S) -> Text:
    """The mark her THINKING wears, and nothing else does: grape, the family colour `thinking` already
    carries, against the spinner's sun, and a swell that resets against a meter that fills — "she is
    composing" and "a tool is running" were drawing one glyph.

    `period` is the SURFACE's frame length, never taste: a region sampled at `CARD_S` reads a `THINK_S`
    ramp 2-of-3 frames at a time, and the swell plays backwards. It is reachable in practice — a long
    job's card opening inline over a thinking turn.

    Still when the region passes no spinner, as every other mark out there is: at the prompt nothing
    may cost bytes per second."""
    if spin is None:
        return still_glyph(caps, "grape")
    return Safe(spin.breath(period), style="grape")


#: The long job's mark and how many of its frames a second: growing INTO the still `▸` it rests on and
#: settling back, so the loop has no jump — a 3-frame cycle that snapped from `▸` to `·` read as a
#: stutter. Every frame is a glyph the table already carries, so `--ascii` gets `. * >` and not a hole.
BEAT_FRAMES = ("bullet", "pip", "give", "pip")
BEAT_HZ = 6


def beat_mark(caps, elapsed: float, hz: float = BEAT_HZ) -> Text:
    """The mark row one wears out at the prompt, where no region passes a `Spin`.

    `hz` is the rate of the SURFACE that samples this, not a speed knob: the phase must be the sampler's
    or the sampler tears it — a 6 Hz phase read at 2 Hz drew `▪▸▪·▪▸▸▪`, reverse-order with stalls. It
    is sampled at `BEAT_HZ`, not once a second: three frames at a frame a second took three seconds and
    read as a stutter. Measured at 96 columns and paid only while a job runs, 6 Hz costs ~1.2 kB/s and
    ~8-9 % of one core (~1.4 % per Hz, linear from 1 to 30) against ~280 B/s and ~1.5 % for a
    once-a-second mark, with keystroke echo unmoved (p50 ~16 ms) and an idle prompt still at 0 B/s.
    Below ~4 Hz the mark parks between frames; above ~12 the CPU doubles for nothing. Still under
    `--calm`, where the wake drops back to the clock's half-second."""
    if caps.reduced_motion:
        return still_glyph(caps, "grape")
    return Safe(caps.g[BEAT_FRAMES[int(elapsed * hz) % len(BEAT_FRAMES)]],
                style="grape")


def tool_row(caps, step, width: int, elapsed: float = 0.0, note: str = "",
             *, spin: Spin | None = None, still: bool = True, hue: str = "grape") -> Text:
    """One step frame as its row. `pending` is the deferred case: it has not run, and a row that reads it
    off `ok` alone reports a failure that never happened.

    NO PRODUCTION CALLER: the CLI banks every action on a `state.Tool` and draws it with `tool_text`
    below. This is the same row off a raw `step` frame, and it is reached only from the tests that pin
    the shared half — `_action_row`, `_mark`, `STATE_GLYPH`'s fallback. Kept as the cheaper fixture for
    those, not as a second way in; a new surface should take `tool_text`."""
    tail = note or step.note or ("needs you" if step.state == "pending" else _clock(elapsed))
    return _action_row(
        caps, width, mark=_mark(caps, step.state, spin=spin, elapsed=elapsed, still=still, hue=hue),
        kind=step.kind, action=step.action, detail=step.result, tail=tail,
        filled=step.state not in ("running", "pending"), failed=step.state == "failed")


def tool_text(caps, tool, width: int, *, spin: Spin | None = None, still: bool = False,
              hue: str = "grape", word: str = "", clock: bool = True) -> Text:
    """The same row off the turn's own record, which clocks itself and remembers what came back. `word`
    replaces the clock for an outcome a duration cannot say — a deferred command's `lapsed`, or the
    stored grant that is the only reason this one never stopped to ask. `clock=False` takes it off
    entirely, which is what a panel wants: a clocked row costs bytes per second while the panel is up."""
    tail = word or tool.note or ("needs you" if tool.state == "pending"
                                 else (_clock(tool.elapsed) if clock else ""))
    return _action_row(
        caps, width, mark=_mark(caps, tool.state, spin=spin, elapsed=tool.elapsed, still=still, hue=hue),
        kind=tool.verb, action=tool.arg, detail=tool.detail, tail=tail,
        filled=tool.state not in ("running", "pending"), failed=tool.state == "failed")


def approval_kind(card) -> str:
    """Which chip an approval wears. `card.family` cannot answer it: `command_family()` returns the
    action's FIRST TOKEN (`rm`, `npm`), so a `family == "command"` test is never true and every card drew
    the generic chip. The label IS the action text, and that is what says which tool is asking."""
    label = " ".join((card.label or "").split())
    if card.family == "execute_code" or label.startswith("run Python:"):
        return "code"
    if _MCP_ASK.search(label):
        return "mcp"
    return "shell"


def approval_row(caps, card, width: int) -> Text:
    """The one heavy moment: a command that will not run until a human says so. Sun, because what it is
    waiting on is you — and it blocks right here, inline, rather than deferring to a card nobody drew.
    The label is folded to one line first: `execute_code` sends its whole snippet, newlines and all.

    This is the one row that keeps the plain tail clip while every other one elides a path through the
    middle. A clipped path is obviously unfinished; an elided one is a complete-looking path that is
    not the one about to run, and the moment a person is being asked to consent is the moment that
    difference costs something. The card behind `?` has the whole command, wrapped and unclipped."""
    line = Safe()
    line.append(caps.g["ask"], style="sun")
    line.append(" ")
    line.append_text(chip(caps, approval_kind(card), filled=False))
    line.append(" ")
    body = caps.t(" ".join((card.label or "she wants to run something").split()))
    line.append(_clip(caps, body, max(width - line.cell_len - 12, 8), elide_paths=False),
                style="hard")
    line.append(" " * max(1, width - line.cell_len - 9) + "needs you", style="sun")
    return line


def alike(drawn: Sequence[str]) -> list[bool]:
    """For each row, whether another row in the same listing draws exactly the same thing.

    A listing that hands out numbers to ACT on owes rows that can be told apart: `/approvals rm 2`
    revokes by number, and three deploy scripts under different directories all elided to
    `bash /home/…/deploy.sh`, so the grant somebody meant to revoke stayed live. Ambiguity is a
    property of the SET and of nothing in the row itself, which is why it can only be asked here, with
    every drawn row in hand, and why the answer changes when a neighbour does.

    What this marks is drawn whole instead, and the height budget shows fewer grants and says how many
    it held back: incomplete rather than mistakable."""
    seen: dict[str, int] = {}
    for text in drawn:
        seen[text] = seen.get(text, 0) + 1
    return [seen[text] > 1 for text in drawn]


def grant_rows(caps, n: int, pattern: str, permits: str, width: int, *,
               whole: bool = False) -> list[Text]:
    """One saved always-allow: the number, the command line it stands for, and what it buys under it.

    The command IS the identity here and the ordinal is only a handle, so the line takes the row. A
    listing row's twenty-cell name column is for a NAME, and spending it on `1.` left a forty-cell
    command eight cells to be recognised in at a thirty-column window — `bash /h…`, three times over.

    `whole` prints the line unelided and unclipped, wrapped over as many rows as it needs. That is what
    this listing does for a grant another grant would draw the same as (`alike`): a command wrapped in
    full cannot be mistaken for a different one, whatever the window is."""
    head = f"{n}."
    text = caps.t(scrub(pattern))
    room = max(width - GRANT_COL, 1)
    body = _lines(text, room) if whole else [_clip(caps, text, room)]
    out = [Safe(head + " " * max(1, GRANT_COL - cell_len(head)) + body[0], style="hard")]
    out += [Safe(" " * GRANT_COL + line, style="hard") for line in body[1:]]
    out += [Safe(" " * GRANT_COL + line, style="chrome")
            for line in wrap(caps.t(permits), room)]
    return out


def _lines(text: str, room: int) -> list[str]:
    """`text` over as many rows as it needs, broken at a space or — inside a path too wide for one row —
    at a SEPARATOR, so that no directory name is cut in half.

    `render/text.wrap` breaks an over-wide token on cells, which is right for prose and wrong here: it
    put `stagin` on one row and `g/deploy.sh` on the next, and the name it split is the whole reason
    this grant is being drawn whole. The separator stays on the line it ends, so a continuation always
    begins on a name. A segment wider than the row still gives way by cells — there is nothing else
    left to break on."""
    out: list[str] = []
    for word in text.split(" "):
        for chunk in _split(word, room):
            if out and cell_len(out[-1]) + 1 + cell_len(chunk) <= room:
                out[-1] += " " + chunk
            else:
                out.append(chunk)
    return out or [""]


def _split(word: str, room: int) -> list[str]:
    if cell_len(word) <= room:
        return [word]
    names = word.split("/")
    parts, out, line = [n + "/" for n in names[:-1]] + names[-1:], [], ""
    for part in parts:
        while cell_len(part) > room:
            take = ""
            for char in part:
                if cell_len(take + char) > room:
                    break
                take += char
            take = take or part[0]
            out += [line] if line else []
            out, line, part = out + [take], "", part[len(take):]
        if line and cell_len(line) + cell_len(part) > room:
            out, line = out + [line], ""
        line += part
    return [part for part in out + [line] if part]


def work_row(caps, work, width: int, *, opening: bool = False, word: str = "",
             clock: bool = True) -> Text:
    """The bracket around the long job: one row when it goes out, its twin when it comes back — same
    seven-cell chip, same clock, column 0, because it is machine output and the gutter is for what she
    SAYS.

    Grape, never sun: sun means waiting on you, and she is the one working here. The ordinal sits
    OUTSIDE the chip, since the chip is a fixed seven cells with a five-cell label and widening it for
    `WORK 1` would move every tool row in the block. The closing bracket falls back the same way every
    other row does, to `?` and the state's own word: it used to fall back to the RUNNING mark and the
    word `done`, drawing a job still going and calling it finished."""
    line = Safe()
    if opening:
        line.append(caps.g["give"], style="grape")
    else:
        key, style = STATE_GLYPH.get(work.state, STATE_GLYPH["unknown"])
        line.append(caps.g[key], style=style)
    line.append(" ")
    line.append_text(chip(caps, "work", filled=not opening))
    line.append(" ")
    line.append(f"{work.n:02d}", style="hard")
    line.append("  ")
    tail = _plain(caps, word or (("started" if opening else state_word(work.state)) if clock else ""))
    if opening:
        body = work.goal
    elif work.state == "interrupted":
        bits = ([f"{duration(work.elapsed)} in"] if clock else []) + ["nothing kept"]
        body = f" {caps.g['bullet']} ".join(bits)
    else:
        kept = len(work.gift_ns) if work.state == "ok" else 0
        bits = ([_count(kept, "file")] if kept else []) + ([duration(work.elapsed)] if clock else [])
        body = f" {caps.g['bullet']} ".join(bits)
    room = max(width - line.cell_len - cell_len(tail) - 3, 8)
    line.append(_clip(caps, caps.t(body), room), style="" if opening else "chrome")
    line.append(" " * max(1, width - line.cell_len - cell_len(tail)) + tail, style="chrome")
    return line


def gift_rows(caps, gift, n: int, width: int) -> list[Text]:
    """What she hands you. A URL is never ellipsised and never wrapped mid-token — a cut URL is a dead
    URL, and one with a newline inside it cannot even be double-clicked. When it does not fit beside the
    chip it takes a row of its own, printed unwrapped so the terminal breaks it and it stays one
    selectable run, and the description goes below.

    Two kinds carry no `/open`: what you SENT her, and a reminder that came due. Neither is a thing on
    disk, and a number pointing at a sentence is a number that opens nothing."""
    line = Safe()
    line.append(caps.g["take"] if gift.kind == "sent" else caps.g["give"], style="coral")
    line.append(" ")
    line.append_text(chip(caps, gift.kind))
    line.append(" ")
    tail = _plain(caps, gift.tail or ("" if gift.kind in ("sent", "due") else f"/open {n}"))
    target, note = caps.t(scrub(gift.target)), caps.t(scrub(gift.note))
    room = width - line.cell_len - cell_len(tail) - 2
    if cell_len(target) > room:
        line.append(_clip(caps, note, room), style="chrome")
        _tail(line, tail, width)
        return [line, Unwrapped(target)]
    line.append(target)
    spill = ""
    if note:
        beside = f" {caps.g['bullet']} {note}"
        if line.cell_len + cell_len(beside) + cell_len(tail) + 2 < width:
            line.append(beside, style="chrome")
        else:
            spill = _clip(caps, note, width)
    _tail(line, tail, width)
    return [line] + ([Safe(spill, style="chrome")] if spill else [])


def helper_text(caps, helper, n: int, width: int, *, spin: Spin | None = None,
                still: bool = False, clock: bool = True) -> Text:
    """Her nameplate, scaled down: the same sigil, the same seven cells, the same hard shadow, and a
    kaomoji beside it — so a helper reads as a little Kotoba that was sent somewhere, not as row three of
    a table. The rank is the only name there is; a subagent has none on the wire.

    Step pips, never a percentage: `SUB_MAX_ITERATIONS` is a cap and not a target, so a meter against it
    sits at one eighth and then snaps to full. Pips accumulate and cannot be wrong."""
    line = Safe()
    line.append_text(_mark(caps, helper.state, spin=spin, elapsed=helper.elapsed, still=still))
    line.append(" ")
    slot = SLOT.get(helper.state, "ink")
    line.append(f" {caps.g['sigil']} {n:02d} ", style=f"chip.{slot}")
    if caps.color != "none":
        line.append(caps.g["shadow"], style=f"shadow.{slot}")
    line.append(" ")
    line.append(_cameo(caps, helper.state), style="chrome" if slot == "ink" else slot)
    line.append("  ")
    _bold(line, _role(caps, helper.role), "chrome" if slot == "ink" else slot)
    tail = _clock(helper.elapsed) if clock else ""
    pips = _pips(caps, helper)
    room = width - line.cell_len - cell_len(pips) - cell_len(tail) - 5
    if room < 8:
        pips, room = "", width - line.cell_len - cell_len(tail) - 5
    line.append(_clip(caps, caps.t(helper.goal), max(room, 0)), style="chrome")
    line.append(" " * max(1, width - line.cell_len - cell_len(pips) - cell_len(tail) - 1))
    line.append(pips, style="chrome" if slot == "ink" else slot)
    line.append(" " + tail, style="chrome")
    return line


def helper_rows(caps, helper, n: int, width: int, *, spin: Spin | None = None,
                still: bool = False, clock: bool = True) -> list[Text]:
    """The nameplate at any width — the gate the live line-up keeps, given to the surfaces that PRINT a
    plate and to the panel painted over the transcript.

    The plate is thirty cells before the goal has started, which under `ROSTER_MIN_W` is wider than the
    measure it is handed — thirty-seven cells into twenty-eight at a thirty-column window — and a row
    over its measure is not clipped by anything, it is folded by the terminal wherever it likes. So it
    folds to TWO rows, never one: a transcript that swapped a helper for a count would delete the only
    record there is of it. Row one keeps the rank, the state and the name, the goal takes row two, and
    the cameo and the pips go. Height is `helper_height`, and a caller with a row budget must ask it
    rather than assume one."""
    if width >= ROSTER_MIN_W:
        return [helper_text(caps, helper, n, width, spin=spin, still=still, clock=clock)]
    line = Safe()
    line.append_text(_mark(caps, helper.state, spin=spin, elapsed=helper.elapsed, still=still))
    line.append(" ")
    slot = SLOT.get(helper.state, "ink")
    line.append(f" {caps.g['sigil']} {n:02d} ", style=f"chip.{slot}")
    if caps.color != "none":
        line.append(caps.g["shadow"], style=f"shadow.{slot}")
    line.append("  ")
    tail = _clock(helper.elapsed) if clock else ""
    room = width - line.cell_len - cell_len(tail) - 1
    if room < 4:
        tail, room = "", width - line.cell_len - 1
    _bold(line, _clip(caps, caps.t(helper.role), max(room, 0)), "chrome" if slot == "ink" else slot)
    _tail(line, tail, width)
    goal = _clip(caps, caps.t(helper.goal), max(width - 5, 0))
    return [line, Safe("     " + goal, style="chrome")]


def helper_height(width: int) -> int:
    """Rows one nameplate takes at this width, for a caller that has to budget them before it draws."""
    return 1 if width >= ROSTER_MIN_W else 2


def roster_rows(caps, helpers, width: int, *, spin: Spin | None = None, folded: bool = False,
                peeked: int = 0) -> list[Text]:
    """The line-up: insertion order, fixed height for the whole turn. A finished helper greys in place
    instead of rising and shoving the others up mid-read — that motion is the entire cost of a roster."""
    if not helpers:
        return []
    if folded or caps.height < FOLD_ROWS or width < ROSTER_MIN_W:
        return [roster_fold(caps, helpers, width, spin=spin)]
    out = []
    for i, helper in enumerate(helpers, 1):
        out.append(helper_text(caps, helper, i, width, spin=spin))
        if i == peeked:
            seen = [step_line(caps, s, width) for s in helper.steps[-3:]]
            out += [Safe(s, style="chrome") for s in seen or ["     " + caps.t("nothing yet")]]
    return out


def step_line(caps, step, width: int) -> str:
    """One of a helper's steps, in the one shape every surface draws it in — the peek inside a turn and
    `/helpers` afterwards used to indent it differently, which is the live region and the transcript
    disagreeing about a row; `^r`'s panel came later and takes the same builder.

    The mark is the wire's `ok`: the quiet bullet for the line that says what she is about to do, and
    the `✓` or `×` of the glyph table for the one that reports how it went. Before
    that field existed the two were the same string and a failure showed only as `_result_text`'s
    `  ! ` prefix, which no renderer should have to read."""
    text, ok = step
    key = "bullet" if ok is None else ("ok" if ok else "fail")
    return f"     {caps.g[key]} {_clip(caps, caps.t(str(text)), width - 7)}"


def roster_fold(caps, helpers, width: int, *, spin: Spin | None = None) -> Text:
    """The whole line-up as one row, for a short window or a `^r`. The hint has a short twin and then
    goes entirely, and the `N back` tail goes the same way with no twin at all (`fit` answers a room
    nothing fits in with ""), so a narrow terminal loses those rather than the count."""
    out = sum(1 for h in helpers if h.state in ("running", "queued"))
    line = Safe()
    line.append_text(_mark(caps, "running" if out else "ok", spin=spin, elapsed=1.0, hue="sun"))
    line.append(" ")
    line.append(f" {caps.g['sigil']} {len(helpers):02d} ", style="chip.grape")
    if caps.color != "none":
        line.append(caps.g["shadow"], style="shadow.grape")
    line.append("  ")
    _bold(line, caps.t(f"{out} on stage" if out else "the line-up is done"), "grape")
    done = len(helpers) - out
    if done:
        line.append(fit(width - line.cell_len, caps.t(f" · {done} back")), style="chrome")
    line.append(fit(width - line.cell_len, caps.t("   ^r opens the line-up"), caps.t("   ^r opens")),
                style="chrome")
    return line


def plan_rows(caps, plan, width: int) -> list[Text]:
    """The receipt for a task list: a header row and one row per step. ONE of these reaches the
    transcript, and it goes there when the list CLOSES (`app._task_list`); it used to be reprinted at
    every landed step as well, and those reprints in the middle of her messages are the thing the owner
    refused. While the list is open the band is where it lives (`band_rows`), and `/plan` draws the open
    shape on demand — which is the only caller the `opening` branch has left."""
    opening = plan.get("status") == "open"
    tasks = sorted(plan.get("tasks") or [], key=lambda t: t.get("order", 0))
    done = sum(1 for t in tasks if t.get("status") == "done")

    head = Safe()
    if opening:
        head.append(caps.g["give"], style="grape")
    else:
        key, style = STATE_GLYPH["ok" if plan.get("status") == "done" else "interrupted"]
        head.append(caps.g[key], style=style)
    head.append(" ")
    head.append_text(chip(caps, "plan", filled=not opening))
    head.append(" ")
    tail = f"{len(tasks)} steps" if opening else f"{done} of {len(tasks)}"
    title = caps.t(str(plan.get("title") or "plan"))
    room = max(width - head.cell_len - cell_len(tail) - 3, 8)
    head.append(_clip(caps, title, room), style="" if opening else "chrome")
    head.append(" " * max(1, width - head.cell_len - cell_len(tail)) + tail, style="chrome")

    rows = [head]
    for task in tasks:
        key, style = TASK_GLYPH.get(task.get("status", "pending"), TASK_GLYPH["pending"])
        row = Safe("   ")
        row.append(caps.g[key], style=style)
        row.append(f"  {task.get('order', 0)}  ", style="chrome")
        row.append(caps.t(str(task.get("text", ""))),
                   style=style if task.get("status") == "active" else "chrome")
        rows.append(row)
    return rows


def step_verb(kind: str, action: str = "") -> str:
    """The phrase for ONE running step, off its coarse kind and — where the kind is too coarse — its
    action.

    `mcp` is the coarse one: `core/loop._step_kind` hands it to `mcp_find`, to `mcp_install`, to every
    `browser__` call and to every tool on every connected server alike. Read off the kind alone, an
    ordinary browser hop said `wiring herself a new tool…` for 3m 24s — a claim she had installed
    something nobody asked for. The action says which it actually was, and the install phrase is kept
    for the one call that genuinely is one."""
    if kind == "mcp":
        for prefix, slot in MCP_ACTION_VERB:
            if action.startswith(prefix):
                return WORK_VERB[slot]
        return WORK_VERB["mcp_tool"]
    return WORK_VERB.get(kind, WORK_VERB["tool"])


def work_is_live(state) -> bool:
    """Is the long job running right now. ONE read, shared by the band's job row, the bar's long-job
    phrase and the bar's chip, because a chip that said WORKING while the phrase said the job was done
    would be worse than having no chip at all."""
    work = getattr(state, "work", None)
    return bool(work is not None and work.state == "running" and not work.landed)


#: Her phrase for a job that has ENDED, longest first. One origin for both surfaces: the bar reads it
#: too, and a second wording would let the band and the bar disagree about the same dead job.
WORK_ENDED_SAID = {
    "ok": ("the long job is done — she's telling you now",
           "the long job is done", "long job done"),
    "failed": ("the long job didn't work out — she'll say why",
               "the long job didn't work out", "long job failed"),
    "interrupted": ("stopped the long job — nothing of it was kept",
                    "stopped the long job", "long job stopped"),
}


def work_held(state) -> bool:
    """Whether the surfaces still carry a job that has ended, until the next turn begins.

    Its receipt printed into rows the band immediately paints over, and at rest the prompt never ends
    to repaint them, so the glass kept a plan step and no word that the job was over. Only the two
    endings she does NOT speak to: a job that finished well gets her own sentence in the transcript,
    while a stopped one and a failed one both close with a bracket and nothing else — every failure
    path sends an empty summary, so there is no receipt to print either."""
    work = getattr(state, "work", None)
    return bool(work is not None and work.state in ("interrupted", "failed")
                and not getattr(work, "cleared", False))


def work_said(work) -> tuple[str, ...]:
    """The long job's CURRENT step, longest phrase first, for a surface that fits what it can.

    Derived, not latched: `Work.verb` was written when a step STARTED and by nothing else, so a web
    search that took four seconds was still `looking that up…` ninety seconds after it landed.

    The first phrase is what the step is actually DOING — the tool's own argument, the helper's rank
    and goal — and `WORK_VERB` stays as the last phrase rather than the only one: it is her own words
    for the kind of thing, what the bar carries, and what a window too narrow for the argument falls
    back to. Helpers out are read FIRST, because `delegate` is a running step for exactly as long as
    its line-up is: read tool-first, the row said `delegate` for the whole of a three-helper job."""
    out = [h for h in work.helpers if h.state in ("running", "queued")]
    if out:
        said = _sent_out(out)
        return (said, WORK_VERB["team"]) if said else (WORK_VERB["team"],)
    running = next((t for t in reversed(work.tools) if t.state == "running"), None)
    if running is not None:
        verb = step_verb(running.verb, running.arg)
        return (running.arg, verb) if running.arg else (verb,)
    return (WORK_VERB["tool"] if (work.tools or work.helpers) else WORK_VERB[""],)


def _sent_out(helpers) -> str:
    """What she has somebody else doing: the rank and the goal for one, the count and the oldest goal
    for several — `2 helpers out`, the words `/helpers` and the bar use, so one line-up is one phrase.
    Empty when nothing on the wire named a goal, which is what sends the caller back to the coarse
    phrase rather than drawing a rank with nothing after it."""
    first = helpers[0]
    goal = " ".join((first.goal or "").split())
    if not goal:
        return ""
    if len(helpers) > 1:
        return f"{_count(len(helpers), 'helper')} out · {goal}"
    role = " ".join((first.role or "").split())
    return f"{role} · {goal}" if role else goal


def work_verb(work) -> str:
    """The coarse phrase for the job's current step — `work_said`'s last one, never a second table.

    The bar keeps this and the band no longer does, so the two rows say two different things about one
    job instead of the same thing twice. It stays the bar's because every twin there has to shorten
    (`footer.work_twins`, whose shortest twin is what the bar's byte budget is measured against) and a
    model-written argument cannot."""
    return work_said(work)[-1]


def band_rows(caps, state, width: int, *, spin: Spin | None = None, room: int = 0,
              hz: float = BEAT_HZ) -> list[Text]:
    """The status band above the input box: her current action on row one and the open plan under it,
    from ONE builder for BOTH surfaces so the two renderers can never disagree about a row.

    `room` is how many rows the surface has, and it reaches the builder because a band sliced blind from
    the bottom loses the pending steps and the `… +N pending` tail — the point of the surface for
    somebody watching a job they cannot see. Two rows before the plan, because they are two independent
    facts: sharing one let the turn win, so a message sent while a job ran took the job's row off the
    glass. Down to ONE row it goes to whichever fact the bar is not already carrying. The band IS its
    content — no run and no plan is zero rows and zero bytes — and only the head rows carry a clock,
    since a clocked row costs bytes per second while the band is up."""
    head = [r for r in (_band_job(caps, state, width, hz),
                        _band_now(caps, state, width, spin=spin, hz=hz)) if r is not None]
    if state.folded or caps.height < FOLD_ROWS or width < ROSTER_MIN_W:
        return head[:1]
    if room and room <= len(head):
        if room == 1 and not (state.turn_start and work_is_live(state)):
            return _band_steps(caps, state, width, elbow=False, tall=1) or head[:1]
        return head[:room]
    idle = (not head and not state.approval and not state.confirm
            and not state.turn_start and not work_is_live(state))
    want = BAND_TASKS + 1 if idle else BAND_STEPS
    tall = want if not room else min(want, max(0, room - len(head)))
    return head + _band_steps(caps, state, width, elbow=bool(head), tall=tall, idle=idle)


def _band_job(caps, state, width: int, hz: float = BEAT_HZ) -> Text | None:
    """The long job's row: what she is doing, or how it ended when nothing said so.

    `work_is_live` is the reading — the same one the bar's chip and phrase share, so a fourth reading
    cannot disagree with the others about whether she is still on it. Live, it says the job's CURRENT
    step, argument first: the rung used to start at the category and throw the argument away, so a
    search, a file write and a browser hop drew the same twelve words.

    An ending she does not speak to keeps the row instead, wearing its glyph and its final duration —
    a still, so it costs nothing per second and claims no motion. A turn beginning takes it back."""
    if not work_is_live(state):
        if not work_held(state):
            return None
        work = state.work
        key, style = STATE_GLYPH[work.state]
        return _band_line(caps, width, WORK_ENDED_SAID[work.state], work.elapsed,
                          Text(caps.g[key], style=style))
    elapsed = state.work.elapsed
    return _band_line(caps, width, work_said(state.work), elapsed, beat_mark(caps, elapsed, hz))


def _band_now(caps, state, width: int, *, spin: Spin | None = None,
              hz: float = BEAT_HZ) -> Text | None:
    """What she is doing right now, or None when the turn has nothing to add. The precedence is the
    bar's and not a second vocabulary: the running tool's own argument first — never a `Considering` —
    then the read-only verb, then her thinking. For everything a MACHINE is doing the mark and the dim
    are a tool row's own, not a third treatment: that row IS a running row, just off the state instead
    of a step frame. Her thinking is the one exception, wearing `breath_mark`.

    While she SPEAKS this row is absent, and it comes and goes at TOOL boundaries rather than frame by
    frame: `status` is cleared on every chunk she writes and set back on every tool that lands. It never
    drags the job's row with it, which is what the flicker caught on camera actually was."""
    turn_el = (time.monotonic() - state.turn_start) if state.turn_start else 0.0
    mark = None
    tool = next((t for t in reversed(state.tools) if t.state == "running"), None)
    if tool is not None:
        said, elapsed = (tool.arg or tool.verb,), tool.elapsed
    elif state.peek:
        said, elapsed = PEEK.get(state.peek, (state.peek + "…",)), turn_el
    elif state.status == "thinking":
        said, elapsed, mark = ("thinking…",), turn_el, breath_mark(
            caps, spin, period=max(THINK_S, 1.0 / hz))
    elif state.status:
        said, elapsed = (step_verb(state.status),), turn_el
    else:
        return None
    return _band_line(caps, width, said, elapsed, mark or _mark(
        caps, "running", spin=spin, elapsed=elapsed, still=spin is None))


def _band_line(caps, width: int, said: tuple[str, ...], elapsed: float, mark: Text) -> Text:
    """One band row: a mark, the longest phrase that fits, and the clock at the right edge. The clock
    is `duration()`, not `_clock()`: a tenth of a second on an hour-long job is not a duration."""
    line = Safe()
    line.append_text(mark)
    line.append(" ")
    tail = duration(elapsed) if elapsed >= 1.0 else ""
    room = max(width - line.cell_len - cell_len(tail) - 3, 8)
    line.append(fit(room, *[caps.t(scrub(v)) for v in said], "")
                or _clip(caps, caps.t(said[-1]), room), style="chrome")
    if tail:
        line.append(" " * max(1, width - line.cell_len - cell_len(tail)) + tail, style="chrome")
    return line


def _band_steps(caps, state, width: int, *, elbow: bool, tall: int = BAND_STEPS,
                idle: bool = False) -> list[Text]:
    """The open plan, `tall` rows at most, windowed on the active step — the one sliding window the
    completion list takes too, because two of them diverge. Done steps are struck through their dim,
    pending ones wear the `box`, and whatever the window cannot hold says `… +N pending`, counting only
    the pendings below it so the count follows the window down. At rest the same plan goes to
    `_band_inventory` instead.

    The active step's `▸` is a motion claim, so it is worn only while something actually runs; a plan
    that outlives its run keeps its rows — it still says what she meant to do — but its current step
    wears the committed `◇`. The reading is ACTIVITY, not ownership: a task frame stamps only the
    EMITTER's run, so a plan a turn opened beside a job still running wears `▸` on the job's activity."""
    plan = state.plan
    if plan is None or not plan.is_open or tall <= 0:
        return []
    tasks = sorted((getattr(plan, "frame", None) or {}).get("tasks") or [],
                   key=lambda t: t.get("order", 0))
    if not tasks:
        return []
    if idle:
        return _band_inventory(caps, tasks, width, tall)
    at = next((i for i, t in enumerate(tasks) if t.get("status") == "active"), None)
    if at is None:
        at = next((i for i, t in enumerate(tasks) if t.get("status") == "pending"), len(tasks) - 1)
    tall = min(tall, len(tasks))
    first = window(at, len(tasks), tall)
    shown = tasks[first:first + tall]
    left = sum(1 for t in tasks[first + tall:] if t.get("status") == "pending")
    stalled = not state.turn_start and not work_is_live(state)
    out = []
    for i, task in enumerate(shown):
        status = task.get("status", "pending")
        key, style = BAND_GLYPH.get(status, BAND_GLYPH["pending"])
        if status == "active" and stalled:
            key, style = STATE_GLYPH["interrupted"]
        row = Safe("  " + caps.g["elbow"] + " " if elbow and i == 0 else "    ", style="chrome")
        row.append(caps.g[key] + " ", style=style)
        tail = caps.t(f"… +{left} pending") if left and i == len(shown) - 1 else ""
        room = max(width - row.cell_len - cell_len(tail) - 3, 8)
        text = _clip(caps, caps.t(str(task.get("text", ""))), room)
        if status == "done":
            _strike(row, text, "chrome")
        else:
            row.append(text, style=style if status == "active" else "chrome")
        if tail:
            row.append(" " * max(1, width - row.cell_len - cell_len(tail)) + tail, style="chrome")
        out.append(row)
    return out


def _band_inventory(caps, tasks, width: int, tall: int) -> list[Text]:
    """The at-rest shape of the open plan: a counts header, up to `BAND_TASKS` rows with the OPEN steps
    first, and one tail saying what the cap hid. Three bare windowed rows told an idle reader nothing —
    a 72-task list looked exactly like a 4-task one.

    AT REST ONLY. Inside a turn the band's height is already spoken for, and a header would cost an
    actual step from a three-row window; at one row it yields too, because the bar already carries
    `step N of M` while the step's own text is on no other surface. Every row is a STILL — the counts
    move only when a task frame lands — so an idle prompt still costs 0 B/s. The tail JOINS the settled
    `… +N pending`, extended to `+N done`, and unlike the in-turn count it reads the whole list. The
    active step wears the committed `◇`: at rest is stalled by definition."""
    opens = [t for t in tasks if t.get("status") in ("active", "pending")]
    closed = [t for t in tasks if t.get("status") not in ("active", "pending")]
    done = sum(1 for t in tasks if t.get("status") == "done")
    header = tall >= 2
    cap = min(BAND_TASKS, tall - 1 if header else tall)
    at = next((i for i, t in enumerate(opens) if t.get("status") == "active"), 0)
    show = min(cap, len(opens))
    first = window(at, len(opens), show)
    kept = opens[first:first + show] + closed[:max(0, cap - show)]
    hid_pending = (sum(1 for t in opens if t.get("status") == "pending")
                   - sum(1 for t in kept if t.get("status") == "pending"))
    hid_done = done - sum(1 for t in kept if t.get("status") == "done")
    bits = ([f"+{hid_pending} pending"] if hid_pending else []) \
        + ([f"+{hid_done} done"] if hid_done else [])
    tail = caps.t("… " + ", ".join(bits)) if bits else ""
    out = []
    if header:
        row = Safe("    ")
        counts = f"{_count(len(tasks), 'task')} ({done} done, {len(opens)} open)"
        row.append(_clip(caps, caps.t(counts), max(width - 7, 8)), style="chrome")
        out.append(row)
    for i, task in enumerate(kept):
        status = task.get("status", "pending")
        key, style = BAND_GLYPH.get(status, BAND_GLYPH["pending"])
        if status == "active":
            key, style = STATE_GLYPH["interrupted"]
        row = Safe("    ", style="chrome")
        row.append(caps.g[key] + " ", style=style)
        last = tail if i == len(kept) - 1 else ""
        room = max(width - row.cell_len - cell_len(last) - 3, 8)
        text = _clip(caps, caps.t(str(task.get("text", ""))), room)
        if status == "done":
            _strike(row, text, "chrome")
        else:
            row.append(text, style=style if status == "active" else "chrome")
        if last:
            row.append(" " * max(1, width - row.cell_len - cell_len(last)) + last, style="chrome")
        out.append(row)
    return out


def window(at: int, n: int, tall: int) -> int:
    """The first row of a `tall`-row window over `n` rows, centred on row `at` and never past the end:
    the ONE sliding window the band's steps, the at-rest inventory and the completion list
    (`footer.menu_rows`) share, so the three cannot drift from each other."""
    tall = min(tall, n)
    return min(max(0, at - tall // 2), n - tall) if tall else 0


def state_word(state: str) -> str:
    """The wire's state as a word, for the dim column a panel row ends in instead of a clock. It is the
    long job's own closing vocabulary and not a second one, so a panel and the receipt under it can never
    call the same outcome two different things."""
    return WORK_TAIL.get(state, state)


def hold_at(holds, *states: str):
    """The oldest deferred command in one of these states. Oldest first everywhere: two cards can be
    outstanding at once, and the one that has been waiting longest is the one whose window runs out
    first.

    The app reads its own list through this; the bar reads the snapshot's copy of the same list
    through `footer.State.hold_at`."""
    return next((h for h in holds if h.state in states), None)


def _action_row(caps, width: int, *, mark: Text, kind: str, action: str, detail: str, tail: str,
                filled: bool, failed: bool) -> Text:
    tail = _plain(caps, tail)
    line = Safe()
    line.append_text(mark)
    line.append(" ")
    line.append_text(chip(caps, kind, filled=filled))
    line.append(" ")
    room = max(width - line.cell_len - cell_len(tail) - 3, 8)
    note = _clip(caps, f" {caps.g['bullet']} {caps.t(detail)}", room // 2) if detail else ""
    line.append(_clip(caps, caps.t(action or kind), room - cell_len(note)), style="chrome")
    line.append(note, style="live" if failed else "chrome")
    line.append(" " * max(1, width - line.cell_len - cell_len(tail)) + tail, style="chrome")
    return line


def _mark(caps, state: str, *, spin: Spin | None, elapsed: float, still: bool = False,
          hue: str = "grape") -> Text:
    if state != "running":
        key, style = STATE_GLYPH.get(state, STATE_GLYPH["unknown"])
        return Safe(caps.g[key], style=style)
    if still or spin is None:
        return still_glyph(caps, hue)
    return Safe(spin.spinner(elapsed), style="sun")


def _plain(caps, text: str) -> str:
    """A row's right-hand tail, made safe BEFORE it is measured. `Safe` scrubs on the way to the
    terminal, which is one step too late for a string the padding arithmetic has already sized: a C0
    byte costs zero cells and the space it becomes costs one, so the row comes out that many cells past
    the width it was fitted to. Third-party after all — a saved grant's family is the first token of a
    command the model wrote (`core/loop._grant_note`), and a reminder's `every` is her own phrasing."""
    return caps.t(scrub(FLATTEN.sub(" ", text)))


def _tail(line: Text, tail: str, width: int) -> None:
    if tail:
        line.append(" " * max(1, width - line.cell_len - cell_len(tail)) + tail, style="chrome")


def _bold(line: Text, text: str, style: str) -> None:
    """Bold ON a hue. The palette ships `d.<slot>` but no bold twin, and rich resolves a name it does not
    know to nothing at all — so asking for one silently dropped the weight and the colour together."""
    at = len(line)
    line.append(text, style=style)
    line.stylize("bold", at, len(line))


def _strike(line: Text, text: str, style: str) -> None:
    """Struck THROUGH a hue, by the same overlay `_bold` is and for the same reason: `chrome strike`
    as one style string resolves to nothing at all — measured, it dropped the dim and the strike
    together, silently. As SGR 9, never U+0336: the combining char doubles the codepoints, breaks
    `cell_len` on the first wide character, and rides along in every copied selection."""
    at = len(line)
    line.append(text, style=style)
    line.stylize("strike", at, len(line))


def _pips(caps, helper) -> str:
    if helper.state == "queued":
        return caps.t("waiting for a slot")
    if not helper.steps:
        return ""
    k = len(helper.steps)
    return caps.g["pip"] * min(k, 8) + ("+" if k > 8 else "") + " " + _count(k, "step")


def _role(caps, role: str) -> str:
    """The rank, in a column whose gap cannot be swallowed by the rank itself.

    `f"{role:<9}"` padded to a field the longest name of the day exactly filled, so the tenth character
    left no separator and `researcher` welded to its goal — `researchergoal number 1`, one word on the
    screen and two on the wire. The column is kept rather than widened, since `ROLE_COL` already holds
    every toolset name plus the `helper` fallback; what changes is that the gap is a FLOOR, so a
    ten-character role pushes the goal two cells right instead of touching it.

    Measured and clipped in CELLS: a rank in a wide script fills the field in half the characters, and
    one arriving off the wire at any length would push the goal, the pips and the clock off the window."""
    role = _clip(caps, caps.t(role), ROLE_MAX)
    return role + " " * max(1, ROLE_COL - cell_len(role))


def _cameo(caps, state: str) -> str:
    """A helper's face: the same fourteen-face table, mouth shut, tail dropped, padded to the widest of
    the five so every goal starts on one column. It carries STATE, not mood — a helper is a job, not a
    person, and one continuously animated kaomoji per screen is the budget."""
    table = FACES_UNICODE if caps.unicode else FACES_ASCII
    drawn = _cameo_face(table, state)
    return drawn + " " * max(0, _cameo_width(caps.unicode) - cell_len(drawn))


def _cameo_face(table: dict, state: str) -> str:
    left, right, mouths, _ = table[CAMEO.get(state, "neutral")]
    return f"( {left}{mouths[0]}{right} )"


@lru_cache(maxsize=2)
def _cameo_width(unicode: bool) -> int:
    table = FACES_UNICODE if unicode else FACES_ASCII
    return max(cell_len(_cameo_face(table, s)) for s in CAMEO)


def _clip(caps, text: str, room: int, *, elide_paths: bool = True) -> str:
    """Cell-correct, because slicing by character overflows the moment a goal is in Japanese.

    A newline costs nothing in cells, so `ls -la` came back through the clip whole and printed a second
    row from a builder that returns one — the breaks are flattened once, and here. The scrub runs BEFORE
    the measure: a C0 byte is zero cells and the space it becomes is one, so a row scrubbed after it was
    fitted overflows the width it was just fitted to.

    A path gives up its middle first — `read_file /home/…/notes.md` still names the file, and the clip
    alone never did. `elide_paths=False` is for the one surface that would rather be incomplete than
    mistakable."""
    text = scrub(FLATTEN.sub(" ", text))
    if room <= 0:
        return ""
    if cell_len(text) <= room:
        return text
    if elide_paths:
        text = paths.shorten(text, room, caps.unicode) or text
        if cell_len(text) <= room:
            return text
    mark = "…" if caps.unicode else "~"
    while text and cell_len(text) + cell_len(mark) > room:
        text = text[:-1]
    return text + mark


def _count(n: int, word: str) -> str:
    """One place for every plural, so nothing renders `1 steps` — or `3 familys`, the same defect in a
    different hat."""
    if n == 1:
        return f"{n} {word}"
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return f"{n} {word[:-1]}ies"
    return f"{n} {word}s"


def _clock(elapsed: float) -> str:
    return f"{elapsed:4.1f}s" if elapsed >= 0.95 else "     "
