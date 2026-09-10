"""Per-session background-work status, decoupled from the voice turn.

ONE work item per session at a time. The runner writes status as it progresses; the voice turn reads
prompt_note() to report progress and announce the result exactly once.

`acted` witnesses whether anything ever reached the user's SCREEN, written by whatever DREW a row and
never by the model's account of itself: she told the user to watch the screen for a job that ended in
4.7 s having drawn nothing. `el_call_bound` is the job's TRANSPORT, captured at start and never
re-read. `run_id` keys END-STATE writes to the run that owns the record, or a cancelled runner's late
teardown wipes the job that replaced it — a caller with no run_id still writes unconditionally."""
from __future__ import annotations

# The turn a surface fires so a finished background job is REPORTED instead of
# silently ending. Dropped before the model sees it.
WORK_DONE = "__work_done__"

import asyncio
import time

# session_id -> {status, goal, step, summary, files, reason, announced, acted, el_call_bound, run_id, ts}
_state: dict[str, dict] = {}
# session_id -> the asyncio.Task running that session's work-runner (so it survives the turn + is cancelable)
_tasks: dict[str, asyncio.Task] = {}


def _idle() -> dict:
    return {"status": "idle", "goal": "", "step": "", "summary": "", "files": [], "reason": "",
            "announced": True, "acted": False, "el_call_bound": False, "run_id": "", "ts": 0.0}


def get(session_id: str) -> dict:
    return _state.get(session_id, _idle())


def is_running(session_id: str) -> bool:
    return get(session_id)["status"] == "running"


def start(session_id: str, goal: str, *, el_call_bound: bool = False, run_id: str = "") -> None:
    _state[session_id] = {"status": "running", "goal": goal, "step": "starting…", "summary": "",
                          "files": [], "reason": "", "announced": True, "acted": False,
                          "el_call_bound": bool(el_call_bound), "run_id": run_id,
                          "ts": time.monotonic()}


def _another_run_owns(session_id: str, run_id: str) -> bool:
    """See the module docstring: a run-keyed ending may only close its own record."""
    if not run_id:
        return False
    own = _state.get(session_id, {}).get("run_id") or ""
    return bool(own) and own != run_id


def note_acted(session_id: str | None) -> None:
    """Something the user can SEE happened in this job (an action row, a web search). See the docstring."""
    s = _state.get(session_id or "")
    if s is not None and s["status"] == "running":
        s["acted"] = True


def set_step(session_id: str, step: str) -> None:
    s = _state.get(session_id)
    if s is not None and s["status"] == "running":
        s["step"] = step
        s["acted"] = True
        s["ts"] = time.monotonic()


def _acted_at_end(prev: dict) -> bool:
    """Carry the running job's witness — but a result posted here by something OTHER than a running job
    is core.deferred_exec reporting a command it just ran on the user's screen, which is not the empty
    ending this flag exists to catch."""
    return bool(prev.get("acted")) if prev["status"] == "running" else True


def finish(session_id: str, summary: str, files: list[str], *, run_id: str = "") -> None:
    if _another_run_owns(session_id, run_id):
        return
    prev = get(session_id)
    _state[session_id] = {"status": "done", "goal": prev["goal"], "step": "",
                          "summary": summary, "files": list(files or []), "reason": "",
                          "announced": False, "acted": _acted_at_end(prev),
                          "el_call_bound": prev.get("el_call_bound", False),
                          "run_id": prev.get("run_id", ""), "ts": time.monotonic()}


def fail(session_id: str, reason: str, *, run_id: str = "") -> None:
    if _another_run_owns(session_id, run_id):
        return
    prev = get(session_id)
    _state[session_id] = {"status": "failed", "goal": prev["goal"], "step": "",
                          "summary": "", "files": [], "reason": reason,
                          "announced": False, "acted": _acted_at_end(prev),
                          "el_call_bound": prev.get("el_call_bound", False),
                          "run_id": prev.get("run_id", ""), "ts": time.monotonic()}


def mark_announced(session_id: str) -> None:
    s = _state.get(session_id)
    if s is not None:
        s["announced"] = True


def has_pending_announcement(session_id: str | None) -> bool:
    """True when a background task just finished/failed and the user hasn't been told yet. Used so a muted
    session still SPEAKS the completion once (the user muted their mic, but a finished heavy task is exactly
    when Kotoba must announce — otherwise 'it ended and she said nothing')."""
    if not session_id:
        return False
    s = get(session_id)
    return s["status"] in ("done", "failed") and not s["announced"]


def clear(session_id: str, *, run_id: str = "") -> None:
    if _another_run_owns(session_id, run_id):
        return
    _state.pop(session_id, None)


def register_task(session_id: str, task: asyncio.Task) -> None:
    _tasks[session_id] = task


def pop_task(session_id: str, *, only: asyncio.Task | None = None) -> asyncio.Task | None:
    if only is not None and _tasks.get(session_id) is not only:
        return None
    return _tasks.pop(session_id, None)


_OVER = (" That job is OVER: report it in the past tense. Do NOT say you're starting it, do NOT tell "
         "them to watch the screen for it, and do NOT call start_work again for it.")


def prompt_note(session_id: str) -> str:
    """A short status line injected into the voice turn's context so the model can report/announce.
    Empty when there's nothing to say (idle, or a done/failed item already announced)."""
    s = get(session_id)
    st = s["status"]
    if st == "running":
        return f"[BACKGROUND WORK running] goal: {s['goal']} — current step: {s['step']}."
    if st in ("done", "failed") and not s["announced"]:
        if st == "failed":
            return f"[BACKGROUND WORK just failed] reason: {s['reason']}.{_OVER}"
        if not s["acted"]:
            return (
                f"[BACKGROUND WORK ended without doing anything] It stopped before touching anything: no "
                f"page was opened, nothing was searched, built or run, and the screen stayed empty. All "
                f"it came back with: {s['summary']}.{_OVER} If you told them you were on it or to watch "
                f"the screen, correct that NOW in plain words: say you did not actually do it, and what "
                f"you would need from them in order to. Never claim you opened, read or built anything."
            )
        files = f" Files: {', '.join(s['files'])}." if s["files"] else ""
        return f"[BACKGROUND WORK just finished] result: {s['summary']}.{files}{_OVER}"
    return ""
