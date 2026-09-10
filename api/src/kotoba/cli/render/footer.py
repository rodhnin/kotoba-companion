"""The last rows of the window: her frame, and the one thing on the screen allowed to move.

The bar is chip, face, phrase and right slot, and precedence is one rule: everything the turn is doing
outranks everything out of turn, and all of it outranks the idle clause. The right slot is a state
channel, not a clock; every phrase has short twins down to nothing — one that shortens reads as a
decision, one that truncates as a bug. Every rate is tied to the wake that draws it (`frame_hz` IS that
wake, inverted), and the WORKING mark is the one motion device: every other mark is a status blink or a
still. Every row is a `Safe`, scrubbed BEFORE it is measured: a reminder with eight C0 bytes fitted at
20 cells and drew at 28, wrapping the bar and walking the frame. Two renderers draw it cell for cell.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from rich.cells import cell_len
from rich.text import Text

from kotoba.cli.render.rows import (BEAT_HZ, FOLD_ROWS, HELD_VERB, PEEK, WORK_ENDED_SAID, _count,
                                    band_rows, window, work_held, work_is_live, work_verb)
from kotoba.cli.render.safe import Safe
from kotoba.cli.render.text import duration, fit
from kotoba.core.text_security import scrub

HEARTBEATS = ("Working on it…", "Still running…", "Still at it…",
              "Not stuck — it's just a slow one…")
OUT_KINDS = ("card", "due", "okayed", "okdone", "long", "landed", "plan")
SUN_KINDS = ("wait", "card", "due")

HEAVY = {"┌": "┏", "┐": "┓", "└": "┗",
         "┘": "┛", "─": "━", "│": "┃"}
PLAIN_BOX = {"┌": "+", "┐": "+", "└": "+", "┘": "+",
             "─": "-", "│": "|"}


class Running(Protocol):
    """A tool step or a helper. The bar reads only whether it is still going — and it reads the WIRE's
    words for that, never a second vocabulary of the footer's own. Which is why the two
    endings that arrived later — `refused` for an action that never ran, `unknown` for a word this build
    cannot place — cost this file nothing: everything here asks whether a row is `running` or `queued`,
    and neither of those two is either."""

    state: str          # running | queued | pending | ok | failed | refused | interrupted | unknown


class Job(Protocol):
    """The long job. `landed` is work_state's `announced` — the receipt is told once, and only in the
    transcript; the bar carries the job while it runs, and after an ending nobody else voiced.

    `tools` and `helpers` are read for the PHRASE, which is the job's current step and not the last one
    it ever started (`rows.work_verb`) — derived on every read, never a field latched at a step's
    start, which is the verb that outlived its tool."""

    state: str          # running | ok | failed | interrupted
    landed: bool
    cleared: bool       # a later turn has begun, so this ending stops holding the glass
    elapsed: float
    tools: list
    helpers: list


class Held(Protocol):
    """A command she deferred rather than blocked on, whose card turns up at the prompt minutes later.
    Two can be outstanding at once, so `holds` is a list and the oldest is read first."""

    state: str          # wait | ask | run | ok | fail | deny | late
    verb: str
    told: bool
    began: float
    elapsed: float


class Due(Protocol):
    """A reminder cron poured out with no turn around it."""

    msg: str
    when: str
    every: str
    told: bool


class Task(Protocol):
    order: int


class Plan(Protocol):
    is_open: bool
    total: int
    active_task: Task | None


class Parts(Protocol):
    """The row builders `flow_view` arranges but does not own. The region decides the order and where
    the blank rows go; what each row looks like belongs to the surface that also prints it."""

    seated_k: int

    def tool_text(self, tool: Running) -> Text: ...
    def roster_rows(self) -> list: ...
    def approval_rows(self) -> list: ...
    def seated_rows(self) -> list: ...
    def confirm_rows(self) -> list: ...
    def head_plate(self, live: bool = False) -> Text: ...
    def at_gutter(self, row): ...
    def prose(self, block: str): ...
    def link_rows(self, block: str) -> list: ...


@dataclass(frozen=True)
class State:
    """Everything the pinned footer reads, and nothing else."""

    phase: str = "live"                       # ringing | live | off
    boot_line: str = ""
    model: str = ""
    turn_start: float = 0.0
    armed_until: float = 0.0
    status: str = ""
    peek: str = ""
    partial: str = ""
    held: str = ""
    typing: str = ""
    queued: tuple[str, ...] = ()
    tools: tuple[Running, ...] = ()
    helpers: tuple[Running, ...] = ()
    holds: tuple[Held, ...] = ()
    due: tuple[Due, ...] = ()
    work: Job | None = None
    plan: Plan | None = None
    approval: object | None = None
    confirm: object | None = None
    said_plate: bool = False
    resumed: bool = False
    rows_committed: bool = False
    owes_gap: bool = False
    folded: bool = False
    at_rest: bool = False

    def hold_at(self, *states: str) -> Held | None:
        """The oldest deferred command in one of these states. Oldest first everywhere: two cards can be
        outstanding at once, and the one that has been waiting longest is the one whose window runs out
        first."""
        return next((h for h in self.holds if h.state in states), None)

    def hold_back(self) -> Held | None:
        """A deferred command that has finished one way or another and whose row has not landed yet. A
        denial is not one of these: the audit line the card collapsed to was the whole receipt."""
        return next((h for h in self.holds
                     if not h.told and h.state in ("ok", "fail", "late")), None)

    def due_now(self) -> Due | None:
        return next((d for d in self.due if not d.told), None)

    @property
    def speaking(self) -> str:
        """Everything of this reply that is not in the transcript yet: what she is still typing, plus
        whatever is being held back for her portrait."""
        if self.held and self.partial:
            return self.held + "\n\n" + self.partial
        return self.held or self.partial


@dataclass(frozen=True)
class Flow:
    """What the region holds above the frame, and the three marks the region needs off it. `owes_tail`
    says whether anything here will leave a blank row behind it when it lands — anything at all beyond
    a gap already owed does, and the region has to be holding that row before it needs it, or the
    terminal buys it by scrolling exactly as she stops typing."""

    rows: list
    her_at: int = -1
    tail_at: int = -1
    owes_tail: bool = False


def bar_kind(state: State) -> str:
    """Everything the turn is doing outranks anything out of turn: the turn is what you are waiting on,
    the rest is what happens while you are not. Below the turn and above the idle clause is the whole
    of the out-of-turn channel.

    Inside that band the order is who is waiting on whom. A card she cannot run without is first, a
    reminder second — both are `sun`, both are yours. Then the two things she is doing on her own,
    which are grape: the command you already okayed, then the long job."""
    if state.approval or state.confirm:
        return "wait"
    if any(h.state in ("running", "queued") for h in state.helpers):
        return "helpers"
    if state.peek:
        return "peek"
    if state.status == "thinking":
        return "think"
    if state.status:
        return "work"
    if state.partial:
        return "speak"
    if state.hold_at("ask"):
        return "card"
    if state.due_now():
        return "due"
    if state.hold_at("run"):
        return "okayed"
    if state.work and state.work.state == "running":
        return "long"
    if state.hold_back():
        return "okdone"
    if state.work and (not state.work.landed or work_held(state)):
        return "landed"
    if state.plan and state.plan.is_open and state.plan.active_task:
        return "plan"
    return "idle"


def plan_bar_twins(plan: Plan) -> tuple:
    """Out-of-turn bar phrase while an open plan has an active step. A still, and with no ellipsis on
    purpose: `bar_kind` can only reach "plan" with nothing running — every running state outranks it —
    so `step 1 of 8…` was always a motion claim about a plan nobody was on. That is exactly what a
    dead job's bar showed beside a LIVE chip, in the run that turned up the silent deaths. Position is the
    information the phrase carries; motion here would be a lie every time it is drawn."""
    active = plan.active_task
    if not active:
        return ()
    n, total = active.order, plan.total
    return (f"step {n} of {total}", f"{n}/{total}")


def out_twins(state: State, kind: str) -> tuple:
    """One phrase per out-of-turn state, with its short twins. Every twin of a state that MOVES carries
    the words its byte budget is measured against — `long job` for the job, `okayed` for the command —
    because a marker a 44-column terminal can shorten out of the picture cannot be measured at all."""
    if kind == "plan":
        return plan_bar_twins(state.plan) if state.plan else ()
    if kind in ("long", "landed"):
        return work_twins(state)
    if kind == "card":
        h = state.hold_at("ask")
        n = sum(1 for x in state.holds if x.state == "ask")
        if n > 1:
            return (f"{n} of hers are waiting on a yes from you",
                    f"{n} she needs a yes on", "she needs you")
        return ("one of hers is waiting on a yes from you",
                "she needs a yes on one", "she needs you") if h else ()
    if kind == "due":
        d = state.due_now()
        return (f"a reminder just came due — {d.msg}",
                f"a reminder — {d.msg}", "a reminder for you",
                "a reminder") if d else ()
    if kind == "okayed":
        h = state.hold_at("run")
        return (f"the one you okayed — {HELD_VERB.get(h.verb, 'on it…')}",
                "the one you okayed…", "okayed, running…") if h else ()
    if kind == "okdone":
        h = state.hold_back()
        if not h:
            return ()
        if h.state == "late":
            return ("that one ran out of time — I left it alone",
                    "it ran out of time", "left alone")
        if h.state == "fail":
            return ("that one didn't go through — say anything",
                    "that one didn't go through", "it didn't work")
        return ("that one's done — say anything and I'll show you",
                "that one's done", "done")
    return ()


def out_right(caps, state: State, kind: str, armed: bool = False) -> str:
    """The right slot, per state. It carries a clock only where there is something a clock is honest
    about — a thing running. A card that is waiting has no clock, because a countdown on a safety gate
    is a pressure the design does not want and a live state the budget cannot afford."""
    if kind == "plan":
        return ""
    if kind in ("long", "landed"):
        return work_right(caps, state, armed)
    if kind == "card":
        return caps.t(f"{caps.g['enter']} shows it")
    if kind == "due":
        d = state.due_now()
        return caps.t(d.every or d.when) if d else ""
    if kind == "okdone":
        h = state.hold_back()
        return duration(h.elapsed) if (h and h.began) else ""
    if kind == "okayed":
        h = state.hold_at("run")
        if not h:
            return ""
        clock = duration(h.elapsed)
        if armed:
            return clock
        hint = fit(caps.width - 34, caps.t("esc stops it"), "")
        return f"{hint}   {clock}".strip()
    return ""


#: The half-second the region drops to under `--calm` — `app._clock` sleeps on it through `frame_hz`,
#: `app._wake` folds to it through `beat_hz`, and a flash outlives one of it (`flash_secs`). An open
#: card used to drop here too; `frame_hz` says why it no longer does.
CARD_S = 0.5

#: The WORKING mark's swell — the band's own shape (`rows.BEAT_FRAMES`) in the chip's cell (`chip_dot`).
DOT_FRAMES = ("bullet", "ring", "dot", "ring")

#: A status light's blink per state that blinks: half on, half off, the prototype's own numbers.
#: `wait` is the slow one on purpose — calm at a safety gate. It was spelt `4 * CARD_S` while a card
#: was sampled at `CARD_S`; no surface samples it there now (`--calm` draws the chip solid), so the
#: number is the prototype's 2.0 and not a derivation from a frame length.
BLINK = {"speak": 0.7, "work": 0.9, "think": 0.9, "peek": 0.9,
         "helpers": 0.9, "wait": 2.0}


def frame_hz(caps, state: State) -> float:
    """How many times a second the in-turn region is sampled — `app._clock`'s sleep, inverted, and the
    ONE copy of that arithmetic, so the rate a mark is drawn at and the rate the surface repaints at
    are one number rather than two kept equal by hand. `CARD_S` under `--calm`; `caps.fps` otherwise,
    which is 4 over SSH.

    An open card used to drop to 2 Hz for calm's sake, and that throttle was the lag: at 2 Hz it cost
    4.5 kB/s and 1.4 % of a core while a key's reaction reached the glass in p50 240 ms / p90 410 ms,
    against 25 kB/s, 5-7 % of a core and 34-68 ms at the turn's own rate. `state` is TAKEN AND NOT
    READ, deliberately: the rate answers to the surface, and a card is not a slower surface."""
    return 1.0 / CARD_S if caps.reduced_motion else float(caps.fps)


def beat_hz(caps, state: State) -> float:
    """The rate a `rows.BEAT_HZ` mark is phased at on THIS surface — the lesser of the design speed
    and what the surface actually draws at (`frame_hz`) — and the one copy of that fold. The band's
    job mark (`pinned_view` in a turn, `app._band` at the prompt), the chip's swell (`chip_dot`) and
    the prompt's own wake (`app._wake`) all read it, so the rate a mark steps at and the rate the
    surface that shows it repaints at are one number. It had been three: the fold lived in
    `pinned_view` and in `chip_dot` as two `min()` calls, and `app._wake` kept `BEAT_HZ` bare with a
    second `0.5` for `--calm` — so over SSH, where the region draws at 4, the prompt woke at 6 around
    a chip phased at 4."""
    return min(float(BEAT_HZ), frame_hz(caps, state))


def chip_dot(caps, state: State, kind: str) -> str:
    """The chip's mark, computed once for both renderers: two copies of this arithmetic is how the two
    halves of one bar drift.

    While the long job is live the chip reads WORKING and its mark is MOTION — a four-frame swell at
    `beat_hz`, the fold to what the surface actually samples, since a 6 Hz phase read over SSH's 4
    plays the swell out of order. A blink sped up would be three flashes a second, a strobe at the
    general flash threshold. Every other state's mark is a STATUS light: a slow blink saying "this is
    alive", and speeding one up reports an emergency nobody is having. `wait` keeps its slow 2.0 even
    while the job is live — calm at a safety gate. Solid under `reduced_motion`, and at idle, where
    the surface never repaints and nothing may cost a byte."""
    if caps.reduced_motion:
        return caps.g["dot"]
    now = time.monotonic()
    if state.phase == "live" and kind != "wait" and work_is_live(state):
        return caps.g[DOT_FRAMES[int(now * beat_hz(caps, state)) % len(DOT_FRAMES)]]
    period = BLINK.get(kind, out_period(kind)) if state.phase == "live" else 1.0
    if period and (now % period) >= period / 2:
        return caps.g["ring"]
    return caps.g["dot"]


def out_period(kind: str) -> float:
    """The out-of-turn STATUS blinks — `okayed` is the one entry left; the long job's mark swells at
    `BEAT_HZ` instead, since the 2.0 here was sized against an out-of-turn repaint of twice a second
    that the prompt's own wake retired.

    `okayed` keeps the 2.0 rather than joining the swell because nothing would draw one: a hold never
    reaches `run` in this binary, and no clause wakes the prompt for it — a swell nothing samples parks
    mid-frame and reads as broken. If `run` ever gains a source it joins the swell AND brings its wake.
    Every other state is a still and costs nothing per second."""
    return 2.0 if kind == "okayed" else 0.0


#: How long a refused key's flash stays on the rail: long enough to read as a refusal, short enough
#: not to sit there. 0.45 s is the value the CLI was designed around and every surface derives from.
FLASH_S = 0.45


def flash_secs(caps, state: State) -> float:
    """The flash's span on THIS surface: the demo's `FLASH_S`, or one frame of the sampler plus a
    margin where a frame is longer than that. The port lengthened it to `CARD_S + 0.2` for every
    card, because a card was sampled at 2 Hz and a 0.45 s span could fall whole between two frames
    — a refused key swallowed with nothing on the glass. The card is sampled at the turn's rate since
    its throttle went, so that reason survives only under `--calm`, and the number is derived from the
    sampler rather than sized for a surface that no longer exists."""
    return max(FLASH_S, 1.0 / frame_hz(caps, state) + 0.2)


def work_twins(state: State) -> tuple:
    """Her phrase for the long job, with its short twins, and every one carries the words `long job` —
    the marker the pulse's byte budget is measured against, so a narrow terminal cannot shorten the
    measurement out of the picture. The verb is `work_verb`'s, the band's own: a phrase latched at a
    step's START outlived the step on both surfaces at once.

    The last running twin has no ellipsis on purpose: folded to ASCII, `…` becomes three cells, which at
    exactly 44 columns emptied the phrase and took the marker with it. While helpers are out the phrase
    becomes the COUNT and where to read them, in place of the verb — a still, since it is a function of
    the line-up's states and of nothing that ticks — and it is gone the moment none is out."""
    w = state.work
    if not w or (w.landed and not work_held(state)):
        return ()
    if w.state == "running":
        out = sum(1 for h in w.helpers if h.state in ("running", "queued"))
        if out:
            back = len(w.helpers) - out
            said = _count(out, "helper") + " out" + (f", {back} back" if back else "")
            return (f"the long job — {said} · /helpers shows them",
                    f"the long job — {said} · /helpers", f"the long job — {said}",
                    f"long job — {out} out", "long job…", "long job")
        return (f"the long job — {work_verb(w)}",
                "the long job…", "long job…", "long job")
    return WORK_ENDED_SAID[w.state]


def armed_twins(state: State) -> tuple:
    """`esc` names the blast radius before it happens: the long job, a command you okayed that is still
    running, or the line-up. A card still waiting is not in the radius — you decline that one with `n`.

    Every size has its own sentence, and that is not tidiness. The arm is set once, on a press with more
    than one thing out there (`cli/app._typeahead`), and then this is recomputed on every frame for the
    next three seconds — during which the things it counts LAND. The plural sentence answered the whole
    window, so the count the arm exists to state ran down through `that stops all 1 of them` to `that
    stops all 0 of them`, which is a blast radius of nothing offered as a warning."""
    n = (sum(1 for h in state.helpers if h.state in ("running", "queued"))
         + sum(1 for x in state.tools if x.state == "running"))
    if n > 1:
        return (f"esc again — that stops all {n} of them",
                f"esc again stops all {n}", "esc again stops all")
    if n == 1:
        return ("esc again — that stops the one still running",
                "esc again stops it", "esc again stops it")
    job = bool(state.work and state.work.state == "running")
    cmd = bool(state.hold_at("run"))
    if job and cmd:
        return ("esc again — that stops the job and the command",
                "esc again stops both", "esc again stops both")
    if cmd:
        return ("esc again — that stops the one you okayed",
                "esc again stops it", "esc again stops it")
    if job:
        return ("esc again — that stops the long job",
                "esc again stops the job", "esc again stops it")
    return ("esc again — that stops this turn",
            "esc again stops the turn", "esc again stops it")


def work_right(caps, state: State, armed: bool = False) -> str:
    """The right slot while the long job is up: the gesture that stops it, and its clock. The hint costs
    no row, which is exactly what the right slot is for.

    WHICH gesture depends on who is holding the keyboard, and this is the bar that both surfaces draw.
    Inside a turn the CLI owns stdin and `esc` reaches the typeahead. Out at the prompt prompt_toolkit
    owns it and binds `escape` in two combinations only — `escape enter` for a newline and `escape` to
    close the completion list — so nothing there stops anything, and `/stop` is the route.
    One string for both said `esc stops it` at a prompt where the key does nothing, which is the whole
    long job's only surface telling the person to press something inert."""
    w = state.work
    if not w or w.landed:
        return ""
    clock = duration(w.elapsed)
    if armed or w.state != "running":
        return clock
    hint = fit(caps.width - 34, caps.t("/stop stops it" if state.at_rest else "esc stops it"), "")
    return f"{hint}   {clock}".strip()


def bar_text(caps, face, state: State) -> Text:
    """The bar itself. Its mark is `chip_dot`'s, one function for both renderers, and her face is drawn
    still under `--calm` — that flag promises nothing moves.

    The fourth chip label, `WORKING`, is a departure and not a restoration: while a long job ran, the
    loudest device in the bar announced `LIVE` while the one thing the reader wanted sat in the dim
    phrase beside it. It reads off `work_is_live`, so the chip cannot be working while the phrase says
    the job landed. The phrase's room is measured against the prefix and right slot actually built,
    never a fixed reserve: the old constant 34 was short by eight below 84 columns, the slot running to
    19 cells. When the slot is too wide the clock is the fallback and not the first casualty — it is the
    one thing that proves she is not stuck."""
    now = time.monotonic()
    kind = bar_kind(state)
    bar = Safe()
    label = {"ringing": "RINGING", "live": "LIVE", "off": "OFFLINE"}[state.phase]
    slot = {"ringing": "sun", "live": "live", "off": "ink"}[state.phase]
    if state.phase == "live" and work_is_live(state):
        label, slot = "WORKING", "grape"
    dot = chip_dot(caps, state, kind)
    if caps.color == "none" and not caps.interactive:
        bar.append(f"[{label}]")
    else:
        bar.append_text(plate(caps, f"{dot} {label}", slot))
    bar.append("  ")
    bar.append(face.render(still=caps.reduced_motion), style=face.style)
    bar.append("  ")
    el = now - state.turn_start if state.turn_start else 0.0
    clock = f"{el:4.1f}s" if (kind != "idle" and el > 1.0) else ""
    verbs = ""
    if time.monotonic() < state.armed_until:
        verbs = ""
    elif kind == "helpers":
        verbs = fit(caps.width - bar.cell_len - 26,
                    caps.t("tab peek   esc stops all"),
                    caps.t("tab peek  esc stop"), caps.t("tab · esc"))
    elif kind != "idle" and el > 10.0 and kind != "wait":
        verbs = fit(caps.width - bar.cell_len - 12,
                    caps.t(f"esc {caps.g['arrow']} stop"), caps.t("esc stop"), "")
    right = f"{verbs}   {clock}".strip() if (verbs and clock) else (
        verbs or clock or (state.model if kind == "idle" else ""))
    armed = time.monotonic() < state.armed_until
    if kind in OUT_KINDS:
        right = out_right(caps, state, kind, armed)
    right = scrub(right)
    phrase = _bar_phrase(caps, state, kind, el, armed,
                         caps.width - bar.cell_len - cell_len(right) - 3)
    if phrase:
        if (armed or kind in SUN_KINDS) and caps.color != "none":
            bar.append(" " + phrase + " ", style="chip.sun")
        else:
            bar.append(phrase, style="chrome")
    if right:
        room = caps.width - bar.cell_len - 1
        bare = out_right(caps, state, kind, True) if kind in OUT_KINDS else clock
        right = right if cell_len(right) <= room else fit(room, bare, "")
    if right:
        pad = caps.width - bar.cell_len - cell_len(right)
        bar.append(" " * max(1, pad) + right, style="chrome")
    return bar


def _bar_phrase(caps, state: State, kind: str, el: float, armed: bool = False,
                room: int | None = None) -> str:
    """Short twins all the way down — a phrase that shortens reads as a decision, a phrase that
    truncates reads as a bug. `room` is measured by the caller against the prefix and right slot it
    actually built; the fallback is only for a caller with neither."""
    t = caps.t
    room = caps.width - 34 if room is None else max(0, room)
    if armed:
        return fit(room, *[t(v) for v in armed_twins(state)])
    if kind in OUT_KINDS:
        return fit(room, *[t(scrub(v)) for v in out_twins(state, kind)], "")
    if state.phase == "ringing":
        return t(state.boot_line)
    if kind == "wait":
        return fit(room, t("waiting on you"), t("waiting"))
    if kind == "helpers":
        if state.folded or caps.height < FOLD_ROWS:
            return ""
        n = sum(1 for h in state.helpers if h.state in ("running", "queued"))
        return fit(room, t(f"{n} on stage"), t(f"{n} up"))
    if kind == "peek":
        return fit(room, *[t(scrub(v)) for v in
                           PEEK.get(state.peek, (state.peek + "…",))], "")
    if kind == "think":
        return fit(room, t("thinking…"), "")
    if kind == "work":
        if el > 4.0:
            beat = int((el - 4.0) // 3.5) % len(HEARTBEATS)
            return fit(room, t(HEARTBEATS[beat]), t("working…"), "")
        return fit(room, t("focused — working…"), t("working…"), "")
    if kind == "speak":
        return ""
    return fit(room, t("you're connected — just type"), t("just type"), "")


def flow_view(caps, state: State, parts: Parts) -> Flow:
    """What the turn is doing, drawn downward from the row it already sits on, so nothing moves at commit.

    The order of a machine row against her live block is not a preference: `Screen.row` FLUSHES what she
    has staged before it prints, so a staged opening block commits ABOVE the tool about to land, and
    once she HAS a plate the tool lands above what she is only typing. That is also the only arrangement
    in which her block sits on the row it will keep, and so the only one in which her portrait may be
    painted before it lands. The line-up is drawn last of all, or her closing paragraph drops a row onto
    it. Her block's owed blank is held here, or the terminal buys it by scrolling exactly as the tool
    lands; a URL of hers goes in the TAIL, since a row drawn inside the height her art claims would drop
    two rows the moment she stopped typing."""
    her_at = tail_at = -1
    rows: list = [Safe("")] if state.owes_gap else []
    running = [parts.tool_text(t) for t in state.tools if t.state == "running"]
    roster = parts.roster_rows()
    carded = bool(state.approval or state.confirm)
    if carded or not state.speaking:
        rows += running + roster
    if state.approval:
        rows += parts.approval_rows()
    elif state.confirm:
        rows += parts.confirm_rows()
    elif state.speaking and not state.said_plate and not state.resumed:
        if state.rows_committed:
            rows.append(Safe(""))
        her_at = len(rows)
        rows.append(parts.at_gutter(parts.head_plate(live=True)))
        rows.append(parts.prose(state.speaking))
        tail_at = len(rows)
        rows += parts.link_rows(state.speaking)
        if running or roster:
            rows.append(Safe(""))
            rows += running + roster
    elif state.speaking:
        rows += running
        if not state.said_plate:
            # Her voice resuming after machine rows: the plate it will commit with, no portrait.
            rows.append(Safe(""))
            rows.append(parts.at_gutter(parts.head_plate(live=True)))
        rows.append(parts.prose(state.speaking))
        tail_at = len(rows)
        rows += parts.link_rows(state.speaking)
        if roster:
            rows.append(Safe(""))
            rows += roster
    if state.queued and tail_at < 0:
        tail_at = len(rows)
    for q in state.queued:
        rows.append(Safe.assemble(
            (caps.g["prompt"] + " ", "coral"), (q, "chrome"),
            (f"   {caps.g['enter']} queued", "chrome")))
    return Flow(rows, her_at, tail_at, len(rows) > bool(state.owes_gap))


def pinned_view(caps, face, state: State, *, spin=None) -> list:
    """The band, the frame and the bar, always the last rows of the window. The band comes off the
    one builder both surfaces share (`rows.band_rows`) and rides the pinned block — never `flow` —
    because its rows must never land in the transcript; `LiveView` pays for the extra height out of
    the pad. `spin` is the region's, so row one's mark pulses in a turn and only there.

    The band is handed the rate THIS surface is sampled at (`beat_hz`): a `BEAT_HZ` phase read at
    2 Hz — `--calm`, and an open card until its throttle went — advances three frames per draw, the
    stutter reported live. The sampler otherwise
    is `caps.fps`, which is 4 over SSH — slower than the beat, the same tear — so the rate is the
    lesser of the design speed and whatever this surface actually draws at, never `BEAT_HZ` bare."""
    hz = beat_hz(caps, state)
    return [*band_rows(caps, state, frame_w(caps), spin=spin, hz=hz),
            *box_rows(caps, state), bar_text(caps, face, state)]


def frame_w(caps) -> int:
    """The width the band, the frame and the completion list are drawn at: one cell short of the
    window, which the shadow column takes, floored where a box stops being one. One reading for the
    three surfaces that hand the frame to each other, so no two of them can be a cell apart."""
    return max(20, caps.width - 1)


def box_rows(caps, state: State) -> list[Text]:
    """The same frame prompt_toolkit draws, drawn by us, so the input never blinks out for the length
    of a turn. Cells and colours match exactly — heavy box in her coral, one shadow column — because
    the two renderers hand the footer to each other and a seam would show.

    While something is typed the text gets one cell less than the hint does: the caret is a cell too,
    and spending the closing border's cell on it is what made a full line one wider than the box
    around it."""
    g, box = caps.g, (HEAVY if caps.unicode else PLAIN_BOX)
    w = frame_w(caps)
    body = Safe()
    body.append(box["│"], style="coral")
    body.append(g["prompt"] + " ", style="coral")
    room = w - 4
    if state.typing:
        typed = room - 1
        shown = scrub(state.typing)[-typed:]
        while cell_len(shown) > typed:
            shown = shown[1:]
        body.append(shown)
        body.append(g["caret"], style="chrome")
        hint = caps.t(f"{g['enter']} sends when she's done")
        gap = w - 1 - body.cell_len - cell_len(hint)
        if gap > 2:
            body.append(" " * gap + hint, style="chrome")
    else:
        body.append(box_hint(caps, state, room), style="chrome")
    body.append(" " * max(0, w - 1 - body.cell_len))
    body.append(box["│"], style="coral")
    edge = box["─"] * (w - 2)
    rows = [Safe(box["┌"] + edge + box["┐"], style="coral"), body,
            Safe(box["└"] + edge + box["┘"], style="coral")]
    for r in rows:
        r.append(g["shadow"], style="shadow.coral")
    return rows


def menu_rows(caps, items: list, index, tall: int) -> list[Text]:
    """The completion list, in the frame the input already lives in: a top border and one row per
    match, so the box simply reads as taller while it is open. Same cells and same coral as box_rows —
    these are painted over the transcript and prompt_toolkit never sees them."""
    box = HEAVY if caps.unicode else PLAIN_BOX
    w = frame_w(caps)
    first = window(index or 0, len(items), tall)
    show = items[first:first + tall]
    labels = [(scrub(caps.t(c.display_text)), scrub(caps.t(c.display_meta_text))) for c in show]
    lead = min(30, max(cell_len(text) for text, _ in labels) + 3)
    rest = len(items) - first - len(show)
    rows = [Safe(box["┌"] + box["─"] * (w - 2) + box["┐"], style="coral")]
    for n, (text, meta) in enumerate(labels):
        body = Safe()
        label = " " + text
        body.append(label + " " * max(1, lead - cell_len(label)),
                    style="chip.coral" if first + n == index else "hard")
        if meta:
            body.append(" " + meta, style="chrome")
        if rest and n == len(show) - 1:
            body.append(caps.t(f"   … {rest} more"), style="chrome")
        body.truncate(w - 3, overflow="ellipsis")
        row = Safe()
        row.append(box["│"], style="coral")
        row.append_text(body)
        row.append(" " * max(0, w - 1 - row.cell_len))
        row.append(box["│"], style="coral")
        rows.append(row)
    for r in rows:
        r.append(caps.g["shadow"], style="shadow.coral")
    return rows


def card_keys(card) -> str:
    """The keys THIS card drew, in the words the card uses for them itself.

    The rail is built out of `can_always` and `can_always_exact` — `a` is the family grant, `t` this
    line alone — and the backend withholds either on its own. Restating that as a constant was wrong in
    both directions at once: it named `a` over every dangerous command, where both grants are refused,
    and dropped `t` over every ordinary one, where the rail draws it. A gate that tells the person to
    press a key it never drew teaches them to distrust the rail.

    Both flags are read through their `Approval` field defaults, so a stand-in with neither is described
    as the plain card it stands in for rather than raising under the frame."""
    keys = ["y"]
    if getattr(card, "can_always", True):
        keys.append("a")
    if getattr(card, "can_always_exact", False):
        keys.append("t")
    keys += ["n", "?"]
    return ", ".join(keys[:-1]) + " or " + keys[-1]


def box_hint(caps, state: State, room: int) -> str:
    t = caps.t
    if state.approval:
        keys = card_keys(state.approval)
        return fit(room, t(f"the card above is waiting — {keys}"), t(keys), "")
    if state.confirm:
        return fit(room, t("the card above is waiting — y or n"),
                   t("y or n"), "")
    if state.at_rest:
        return fit(room, t("talk to her · @ a file · / for commands"),
                   t("talk to her · / for commands"), t("talk to her"), "")
    return fit(room, t("type while she works — enter queues it, esc stops"),
               t("enter queues it, esc stops"), t("esc stops"), "")


def plate(caps, label: str, slot: str) -> Text:
    """A chip and the one cell of hard shadow that makes it read as a sticker."""
    out = Safe()
    out.append(f" {label} ", style=f"chip.{slot}")
    if caps.color != "none":
        out.append(caps.g["shadow"], style=f"shadow.{slot}")
    return out
