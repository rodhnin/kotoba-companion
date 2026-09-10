"""Run heavy work in the background, decoupled from the voice turn.

start() launches _run() as a detached task registered in work_state, drives the agentic loop in
mode="work", and emits a work_started/working/work_done bracket; the companion turn never blocks on
it. Every frame carries the job's `run_id`, or a client awaiting its own turn draws this job's rows
inside its reply, and every end-state write is keyed to that run — else a successor started in the
gap before this CancelledError lands has its record wiped by the run it replaced.

The START line logs the user's verbatim words only at DEBUG: at INFO they reached stderr — a
world-readable file, the journal, `docker logs` — and spoken passwords with them."""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Awaitable, Callable

from kotoba.core import task_list, transport, work_state
from kotoba.core.events import emit_task
from kotoba.soul import prompt as _prompt

log = logging.getLogger("kotoba")


async def _run_with_compute_budget(
    coro: Awaitable,
    scope: str,
    budget: float,
    interactive_seconds_fn: Callable[[str], float],
) -> str:
    """Run `coro` under a budget on COMPUTE time only — wall time MINUS the seconds blocked on a human
    (ask_secret/ask_user/approval). A slow password + 2FA must not fail as "took too long".

    `scope` is what the refund is read under, and it is THIS RUN, not the session: the session is
    shared with every companion turn, so read per session the budget grew by whatever a card elsewhere
    on screen took to answer. Raises asyncio.TimeoutError when compute exceeds `budget`. It does NOT
    extend an ElevenLabs call — that ages during a human wait, and announce-on-reconnect covers it.

    The finally is not decoration: a cancel landing mid-`asyncio.wait` does NOT propagate into the
    inner loop task, and unattended it runs on orphaned."""
    task = asyncio.ensure_future(coro)
    start = time.monotonic()
    interval = max(0.01, min(2.0, budget / 4))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if task in done:
                return task.result()
            compute_elapsed = (time.monotonic() - start) - interactive_seconds_fn(scope)
            if compute_elapsed >= budget:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                raise asyncio.TimeoutError
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass


def _work_timeout(el_call_bound: bool = False) -> float:
    """COMPUTE-time ceiling for one background work item, in seconds: a stalled loop must fail and
    announce rather than leave the user waiting. Human wait (ask_secret/ask_user) is EXCLUDED.

    The CEILING is right in both transports; the NUMBER was ElevenLabs'. 1500s was chosen to sit under
    EL_MAX_DURATION_SECONDS with announce headroom, and that clock exists only while an EL agent holds
    the call — so it governs an EL-bound job and nothing else. Off that transport the real bound on a
    runaway loop is the iteration / tool-call / failure caps, and the wall clock is only the backstop.

    `el_call_bound` is CAPTURED when the job is created and passed down: it cannot be read at timeout —
    this runner is detached, the setting stays live-settable all run, and the CLI has no transport."""
    from kotoba.core import app_settings

    default = (transport.WORK_TIMEOUT_EL_BOUND_SECONDS if el_call_bound
               else transport.WORK_TIMEOUT_SECONDS)
    try:
        return max(30.0, float(app_settings.runtime_value("work_timeout", "KOTOBA_WORK_TIMEOUT", default)))
    except ValueError:
        return default

_WORK_SUBSYSTEM = (
    "You are in WORK MODE: doing a focused task the user asked for, running in the background. Use your "
    "tools to actually DO it end-to-end (browse, write files, run code) — don't stop to ask unless truly "
    "blocked.\n"
    "Tool rules:\n"
    "- STEP ZERO — OPEN THE MATCHING SKILL: if one of the SKILLS listed for you fits this job, your very "
    "first call is skill_view on that ONE skill, and then you act on what it says. A skill carries rules "
    "about the DELIVERABLE — the language it is written in, which sources count, how it is structured — "
    "that are written nowhere else, so a job that never opens it ships the wrong artifact. One skill, one "
    "call, no re-reading; if nothing on the list matches, there is no step zero and you act straight away.\n"
    "- BIAS TO ACTION, from step zero onward: every call after it is the real action (write_file / "
    "browser_navigate / execute_code / web_search) — never a second skill_view, never a memory_recall "
    "'preparation' step, never a plan written instead of done. These two are one rule with an entry "
    "point: step zero is a single read that ends the moment it returns, and everything past it acts. "
    "Each covers a failure the other cannot — do not delete either one to satisfy the other.\n"
    "- If the tools you need are ALREADY in your toolset (e.g. browser_navigate, browser_take_screenshot), "
    "USE them directly — the browser is usually ready. Only call mcp_install when a service's tools truly "
    "aren't listed, and only ONCE — never re-install a server you already have.\n"
    "- mcp_install gains a NOT-YET-AVAILABLE service by NAME only (valid: browser, filesystem, github, "
    "google calendar, memory, notion, linear, slack). Prefer it for these by-name servers (it's "
    "deterministic). Never invent a package/command, and NEVER use shell or npm/npx to install an MCP.\n"
    "- If you need a capability you have NO tool for (e.g. control Spotify, query Postgres), call "
    "mcp_find(query) to discover + install a real server from the official MCP registry — the user approves "
    "it on screen. Use mcp_install for the known ones (browser/filesystem/github/calendar/memory); use "
    "mcp_find for anything else. Never invent packages or shell-install an MCP.\n"
    "- ALREADY-CONNECTED capability: if what you need is listed under AVAILABLE TOOLSETS (connected but not "
    "loaded — e.g. blender, notion, github), call activate_tools('<name>') and its tools appear next step. Do "
    "NOT mcp_find/mcp_install it again (it's installed). Re-installing a connected server is a bug.\n"
    "- KNOW YOUR TOOLS: after activating a capability, READ the tool list/schemas you now have and pick the "
    "MOST SPECIFIC tool for the job (e.g. a dedicated 'create page' tool over raw code). The schema IS the "
    "call syntax — never web-search HOW TO CALL a tool you already have.\n"
    "- BUT DO RESEARCH THE DOMAIN when you're unsure: if a tool runs code or needs domain knowledge you don't "
    "have (e.g. blender__execute_blender_code wants correct `bpy` Python, a library's real API, a service's "
    "data model), it's GOOD to web_search the right way to do it BEFORE running — guessing an API and getting "
    "an error wastes a turn. And ALWAYS inspect real state first (e.g. blender__get_scene_info / "
    "get_object_info to see the ACTUAL object/material names) so your code references things that exist. "
    "Resolve unknowns yourself: look it up, inspect, then act — don't guess, and never claim success over an "
    "errored call.\n"
    "- A service that needs a BROWSER SIGN-IN (OAuth: Notion, Linear, Slack, Google, …): mcp_find/mcp_install "
    "will connect it as far as it can and tell you it's been added to Settings → 'Needs connection'. That "
    "sign-in can ONLY be completed by the USER with the 'Sign in' button in Settings — you CANNOT finish it "
    "by opening the service's website and logging in there. When you get that 'added to Settings' result, you "
    "are DONE for now: STOP and report that the server is ready and waiting for their one-tap sign-in. Do NOT "
    "browser_navigate to the service to log in manually, and never claim its tools work before they sign in.\n"
    "- For a screenshot, take a plain full-view capture; do NOT combine a full-page option with an element "
    "target (that errors). You can SEE screenshots — judge layout/colors/design from them.\n"
    "- The browser is driven by the accessibility SNAPSHOT (snapshot first; act on elements by their ref via "
    "`target`, never a CSS selector; re-snapshot after the page changes). The full step-by-step is in the "
    "HOW TO USE THE BROWSER guidance and the operating-websites skill — follow it.\n"
    "- FILLING FORMS / LOGGING IN — collect every value through an ON-SCREEN box, NEVER assume you have it "
    "and NEVER use something the user said by voice:\n"
    "    • password / secret → call ask_secret(name, prompt) FIRST (masked box). It returns a placeholder "
    "{{secret:NAME}}; ONLY AFTER that call, type {{secret:NAME}} into the field and it fills the real value. "
    "If you type {{secret:NAME}} without having called ask_secret, it will be REFUSED — so always ask_secret "
    "first. Never type a real password yourself.\n"
    "    • email / username / name / date / any normal field → call ask_user(prompt), get the typed value, "
    "type that. Don't reuse a value spoken by voice.\n"
    "    • your OWN saved credential → get_credential(name) gives you a {{secret:NAME}} placeholder to type "
    "(the real value fills at the browser, never shown to you); request_credential(name, prompt) to "
    "save a new one of yours.\n"
    "- STOP the moment the deliverable exists (file written, answer found, page captured). Don't loop; "
    "calling the same kind of tool again instead of producing the result is failure.\n"
    "- HONESTY OVER A TOOL ERROR: if a tool result contains an error (e.g. 'Code execution error', "
    "'NoneType has no attribute', a non-zero exit, an exception), you did NOT succeed. Read the error, FIX "
    "the cause and retry, or report the failure plainly. NEVER write a summary that claims you did something "
    "(painted it, created it, moved it) when the call that would have done it errored — verify from the real "
    "result, not your intention. For Blender/code edits, confirm with a follow-up read (get_object_info / "
    "get_scene_info / a screenshot) before claiming it's done.\n"
    "- IF YOUR FINAL VERIFICATION COULDN'T RUN (a tool errored, the app/connection dropped — e.g. 'Could not "
    "connect to Blender', 'Connection refused/closed', the addon stopped), you CANNOT confirm the result: do "
    "NOT report it as done. Say honestly what you applied and that you couldn't verify the final state because "
    "the connection/app dropped (it may have crashed) — ask them to check. A truthful 'I did X but couldn't "
    "confirm because Blender disconnected' beats a confident 'all done' that may be false.\n"
    "When done, end with a SHORT, concrete summary of what you produced — that summary is announced to the "
    "user by voice, so keep it natural and never include code or raw file contents."
)


_REQUEST_CAP = 2000
_URL_RE = re.compile(r"https?://", re.IGNORECASE)


def _request_note(request: str) -> str:
    """How the START line describes the user's message: its shape at INFO, its words only at DEBUG."""
    req = request or ""
    if log.isEnabledFor(logging.DEBUG):
        return f"request={req[:120]!r}"
    return f"request_chars={len(req)} request_urls={len(_URL_RE.findall(req))}"


_LANGUAGE_NOTE = (
    "\n\nThat message is also where the LANGUAGE comes from. Everything you hand back belongs to the "
    "language they wrote it in — the summary you end with, and every file you produce, its title and its "
    "headings as well as its prose, never one language over the other. This brief and your instructions "
    "are in English because the program is written in English; that is not the user asking for English, "
    "and neither is a goal somebody paraphrased into it above."
)


def _brief(goal: str, request: str) -> str:
    """The job's user message: the goal, with the user's verbatim words under it as the authority.

    One item and not two, so the loop's "latest user message" readers see the request and the goal
    together. Falls back to the bare goal when there is nothing to carry.

    The words carry the LANGUAGE, and the language needs saying: asked in Spanish for an
    investigation, the job wrote its report in English, because the whole run is one English developer
    message and an English goal. The skill that states the rule is the one the same developer message
    tells the job not to open before acting. Past `_REQUEST_CAP` the request is cut at a word only
    while that keeps nearly all of it — one 4000-character token trimmed to its last space is gone."""
    req = " ".join((request or "").split())
    goal = (goal or "").strip()
    if not req or req == goal:
        return goal
    if len(req) > _REQUEST_CAP:
        cut = req[:_REQUEST_CAP]
        head = cut.rsplit(" ", 1)[0]
        req = (head if len(head) > _REQUEST_CAP * 0.8 else cut) + "…"
    return (
        f"{goal}\n\nThe user's own words, verbatim. Every concrete detail is in here — links, names, "
        "file paths, exact spellings — and this is what they actually asked for, so it OVERRIDES any "
        "paraphrase above. Never ask them for something this message already contains:\n"
        f"\"{req}\"" + _LANGUAGE_NOTE
    )


async def _run(session_id: str, goal: str, db, soul_patterns: dict, mcp, request: str = "",
               el_call_bound: bool = False, run_id: str = "",
               exclude_tools: frozenset[str] | None = None) -> None:
    """The job itself, detached: the bracket, the loop under a compute budget, and its one ending.

    The bracket is emitted here because the frontend keeps the ElevenLabs call alive on it. The loop is
    fed a throwaway queue — this runner does not speak, only its summary is announced — on
    `channel="text"`, which buys the roomy typed approval window. Human waits are charged to the RUN.

    Cancelling is not failing: a requested stop clears and announces nothing, but it MUST still close
    the bracket or the frontend keepalive pings for ~28 minutes and every ping suppresses her speech.
    Not every CancelledError is a cancel, though — `me.cancelling() == 0` is a stray one nobody asked
    for, recorded as the failure it is; its exc_info carries the inner frames and is the whole line."""
    from kotoba.core.loop import agentic_loop

    files: list[str] = []
    run_id = run_id or uuid.uuid4().hex[:8]
    import time as _t
    _t0 = _t.monotonic()
    log.info("work_runner START session=%s goal=%r %s", session_id, goal[:120], _request_note(request))
    await emit_task(session_id, "work_started", goal=goal[:500], run_id=run_id)
    await emit_task(session_id, "working", on=True, run_id=run_id)
    from kotoba.core import interaction

    try:
        input_items = [
            {"role": "developer", "content": _WORK_SUBSYSTEM + _prompt.platform_note()},
            {"role": "user", "content": _brief(goal, request)},
        ]
        with interaction.interactive_scope(run_id), transport.detached_from_el_call():
            summary = await _run_with_compute_budget(
                agentic_loop(
                    input_items, session_id, db, asyncio.Queue(), soul_patterns,
                    mcp=mcp, mode="work",
                    channel="text",
                    run_id=run_id,
                    exclude_tools=exclude_tools,
                ),
                run_id,
                _work_timeout(el_call_bound),
                interaction.interactive_seconds,
            )
        summary = (summary or "").strip()
        if not summary:
            summary = "the work ended without a final summary — its files are the only record of the result"
        work_state.finish(session_id, summary, files, run_id=run_id)
        log.info("work_runner DONE session=%s in %.1fs summary=%r", session_id, _t.monotonic() - _t0, summary[:120])
        await emit_task(session_id, "work_done", ok=True, summary=summary[:500], run_id=run_id)
    except asyncio.TimeoutError:
        log.warning("work_runner timed out for session %s", session_id)
        work_state.fail(session_id, "it took too long and I stopped it", run_id=run_id)
        await emit_task(session_id, "work_done", ok=False, summary="", run_id=run_id)
    except asyncio.CancelledError:
        me = asyncio.current_task()
        if me is not None and me.cancelling() == 0:
            log.error("work_runner DIED session=%s after %.1fs — a stray CancelledError nobody "
                      "requested, goal=%r", session_id, _t.monotonic() - _t0, goal[:120],
                      exc_info=True)
            work_state.fail(session_id, "it stopped mid-run on its own — try launching it again",
                            run_id=run_id)
            await emit_task(session_id, "work_done", ok=False, summary="", run_id=run_id)
            return
        log.info("work_runner CANCELLED session=%s after %.1fs (stopped on request)",
                 session_id, _t.monotonic() - _t0)
        work_state.clear(session_id, run_id=run_id)
        try:
            await emit_task(session_id, "work_done", ok=True, summary="", cancelled=True, run_id=run_id)
        except BaseException:
            pass
        raise
    except Exception as e:
        log.exception("work_runner failed for session %s", session_id)
        work_state.fail(session_id, f"{type(e).__name__}: {e}", run_id=run_id)
        await emit_task(session_id, "work_done", ok=False, summary="", run_id=run_id)
    finally:
        work_state.pop_task(session_id, only=asyncio.current_task())
        task_list.release_run(session_id, run_id)
        await emit_task(session_id, "working", on=False, run_id=run_id)
        try:
            from kotoba.core import ephemeral_secrets

            if not work_state.is_running(session_id):
                ephemeral_secrets.clear(session_id)
        except Exception:
            pass


def start(session_id: str, goal: str, db, soul_patterns: dict, mcp, request: str = "",
          exclude_tools: frozenset[str] | None = None) -> None:
    """Launch the work-runner as a detached background task that survives the voice turn. `request` is
    the user message that triggered it, carried verbatim into the job.

    THIS LINE is where the transport is captured — it runs inside the turn that asked for the work, the
    last moment anything knows who holds a clock over it, and the job then runs for up to an hour (25
    minutes when what was captured IS an ElevenLabs call) with nothing around it to ask.

    The run id is minted HERE for the same reason: the plan she wrote in the breath before start_work
    is claimed synchronously, while _run — a detached task — does not begin until the next tick, long
    enough for the launching turn to close that plan first."""
    el_bound = transport.el_call_bound()
    run_id = uuid.uuid4().hex[:8]
    work_state.start(session_id, goal, el_call_bound=el_bound, run_id=run_id)
    task_list.bind_run(session_id, run_id)
    task = asyncio.create_task(
        _run(session_id, goal, db, soul_patterns, mcp, request, el_bound, run_id,
             exclude_tools=exclude_tools))
    work_state.register_task(session_id, task)
