"""Per-session task list — in-memory, one list per session.

The model creates or replaces a list via the todo tool and updates steps on later calls. The UI
renders from the last emitted frame; the REST endpoint lets a renderer that connects mid-turn
rehydrate from one GET instead of waiting for the next mutation.

One list per session, but a session can be running TWO agentic loops at once: start_work detaches a
background job that keeps the session id, and the companion turn goes on beside it. Both read the same
prompt_note asking them to tick and close, so `owner_run` names the run the list belongs to — while it
is set only that run may settle or replace it. Unowned lists behave as before."""
from __future__ import annotations

import time
import uuid

_MAX_STEPS = 20
_MAX_STEP_CHARS = 200
_MAX_DETAIL_CHARS = 600

_state: dict[str, dict] = {}
# session_id -> the run id of the background job running in it, for as long as one is (see bind_run).
_job_run: dict[str, str] = {}


def _now() -> float:
    return time.monotonic()


def _split_step(step) -> tuple[str, str]:
    """A step is either a plain title or {"text", "detail"} — the detail being her own working notes
    for that step, which prompt_note hands back so a long session can resume without re-deriving it."""
    if isinstance(step, dict):
        return str(step.get("text") or "")[:_MAX_STEP_CHARS], str(step.get("detail") or "")[:_MAX_DETAIL_CHARS]
    return str(step)[:_MAX_STEP_CHARS], ""


def same_steps(session_id: str, steps: list) -> bool:
    """Is `steps` the list this session already has, step for step and in the same order?

    Re-sending the steps she already sent is not a new plan, and treating it as one is destructive: the
    marks live on the OLD list_id, so `todo(steps=[…], done=[1,2])` opened a fresh list and applied the
    ticks to that — and a later resend after `close` replaced the closed list with an open one showing
    1/2 on a job that was over. Measured live: five todo calls in one turn, three of
    them carrying steps, and the plan ended open at 1 of 2 with both steps really done.

    Titles are excluded on purpose — the same steps under a reworded title is still the same plan, and
    the title is the one part she re-derives freely."""
    lst = _state.get(session_id)
    if lst is None:
        return False
    incoming = [_split_step(s)[0] for s in (steps or [])[:_MAX_STEPS]]
    return incoming == [t["text"] for t in lst["tasks"]]


def held_from(session_id: str, run_id: str) -> bool:
    """Does the open list belong to a run OTHER than `run_id`? Then this caller may not touch it.

    False whenever there is no list, the list is not open, or nobody has claimed it — an ordinary plan
    stays writable by whoever is holding the session."""
    lst = _state.get(session_id)
    if lst is None or lst["status"] != "open":
        return False
    own = lst.get("owner_run") or ""
    return bool(own) and own != (run_id or "")


def bind_run(session_id: str, run_id: str) -> str:
    """`run_id` is now the session's background job: the plan is its plan. Returns the owner, or "".

    A plan she writes in the breath before start_work IS that job's plan — the runner has always read it
    that way. What nothing recorded is that the plan then had no owner: the companion turn kept running
    in the same session, kept being handed the same "tick it, close it when the job is over" note, and
    could settle a plan describing work that had barely started.

    The job is remembered as well as the list, because a plan she opens mid-job is the job's too and
    there is nothing to claim at launch time — open_list claims it when it appears."""
    if not run_id:
        return ""
    _job_run[session_id] = run_id
    lst = _state.get(session_id)
    if lst is None or lst["status"] != "open":
        return ""
    lst["owner_run"] = run_id
    return run_id


def release_run(session_id: str, run_id: str) -> None:
    """Give the list back when that job ends. Held forever, no later turn could tick or replace it."""
    if not run_id:
        return
    if _job_run.get(session_id) == run_id:
        _job_run.pop(session_id, None)
    lst = _state.get(session_id)
    if lst is not None and (lst.get("owner_run") or "") == run_id:
        lst["owner_run"] = ""


def open_list(session_id: str, steps: list, title: str = "", by_run: str = "") -> dict:
    """Create a new list for the session, DISCARDING any open one.

    One list per session, so a replaced list is gone, not archived — marking it abandoned first only wrote
    to an object nobody could read again. If a replaced plan should survive on screen, that needs a real
    history, not a status flag on a dropped dict.

    A list owned by another run is not replaced: the existing one comes back with `refused_run` set. A
    list the background job opens for itself is born owned by it, the same as one claimed at launch."""
    if held_from(session_id, by_run):
        held = _state[session_id]
        held["refused_run"] = held["owner_run"]
        return held
    list_id = uuid.uuid4().hex[:12]
    capped = steps[:_MAX_STEPS]
    tasks = []
    for i, s in enumerate(capped, start=1):
        text, detail = _split_step(s)
        tasks.append({"id": f"{list_id}:{i}", "text": text, "detail": detail,
                      "status": "pending", "order": i})
    now = _now()
    lst: dict = {
        "list_id": list_id,
        "title": (title or "")[:200],
        "tasks": tasks,
        "status": "open",
        "origin": session_id,
        "created_ts": now,
        "updated_ts": now,
        "rev": 1,
        "owner_run": by_run if by_run and _job_run.get(session_id) == by_run else "",
        "refused_run": "",
    }
    _state[session_id] = lst
    return lst


def update(
    session_id: str,
    done: list[int] | None = None,
    active: int | None = None,
    drop: list[int] | None = None,
    close: bool = False,
    by_run: str = "",
) -> dict | None:
    """Mutate the open list. All params are optional and idempotent.

    done/drop take 1-based indices and are no-ops on already-settled steps; at most one step may be
    active, and the previous one reverts to pending; close ends the list, 'done' only when every step
    is settled and 'abandoned' otherwise.

    Every index it could not apply goes into lst["ignored"], so the caller tells the model instead of
    reporting a clean success — a silently dropped index reads as done and the step never gets made. A
    caller that is not the list's owner moves nothing and gets `refused_run`: the reason is state, not
    judgement, so it is enforced here rather than at the tool."""
    lst = _state.get(session_id)
    if lst is None or lst["status"] != "open":
        return lst
    lst["refused_run"] = ""
    if held_from(session_id, by_run):
        lst["refused_run"] = lst["owner_run"]
        return lst

    tasks = lst["tasks"]
    changed = False
    ignored: list[str] = []
    lst["ignored"] = ignored

    def _by_order(i: int):
        return next((t for t in tasks if t["order"] == i), None)

    for i in done or []:
        t = _by_order(i)
        if t is None:
            ignored.append(f"done={i}")
        elif t["status"] not in ("done", "dropped"):
            t["status"] = "done"
            changed = True

    for i in drop or []:
        t = _by_order(i)
        if t is None:
            ignored.append(f"drop={i}")
        elif t["status"] not in ("done", "dropped"):
            t["status"] = "dropped"
            changed = True

    if active is not None:
        # Resolve the target BEFORE clearing the old marker. Clearing first means an out-of-range or
        # already-settled `active` demotes the running step and sets nothing: the plan loses its marker
        # (the panel falls back to idle mid-plan) while the call still looks like it worked.
        target = _by_order(active)
        if target is None or target["status"] not in ("pending", "active"):
            ignored.append(f"active={active}")
        else:
            for t in tasks:
                if t["status"] == "active" and t is not target:
                    t["status"] = "pending"
                    changed = True
            if target["status"] != "active":
                target["status"] = "active"
                changed = True

    if close:
        # Closing on top of unmarked steps is 'abandoned', not 'done': a list that reads as finished
        # while showing 1/3 is the exact lie on screen the panel exists to prevent.
        outstanding = any(t["status"] in ("pending", "active") for t in lst["tasks"])
        lst["status"] = "abandoned" if outstanding else "done"
        changed = True

    if changed:
        lst["rev"] += 1
        lst["updated_ts"] = _now()

    return lst


def get(session_id: str) -> dict | None:
    return _state.get(session_id)


def clear(session_id: str) -> None:
    _state.pop(session_id, None)


def frame(session_id: str) -> dict | None:
    """The payload sent over SSE and returned by the REST endpoint. Internal fields excluded.

    `detail` stays behind: the schema tells her the user does not read her notes, and the panel does not
    render them — so they have no reason to leave the backend. She gets them back via prompt_note(), and
    the `todo` tool reads get() directly when it composes its own reply. The CLI is not an in-process
    reader: it renders the emitted frame like every other client, so it never sees the notes either."""
    lst = _state.get(session_id)
    if lst is None:
        return None
    return {
        "list_id": lst["list_id"],
        "title": lst["title"],
        "tasks": [{k: v for k, v in t.items() if k != "detail"} for t in lst["tasks"]],
        "status": lst["status"],
        "rev": lst["rev"],
    }


def revision(session_id: str) -> tuple[str, int] | None:
    """Identity of the list prompt_note would describe: (list_id, rev), or None when there is none open.

    A caller that keeps the note in a long-running context needs to know when the note it already sent
    has gone stale, and comparing two rendered strings is not the same question — `rev` moves on every
    applied mutation and `list_id` on every replacement, so this pair changes exactly when the note does.
    """
    lst = _state.get(session_id)
    if lst is None or lst["status"] != "open":
        return None
    return (lst["list_id"], lst["rev"])


def prompt_note(session_id: str) -> str:
    """One bounded line injected by core/loop._refresh_plan_note so she can resume or close the list.

    The tail sentence used to read "keep working it, tick steps done as you go" — a description of a
    habit, addressed to a model that is mid-job and about to answer. The state it never named is the one
    that actually happens: steps that are ALREADY finished and still show pending. So it now asks for
    that in the imperative, and says to batch it into one call, because the alternative reading — a todo
    call after every step — buys the same tick for one round trip per step."""
    lst = _state.get(session_id)
    if lst is None or lst["status"] != "open":
        return ""
    outstanding = [t for t in lst["tasks"] if t["status"] in ("pending", "active")]
    if not outstanding:
        return (
            f"[OPEN TASK LIST '{lst['title'] or lst['list_id']}'] All steps are marked. "
            "Call todo(close=true) to close it."
        )
    shown = outstanding[:10]
    # Each step comes back with the notes she wrote for it: this note IS her working memory across a
    # long session, and titles alone make her re-derive the how every time she picks the thread up.
    items = "; ".join(
        f"step {t['order']} ({t['status']}): {t['text']}" + (f" — notes: {t['detail']}" if t.get("detail") else "")
        for t in shown
    )
    tail = f" … and {len(outstanding) - 10} more" if len(outstanding) > 10 else ""
    done_n = sum(1 for t in lst["tasks"] if t["status"] == "done")
    return (
        f"[YOUR OPEN TASK LIST '{lst['title'] or lst['list_id']}' — {done_n}/{len(lst['tasks'])} done] "
        f"{len(outstanding)} step(s) still unmarked: {items}{tail}. "
        "This is your own plan and the user is watching it on screen. If any of those steps is ALREADY "
        "finished, mark it NOW — todo(done=[N,M]) with every finished step in ONE call, plus "
        "active=<the one you're on>, and todo(close=true) once the job is over. Leaving a finished step "
        "unmarked shows them a plan that says nothing got done. Never mark a step you have not done, "
        "never resend `steps`, and if nothing has moved since your last todo call, don't call it again "
        "— get on with the work."
    )
