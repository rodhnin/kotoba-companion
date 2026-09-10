"""What one turn accumulates: eight small records, none of which knows how to draw itself.

Each is fed by one family of frames, and the field names are not ours — live instruments read them by
name. `Tool`, `Helper` and `Work` carry the WIRE's own states: running · pending · ok · failed ·
refused · interrupted, plus `unknown` for a word a later backend invents; a done-frame marked `pending`
has not run at all and `refused` never ran, so a two-state vocabulary reports things that never
happened. `Held` keeps its own words (wait · ask · run · ok · fail · deny · late) — a different axis,
one deferred command around an approval window, where `deny` has no row to draw. Elapsed is clocked
HERE: no progress events, no timestamps on the wire, and no heartbeats at all on a reasoning model.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

# Both ends of the real one give it this: `interaction.TEXT_APPROVAL_TIMEOUT`, `deferred_exec`. The knob
# exists so a test can drive a card all the way to its lapse without waiting three real minutes for it.
HOLD_SECONDS = float(os.environ.get("KOTOBA_HOLD_S") or 180.0)


@dataclass
class Tool:
    """One action she took. `detail` is the phrase that fits beside the chip — `7 results`, `exit 1`;
    `full` is the whole output, kept because the row cannot hold it and something has to. Whole now
    means whole: the done frame carries the trimmed line AND the output the loop trims it out of,
    capped where the model's own copy is capped and saying so when it clips.

    `note` takes the clock's place when the gate let it run under a family granted long ago: a
    duration cannot say that, and nothing else on the row would."""

    verb: str
    arg: str
    started: float = field(default_factory=time.monotonic)
    state: str = "running"
    detail: str = ""
    full: str = ""
    stopped: float = 0.0
    note: str = ""

    @property
    def elapsed(self) -> float:
        return (self.stopped or time.monotonic()) - self.started


@dataclass
class Helper:
    """A delegated agent. `role` is the `toolset` the helper was actually restricted to, and it comes off
    the wire now — `subagent_spawned` carries it beside the id and the goal, so the name on the nameplate
    is hers and not the same invented `helper` on every row.

    `steps` are pairs, `(text, ok)`. `ok` is None on the line that says what she is ABOUT to do and
    True/False on the line that reports how it went; without it the two are indistinguishable strings and
    the only trace of a failure is the `  ! ` prefix `_result_text` writes, which is a renderer sniffing
    text for an outcome the wire can simply state."""

    sid: str
    role: str
    goal: str
    state: str = "queued"
    steps: list = field(default_factory=list)
    summary: str = ""
    started: float = 0.0
    stopped: float = 0.0

    @property
    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        return (self.stopped or time.monotonic()) - self.started


@dataclass
class Work:
    """The long job. `start_work` returns instantly and a runner then owns the session for up to
    `work_timeout` seconds with no turn around it, so all of this happens while the person sits at the
    prompt, and `tools` and `helpers` are the whole of the backend's progress: every surface DERIVES
    its phrase from them, or a four-second search still reads `looking that up…` ninety seconds later.

    `n` is the ordinal in THIS session and the backend cannot supply it — starting a job reassigns the
    session's whole dict, so the second erases the first one's summary and files, and it is on every
    row from the first job on because scrollback cannot be repainted. `summary` is what the backend
    wrote down and `said` is what she makes of it; `run_id` is the detached run's origin mark, which
    lets a handler bank its rows on the job while a turn is speaking. Empty from an older producer."""

    goal: str
    n: int = 1
    run_id: str = ""
    t0: float = field(default_factory=time.monotonic)
    state: str = "running"
    tools: list = field(default_factory=list)
    helpers: list = field(default_factory=list)
    summary: str = ""
    said: str = ""
    stopped: float = 0.0
    landed: bool = False
    #: The surfaces have shown that this job ended and the next turn has begun. Distinct from `landed`,
    #: which is about the transcript: the receipt prints, the band paints over it, and at rest the
    #: prompt never ends to repaint it, so the band carries the ending until there is news.
    cleared: bool = False
    gift_ns: list = field(default_factory=list)
    plans: list = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        return (self.stopped or time.monotonic()) - self.t0


@dataclass
class Due:
    """A reminder that came due. `cron_loop` emits `reminder` to a client with no turn open AND stashes
    the text so the next turn voices it in her own words. Both halves are real, so `told` tracks the row
    and `voiced` tracks the sentence."""

    rid: str
    msg: str
    when: str
    every: str = ""
    told: bool = False
    voiced: bool = False


@dataclass
class Held:
    """A command she asked about that nobody could answer at the time, so the card opens with the person
    at the prompt, minutes later, and each carries its own `request_id` so two can be outstanding at once.

    The axis is the TRANSPORT, never the mode: `shell`/`execute_code` defer only while an ElevenLabs
    agent holds the turn's clock, which in the terminal is never true. So a Held here is never a
    deferred command — it is the long job's card, opened with no turn around it, or a card the turn
    ended over."""

    verb: str
    cmd: str
    danger: str
    family: str
    intent: str
    blast: tuple
    took: float
    ok: bool
    detail: str
    rid: str = ""
    state: str = "wait"
    asked: float = 0.0
    began: float = 0.0
    stopped: float = 0.0
    told: bool = False
    can_always: bool = True
    can_always_exact: bool = False
    always_note: str = ""

    @property
    def elapsed(self) -> float:
        if not self.began:
            return 0.0
        return (self.stopped or time.monotonic()) - self.began

    @property
    def left(self) -> float:
        return max(0.0, self.asked + HOLD_SECONDS - time.monotonic())


@dataclass
class Gift:
    """Something handed over: a report, a file she kept, a link, an attachment, a reminder. `tail`
    replaces the `/open N` on the right, which is how a due row carries its clock there instead."""

    kind: str
    target: str
    note: str
    tail: str = ""


@dataclass(frozen=True)
class PlanStep:
    order: int


@dataclass
class Plan:
    """The task list, as both surfaces read it: the rows take the raw `frame` and the bar takes
    `is_open`/`total`/`active_task`. One wire frame, two readers.

    Counted at arrival, never on demand: what tells one frame from the next is how many steps were done
    WHEN IT LANDED, and a property would answer off whatever the frame says later."""

    frame: dict
    list_id: str = ""
    is_open: bool = False
    total: int = 0
    done: int = 0
    active_task: PlanStep | None = None

    def __post_init__(self) -> None:
        tasks = sorted(self.frame.get("tasks") or [], key=lambda t: t.get("order", 0))
        self.list_id = str(self.frame.get("list_id") or "")
        self.is_open = self.frame.get("status") == "open"
        self.total = len(tasks)
        self.done = sum(1 for t in tasks if t.get("status") == "done")
        step = next((t for t in tasks if t.get("status") == "active"), None)
        self.active_task = PlanStep(int(step.get("order") or 0)) if step else None
