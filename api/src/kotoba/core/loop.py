"""The agentic loop and the per-tool heartbeat.

Hand-rolled on client.responses.create(stream=True), NOT the openai-agents SDK: tool calls and their
outputs correlate by `call_id`, fed back as `function_call_output` items on the next call. EVERY
output item goes back, not only the ones the API refuses to run without — dropping her own `message`
items asked the next iteration of a model with no record of having spoken, and it wrote the same
paragraphs into the same reply twice. Items are echoed WHOLE rather than rebuilt as `{"role": …}`
(the item carries `phase`, which newer models degrade without), and a `reasoning` item must be
re-sent with its function_call or the API answers 400. `INTERRUPTED_TEXT` is display text only: the
STATE of a cut step travels as `outcome` on the step frame, never as that string."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import uuid
from contextvars import ContextVar

from kotoba.core.emotions import extract_emotion
from kotoba.core.events import emit_emotion, emit_task
from kotoba.core.llm import get_client, is_reasoning_model, model_call_kwargs, model_name
from kotoba.core.sandbox.base import exit_outcome, record_exits
from kotoba.core import stream as _stream
from kotoba.core.stream import emotion_from_text, narrate
from kotoba.tools import ToolContext
from kotoba.tools.registry import COMPANION_TOOLSETS, dispatchable, registry, schemas_for

import logging

try:
    from openai import RateLimitError as _RateLimitError
except Exception:  # pragma: no cover
    _RateLimitError = None

TOOL_TIMEOUT = 30
_HEARTBEAT_FIRST = 9.0
_HEARTBEAT_EVERY = 21.0
_HEARTBEAT_TICK = 3.0

INTERRUPTED_TEXT = "(interrupted)"


def _tool_budget(tool, spec, args: dict, base: int, channel: str | None,
                 el_agent: bool | None = None) -> int:
    """Seconds of compute this tool gets. An approval-gated tool (RISK 'exec') must OUTLIVE the approval
    window this turn's card will actually get — otherwise the loop cancels it mid-wait and abandons the
    card on the user's screen, which is the invariant behind core.interaction.VOICE_APPROVAL_TIMEOUT.
    The window depends on the transport as well as the channel, so both are carried here rather than the
    number: a window that widens without its budget widening is the abandoned card by another route.
    Read tools keep the plain budget."""
    from kotoba.core.interaction import approval_timeout

    timeout = int(getattr(tool, "TIMEOUT", base) or base)
    headroom = int(approval_timeout(channel, el_agent=el_agent)) + 5
    if getattr(tool, "ARG_TIMEOUT", False):
        try:
            want = int((args or {}).get("timeout", 60) or 60)
        except (TypeError, ValueError):
            want = 60
        hard = int(getattr(tool, "MAX_TIMEOUT", 600))
        return max(timeout, min(hard, max(1, want) + headroom))
    if spec is not None and getattr(spec, "risk", None) == "exec":
        return max(timeout, headroom)
    return timeout


def _work_max_iter() -> int:
    """Read at RUNTIME so the Settings panel can change it live (override > env > default)."""
    from kotoba.core import app_settings
    try:
        return int(app_settings.runtime_value("work_max_iter", "KOTOBA_WORK_MAX_ITER", "40"))
    except ValueError:
        return 40


def _default_max_iterations(mode: str) -> int:
    """Iterations for a turn nobody sized explicitly.

    Two things were wrong with using _work_max_iter() for both modes. (1) The tool cap and the iteration
    cap were the SAME number, and the gate that drops every tool ("you are out of calls, answer in words")
    is evaluated at the TOP of an iteration — so at the model's usual one-call-per-iteration it needed
    iteration 41 of 40 and never ran. The loop just stopped mid-chain, and work_runner announced "Done."
    as a success. Always leave room for that last, tool-less synthesis pass.
    (2) A COMPANION turn inherited the Settings field labelled "Advanced (work) → Max iterations" (min 1),
    so turning a work limit down made the voice go silent mid-conversation."""
    max_tool_calls, _fail = _caps_for(mode)
    base = _work_max_iter() if mode == "work" else max_tool_calls
    return max(base, max_tool_calls + 1)


def _limit(name: str, fallback: int) -> int:
    """A blank value is how anybody unsets a variable, and the shipped example file has six of these
    uncommented. Read bare, an empty string raised at IMPORT — the server did not start and the
    traceback named neither the variable nor the file it came from."""
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else fallback
    except ValueError:
        logging.getLogger("kotoba").warning(
            "%s is not a number (%r); using %s", name, raw, fallback)
        return fallback


_PER_TOOL_LIMIT = _limit("KOTOBA_PER_TOOL_LIMIT", 3)
_WEB_SEARCH_LIMIT = _limit("KOTOBA_WEB_SEARCH_LIMIT", 8)
_DELEGATE_LIMIT = _limit("KOTOBA_DELEGATE_LIMIT", 3)
_BROWSER_TOOL_LIMIT = _limit("KOTOBA_BROWSER_TOOL_LIMIT", 40)
_TODO_TOOL_LIMIT = _limit("KOTOBA_TODO_TOOL_LIMIT", 10)
_DELEGATE_CONCURRENCY = _limit("KOTOBA_DELEGATE_CONCURRENCY", 2)
_URL_LOOKBACK = _limit("KOTOBA_URL_LOOKBACK", 3)
_MAX_TOOL_CALLS = _limit("KOTOBA_MAX_TOOL_CALLS", 8)
_WORK_MAX_TOOL_CALLS = _limit("KOTOBA_WORK_MAX_TOOL_CALLS", 40)
_WORK_FAIL_LIMIT = _limit("KOTOBA_WORK_FAIL_LIMIT", 6)
_COMPANION_FAIL_LIMIT = _limit("KOTOBA_FAIL_LIMIT", 2)


def _select_delegates_to_spawn(items, budget, already_ran):
    """From this turn's `delegate` calls, choose which to actually SPAWN vs SKIP, applying the per-turn cap
    and dedup BEFORE launching. Spawning all of them and discarding the overflow (the old order) wasted whole
    subagent runs and burst the token budget. A call is SKIPPED if it duplicates another in the same batch,
    is an exact repeat of one already run this turn (`already_ran(sig)` True), or is past the remaining
    `budget`. `items` is [(tc, args_dict), …]; returns (spawn, skip) preserving those pairs, order kept.
    Every spawned call is announced (step card) BEFORE the parallel gather, its card is closed the moment
    ITS helper returns (not when the whole batch does), and its cached result is ALWAYS consumed by the
    per-tool loop — its cap gate skips `_parallel_results` entries, else a same-batch duplicate could push
    per_tool past the cap and leave an announced card dangling, result discarded."""
    spawn: list = []
    skip: list = []
    seen: set[str] = set()
    for tc, a in items:
        sig = f"delegate:{json.dumps(a, sort_keys=True)}"
        if sig in seen or already_ran(sig) or len(spawn) >= max(0, budget):
            skip.append((tc, a))
        else:
            seen.add(sig)
            spawn.append((tc, a))
    return spawn, skip


def _publish_file(session_id: str | None, workdir, rel: str, content: str | None = None) -> None:
    """Synchronous: update the session stash and file-library panel after write_file or patch.

    In the default setup (workdir IS the library) only the panel index is touched — bytes are never
    re-written, so _MAX_TEXT cannot truncate a file larger than 200 KB that the tool wrote correctly.
    No stash is taken either: stashing would truncate at file_store._MAX_FILE, and _rehydrate would
    write the truncated copy back over the real file when the sandbox is recreated.
    In advanced mode (KOTOBA_WORKSPACE_DIR set) the library copy is synced from disk.
    Never widens the path jail: raises PathSecurityError (caller must catch) on traversal."""
    from pathlib import Path
    from kotoba.core.path_security import validate_within_dir
    from kotoba.core import file_library, file_store

    p = validate_within_dir(rel, workdir)
    if content is None:
        try:
            content = p.read_text(errors="replace", encoding="utf-8")
        except OSError:
            return

    wd_is_lib = str(Path(workdir).resolve()) == str(file_library.library_dir().resolve())
    if wd_is_lib:
        file_library.touch(rel)
    else:
        file_store.stash(session_id, rel, content)
        file_library.save_text(rel, content)


def _has_url(input_items: list) -> bool:
    """Is there a real link in play? web_extract is offered only when there is — otherwise the model
    invents a URL to fetch instead of searching.

    Looking at the LAST user message alone was too narrow: hand her a link and then say "ábrelo" and the
    URL is one turn back, so the only tool that can open it isn't even on the table. _URL_LOOKBACK recent
    USER messages are scanned (user only — assistant turns carry web_search citation URLs she was never
    asked to open)."""
    seen = 0
    for m in reversed(input_items):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        if re.search(r"https?://", str(m.get("content", ""))):
            return True
        seen += 1
        if seen >= _URL_LOOKBACK:
            break
    return False


def _web_search_kwargs(use_tools: list, done: int) -> dict:
    """Server-side guard for the built-in web_search budget. OpenAI chains MULTIPLE web_search_call items
    inside ONE streamed response, so dropping the tool at iteration boundaries alone can overshoot the
    per-run cap mid-response (seen live: 14 searches against a cap of 8). `max_tool_calls` (Responses API:
    "maximum number of total calls to built-in tools … in a response")
    bounds each response to the remaining budget. web_search is our only built-in, and the parameter is
    sent only to OpenAI — other OpenAI-compatible providers may reject it."""
    from kotoba.core import providers

    if providers.active_provider_id() != "openai":
        return {}
    if not any(t.get("type") == "web_search" for t in use_tools):
        return {}
    return {"max_tool_calls": max(1, _WEB_SEARCH_LIMIT - done)}


def _web_field(action, name: str) -> str:
    """One field off a `web_search_call.action`, whether the SDK handed us an object or a dict."""
    got = getattr(action, name, None)
    if got is None and isinstance(action, dict):
        got = action.get(name)
    if isinstance(got, (list, tuple)):
        got = ", ".join(str(x) for x in got if x)
    return " ".join(str(got).split()) if got else ""


def _web_action_text(item) -> str:
    """What one `web_search_call` actually DID, for the phrase beside its chip.

    The built-in has three actions and only `search` carries a query — `open_page` carries a url,
    `find_in_page` a pattern, and even a search reports its query "usually (but not always)". Read as
    a query and nothing else, all three come back empty. Plain words, no glyphs: this string lands
    verbatim in a terminal row, and the magnifying glass it used to carry folded to `?` under --ascii.

    `queries` is read BEFORE the deprecated singular `query`: nothing documents what lands in the
    singular when the model fans out, and a fanned-out search was reported as one legacy value with
    the rest invisible. Either field is model-authored free text — display it, never parse or route."""
    action = getattr(item, "action", None)
    if action is None:
        return "searched the web"
    query = _web_field(action, "queries") or _web_field(action, "query")
    if query:
        return f"searched for '{query}'"
    kind = _web_field(action, "type")
    url, pattern = _web_field(action, "url"), _web_field(action, "pattern")
    if pattern or kind == "find_in_page":
        return f"looked for '{pattern}' in the page" if pattern else "looked inside the page"
    if url or kind == "open_page":
        return f"opened {url}" if url else "opened a page"
    return "searched the web"


def _caps_for(mode: str) -> tuple[int, int]:
    """(max_total_tool_calls, fail_limit) for the mode. WORK gets much higher ceilings than COMPANION.
    Work-mode caps read at RUNTIME so the Settings panel can change them live; companion caps stay fixed."""
    if mode == "work":
        from kotoba.core import app_settings
        try:
            mt = int(app_settings.runtime_value("work_max_tool_calls", "KOTOBA_WORK_MAX_TOOL_CALLS", "40"))
        except ValueError:
            mt = _WORK_MAX_TOOL_CALLS
        try:
            fl = int(app_settings.runtime_value("work_fail_limit", "KOTOBA_WORK_FAIL_LIMIT", "6"))
        except ValueError:
            fl = _WORK_FAIL_LIMIT
        return mt, fl
    return _MAX_TOOL_CALLS, _COMPANION_FAIL_LIMIT


async def _ensure_work_browser(ctx) -> None:
    """Connect the browser MCP UP-FRONT for a work task — but NEVER launch the real browser here.

    Connecting early fixes two things that came of the browser only appearing after a mid-loop
    mcp_install: the model called mcp_install on every work task, because the owner drops the browser
    tools whenever the CDP session dies and nothing re-connects them; and the tool-aware guidance plus
    the operating-websites skill key off browser tools being PRESENT when guidance is built, once, at
    loop start — arriving later, the model never got the snapshot→target workflow and guessed refs.

    Connecting is not opening a window: the MCP attaches to CDP lazily, per tool call. Calling
    ensure_browser here popped a window on every work task, web or not. No-op if already registered."""
    mcp = getattr(ctx, "mcp", None)
    if mcp is None or "browser" in getattr(mcp, "server_tools", {}):
        return
    cdp = os.getenv("KOTOBA_BROWSER_CDP", "").strip()
    if not cdp:
        return
    try:
        from kotoba.core.mcp.known import build_cfg

        cfg = build_cfg("browser")
        if cfg is not None:
            await mcp.connect(cfg[0], cfg[1])
    except Exception:
        pass


def _work_guidance_text(offered_names: set[str]) -> str:
    """Tool-aware guidance for the work loop: verification (always) + the browser workflow
    and the skills relevant to the active toolsets (only when those tools are present). Returns '' when
    nothing applies. Shared by the start-of-loop injection and the mid-loop fallback."""
    from kotoba.core.tool_guidance import guidance_for, skills_prompt

    parts = [guidance_for(offered_names, "work")]
    try:
        from kotoba.core.skill_docs import skills_for_toolsets

        active: set[str] = set()
        for n in offered_names:
            _s = registry().get(n)
            ts = getattr(_s, "toolset", None) if _s is not None else None
            if ts:
                active.add(ts)
                if ts.startswith("mcp:"):
                    active.add(ts.split(":", 1)[1])
        parts.append(skills_prompt(skills_for_toolsets(active)))
    except Exception:
        pass
    return "\n\n".join(p for p in parts if p)


def _deferred_servers_block(mcp, active: set | None) -> str:
    """List CONNECTED MCP servers whose tools are DEFERRED (not in the toolset this turn) + how to load
    them. Progressive disclosure: the model sees the capability exists and calls activate_tools(name) only
    when it needs it, so unused servers (github's 44 tools, …) don't bloat/confuse the toolset. '' when
    there's nothing deferred."""
    server_tools = getattr(mcp, "server_tools", None) or {}
    active = active or set()
    lines = []
    for name, tools in server_tools.items():
        if name in active or not tools:
            continue
        sample = ", ".join(t.split("__", 1)[-1] for t in tools[:6])
        more = "…" if len(tools) > 6 else ""
        lines.append(f"- {name} ({len(tools)} tools: {sample}{more})")
    if not lines:
        return ""
    return (
        "AVAILABLE TOOLSETS (connected but NOT loaded — to keep your tools focused). When a task needs one, "
        "call activate_tools('<name>') and that server's tools appear next step; don't say you lack it.\n"
        + "\n".join(lines)
    )

def _parse_tool_args(raw: str | None) -> dict:
    """Model-supplied arguments can be valid JSON that is NOT an object (null/list/number) or broken
    mid-stream; every caller does .get() on the result, so anything non-dict becomes {}. web_search
    PUA citation runs are scrubbed from the RAW argument string first (safe: PUA chars never occur in
    JSON structure) — the SDK item may be frozen, so the string is cleaned, never the item."""
    from kotoba.core.stream import strip_citation_markers

    cleaned = strip_citation_markers(raw) if raw else raw
    try:
        args = json.loads(cleaned) if cleaned else {}
    except json.JSONDecodeError:
        args = {}
    return args if isinstance(args, dict) else {}


def _step_text(name: str, args: dict) -> str:
    """The action line: what she's about to do, in real terms (command, path, query)."""
    if name == "shell":
        return f"$ {str(args.get('command', '')).strip()[:200]}"
    if name == "execute_code":
        code = str(args.get("code", "")).strip().splitlines()
        first = code[0][:80] if code else ""
        return f"python> {first}" + (" …" if len(code) > 1 else "")
    if name in ("read_file", "write_file", "patch"):
        return f"{name} {args.get('path', '')}"
    if name == "search_files":
        return f"search {args.get('query', '')!r}"
    if name in ("web_search", "web_extract"):
        return f"{name} {args.get('url') or args.get('query') or ''}".strip()
    if name == "mcp_find":
        return f"search MCP registry: {str(args.get('query', '')).strip()}"
    if name == "mcp_install":
        return f"connect MCP: {str(args.get('name', '')).strip()}"
    return name


def _step_kind(name: str, spec) -> str:
    """A coarse TYPE for the terminal UI to style each step (icon + color), distinct per kind of action."""
    if name == "shell":
        return "shell"
    if name == "execute_code":
        return "code"
    if name in ("web_search", "web_extract"):
        return "web"
    if name in ("read_file", "write_file", "patch", "search_files"):
        return "file"
    if name in ("mcp_find", "mcp_install") or name.startswith("browser__"):
        return "mcp"
    if spec is not None and str(getattr(spec, "toolset", "")).startswith("mcp:"):
        return "mcp"
    if name in ("skill_view", "skill_list"):
        return "skill"
    if name.startswith("memory"):
        return "memory"
    return "tool"


def _track_step(ctx, call_id: str, step: str) -> None:
    """Remember a terminal row that is still open, so the loop's finally can close it as `interrupted`
    when the turn is cut. A row nobody closes spins on the client for the rest of the session."""
    if not call_id:
        return
    try:
        ctx._open_steps[call_id] = step
    except AttributeError:
        ctx._open_steps = {call_id: step}


_RECENT_USER_TEXTS = 4


def _recent_user_texts(input_items: list) -> list[str]:
    """The last few user utterances, newest first — `ctx.user_texts`, whose [0] is `ctx.user_text`.

    One message is not always one intent. A reminder is scheduled across two or three of them ("remind me
    to drink water every morning" → "what time?" → "eight"), so a tool honouring intent the model dropped
    (cronjob's repeat guard) has to be able to look one turn back. Bounded, and each item is the plain
    text of that turn, so a tool never has to know how a content part is shaped."""
    out: list[str] = []
    for m in reversed(input_items):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out.append(" ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("text")))
        if len(out) >= _RECENT_USER_TEXTS:
            break
    return out


def note_tool_refusal(ctx) -> None:
    """Witness that the tool running right now REFUSED ITSELF and produced nothing.

    The third way a call can end without running, after core.interaction (a card the human ended) and
    core.deferred_exec (a card that outlives the turn): the tool read its own arguments and declined.
    make_report does it when the prose promises citations it has none of. A refusal is a non-empty
    string, so without a witness it was graded exactly like a finished report — green ✓, an `executed`
    audit row, and a verbatim retry told "it is DONE". Keyed by ctx.call_id, the id
    of the Responses call in flight, like the other two."""
    call_id = str(getattr(ctx, "call_id", "") or "")
    if not call_id:
        return
    try:
        ctx._tool_refusals.add(call_id)
    except AttributeError:
        ctx._tool_refusals = {call_id}


def tool_refused(ctx, call_id: str) -> bool:
    """Did the tool itself refuse `call_id`, running nothing? (see note_tool_refusal)"""
    return bool(call_id) and call_id in (getattr(ctx, "_tool_refusals", None) or ())


_MAX_NOTED_FAILURES = 8
_failures: ContextVar[list[str] | None] = ContextVar("kotoba_tool_failures", default=None)


@contextlib.contextmanager
def record_tool_failures():
    """Collect every "I ran and I failed" reported inside this block, in order."""
    reasons: list[str] = []
    token = _failures.set(reasons)
    try:
        yield reasons
    finally:
        _failures.reset(token)


def note_tool_failure(reason: str = "") -> None:
    """Witness that the tool running right now RAN and FAILED. The third state, and NOT a refusal.

    `refused` means nothing happened: no audit row, a grey ⊘, and a sentence about somebody's
    decision. This one means the work was really attempted and did not land, so it keeps the opposite
    of each — an `executed:failed` row (a helper may have written files in the shared workdir before
    it died), a red ×, and the tool's own FAIL line. It therefore never joins _nothing_ran.

    Scoped like the sandbox's exit recording rather than keyed on ctx.call_id, because the parallel
    delegate path never sets one: two helpers running at once share the context, so a call_id key
    would paint one's crash onto the other. With no block open this is a no-op."""
    reasons = _failures.get()
    if reasons is not None and len(reasons) < _MAX_NOTED_FAILURES:
        reasons.append(str(reason or "the tool ran and failed"))


def _nothing_ran(ctx, call_id: str) -> bool:
    """Did this tool call execute nothing at all? Three gates can end a call that way and each keeps its
    own witness: core.deferred_exec for the card that outlives the turn, core.interaction for the one
    asked inline, note_tool_refusal for the tool that declined its own arguments. Only the first was ever
    consulted, so the MCP tools — which ask interaction directly and never defer — slipped past both the
    "it never ran" row and the "an audit row means something ran" rule, landing a green ✓ and an
    `executed` audit line on an install nobody approved; make_report's in-tool refusal walked through the
    same hole a day later."""
    from kotoba.core import deferred_exec, interaction

    return bool(
        deferred_exec.ran_nothing(ctx, call_id)
        or interaction.no_run_verdict(ctx, call_id)
        or tool_refused(ctx, call_id)
    )


def _refusal_line(ctx, call_id: str) -> str:
    """The row detail for a call that ended AT the approval card, or '' for every other ending.

    Four endings share the ⊘ mark, because the mark answers "did it run" and the answer is no in all
    four; what they must not share is the sentence. The row used to read "cancelled by user" for a card
    that had timed out unread, which is the one thing this panel may never do: report a decision the
    user never made. The sentences live with the four endings (core.interaction.refusal_row), so this
    panel and the one core.deferred_exec completes cannot describe the same ending two ways; the indent
    is the row's own. Second person, like the panel's own tooltips."""
    from kotoba.core import interaction

    sentence = interaction.refusal_row(interaction.no_run_verdict(ctx, call_id))
    return f"  {sentence}" if sentence else ""


def _step_outcome(ctx, call_id: str, ok: bool, deferred: bool, exit_codes: list[int]) -> str:
    """What ACTUALLY became of this action: `pending` · `refused` · `failed` · `ok`.

    The row used to be drawn from `ok` alone, and `ok` answers a different question — whether the tool
    handed back usable output for the MODEL. A shell that exited 2, or was killed at its timeout and
    reported 124, returns a perfectly good string, so a green ✓ was drawn over commands that failed,
    and over an inline refusal, whose "I held off on that one" is also a string.

    Each answer comes from the thing that WITNESSED it, never from the words: `pending` from the
    deferred record, `refused` from whichever gate ended the call without running, failure from the
    process's own exit code, then `ok`. Refusal is checked first: the user's decision is not an error."""
    if deferred:
        return "pending"
    if _nothing_ran(ctx, call_id):
        return "refused"
    if not ok:
        return "failed"
    return exit_outcome(exit_codes)


def _grant_note(ctx, call_id: str) -> str:
    """The tail a landed row wears when a stored "always allow" is the whole reason nothing asked.

    Without it a command covered by a family granted weeks ago draws the same row as a read that never
    needed a gate, and the only record that the grant was spent is the audit log.

    It names the grant's WIDTH, because the two are not the same promise: a family grant is standing
    permission for everything that starts with that word, an exact grant is one line and nothing else. A
    row that said "you always allow sh" over an exact grant would be reporting a permission the person
    never gave — and never could have, since the gate refuses to save that family at all."""
    grant = getattr(ctx, "_grants", {}).pop(call_id, "")
    if not grant:
        return ""
    who, family = grant if isinstance(grant, tuple) else ("saved", grant)
    if who == "saved-exact":
        return "you always allow this exact command"
    return f"you always allow {family}" if family else ""


async def _emit_action_step(ctx, session_id: str | None, name: str, args: dict, call_id: str = "") -> None:
    """Surface the current action as a terminal BLOCK 'start' frame: {id, step_kind, action} so the UI can
    show a typed, styled card and then complete it with the result (phase 'done'). Also writes the live
    work_state step (WORK mode) so the voice turn reports the REAL step (companion turns don't write it) —
    which doubles as the job's `acted` witness: this is the call that puts a row on the user's screen."""
    step = _step_text(name, args)
    kind = _step_kind(name, registry().get(name))
    await emit_task(
        session_id, "step", phase="start", id=call_id, step_kind=kind, action=step, text=step,
        run_id=getattr(ctx, "run_id", ""),
    )
    _track_step(ctx, call_id, step)
    if session_id and getattr(ctx, "mode", None) == "work":
        from kotoba.core import work_state

        work_state.set_step(session_id, step)


async def _peek(ctx, session_id: str | None, tool: str) -> None:
    """Which READ-ONLY tool she is inside, for a status line — never a row.

    A read never earns a `step` frame (that is _emit_action_step's rule and it stays), so "what do you
    know about me" fires five of them and puts nothing on the wire at all: a client cannot tell that
    apart from a hang. This is the smallest thing that closes it — one kind, one field, no id and no
    phase, because there is nothing to correlate and nothing to complete. `tool` is the registry name so
    the reader can keep its own words for it; the empty string clears. Emitted only when the value
    changes, so a run of reads is two frames and not two per call, and cleared by the next ACTION and by
    the turn's exit — a bar naming a tool she has left is the lie this exists to prevent."""
    if getattr(ctx, "_peek_tool", "") == tool:
        return
    ctx._peek_tool = tool
    await emit_task(session_id, "peek", tool=tool, run_id=getattr(ctx, "run_id", ""))


async def _announce_action(ctx, session_id: str | None, name: str, args: dict, call_id: str,
                           is_action: bool = True) -> None:
    """Emit the pre-execution UI signals for one tool call: the once-per-turn `working` chip (+determined
    face), the tool's focus expression, and either the terminal step 'start' card (an action) or the
    `peek` status frame (a read). MUST run BEFORE the tool executes — the parallel-delegate block calls
    this ahead of its gather so the cards appear while the helpers are still working, not minutes later
    when the batch returns."""
    rid = getattr(ctx, "run_id", "")
    if is_action and not getattr(ctx, "_working_emitted", False):
        ctx._working_emitted = True
        await emit_emotion(session_id, "determined", run_id=rid)
        await emit_task(session_id, "working", on=True, run_id=rid)
    await emit_emotion(session_id, _expr_for(name, "focus", "thinking"), run_id=rid)
    await _peek(ctx, session_id, "" if is_action else name)
    if is_action:
        await _emit_action_step(ctx, session_id, name, args, call_id=call_id)


_PLAN_NOTE_EVERY = 6


def _refresh_plan_note(ctx, input_items: list) -> None:
    """Keep the open task list in front of her for the whole of a run — ONE copy, at the tail.

    The SINGLE owner of that note, for background jobs and live turns alike. The input is built once
    and then looped on, so a note placed at build time describes the plan as it stood before the first
    model call: a list she opened at iteration 1 was never described, and she was told to tick it once
    or not at all. Measured live: an open plan sat at 0/2 with both steps really done.

    Refreshing is a MOVE, not an addition — the previous note is removed before the new one is
    appended, so the input carries one note and not forty. Re-placed when the list revision moves, and
    otherwise every _PLAN_NOTE_EVERY iterations. A subagent is skipped: the plan is not its work."""
    from kotoba.core import task_list

    keep = getattr(ctx, "_plan_note", None)
    if keep is None:
        keep = ctx._plan_note = {"rev": None, "item": None, "idle": 0}
    rev = task_list.revision(ctx.session_id)
    keep["idle"] += 1
    if rev == keep["rev"] and (rev is None or keep["idle"] < _PLAN_NOTE_EVERY):
        return
    old = keep["item"]
    if old is not None:
        at = next((n for n, it in enumerate(input_items) if it is old), None)
        if at is not None:
            del input_items[at]
    note = task_list.prompt_note(ctx.session_id) if rev is not None else ""
    keep["rev"], keep["idle"], keep["item"] = rev, 0, None
    if note:
        keep["item"] = {"role": "developer", "content": note}
        input_items.append(keep["item"])


_MAX_TOOL_OUTPUT_CHARS = 16_000
_BROWSER_OUTPUT_CHARS = 24_000


def _output_cap(name: str) -> int:
    """Per-tool cap for the result fed back to the model: larger for browser_* (snapshots), default else."""
    return _BROWSER_OUTPUT_CHARS if isinstance(name, str) and name.startswith("browser__") else _MAX_TOOL_OUTPUT_CHARS


def _cap_tool_text(text: str, max_chars: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    return head + f"\n\n[…truncated {len(text) - max_chars} more chars — summarize what you have or fetch a more specific item]"


def _call_output(call_id: str, result, fail_note: str, max_chars: int = _MAX_TOOL_OUTPUT_CHARS) -> dict:
    """Build the function_call_output item fed back to the model. Normally `output` is a string; when the
    tool returned images (a ToolResult), `output` becomes a content-part list with the text plus one
    `input_image` per image (the Responses API accepts that), so the vision model can SEE the screenshot."""
    text = _cap_tool_text(str(result) if result is not None else "", max_chars) + fail_note
    images = getattr(result, "images", None)
    if images:
        content = [{"type": "input_text", "text": text or "[image below]"}]
        for url in images:
            content.append({"type": "input_image", "image_url": url, "detail": "auto"})
        return {"type": "function_call_output", "call_id": call_id, "output": content}
    return {"type": "function_call_output", "call_id": call_id, "output": text}


_MAX_RL_RETRIES = _limit("KOTOBA_RATELIMIT_RETRIES", 6)


def _is_rate_limit(exc: BaseException) -> bool:
    """Word boundaries on the short tokens: a bare `"tpm" in s` / `"429" in s` matched ids and byte
    counts, sending a plain failure through six exponential backoffs (~60 s) before the same error."""
    if _RateLimitError is not None and isinstance(exc, _RateLimitError):
        return True
    s = str(exc).lower()
    return (
        "rate limit" in s or "ratelimit" in s or "tokens per min" in s
        or re.search(r"\b(tpm|rpm|429)\b", s) is not None
    )


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Server's suggested wait: the `retry-after` header if present, else parsed from the message
    ("Please try again in 2.869s" / "in 960ms"). None when we can't tell."""
    resp = getattr(exc, "response", None)
    try:
        ra = resp.headers.get("retry-after") if resp is not None else None
        if ra:
            return float(ra)
    except Exception:
        pass
    m = re.search(r"try again in ([\d.]+)\s*(ms|s)\b", str(exc))
    if m:
        v = float(m.group(1))
        return v / 1000.0 if m.group(2) == "ms" else v
    return None


_ELIDED_VIEW = "[Earlier browser view elided to save context — take a fresh browser_snapshot for current refs.]"


_BROWSER_VIEW_FRAGMENTS = ("snapshot", "screenshot", "run_code", "evaluate")


def _capture_folder(tool_name: str) -> str:
    """Which screenshots/<source>/ subfolder a captured image belongs in, inferred from the tool that
    produced it — so Files stays organized by source (a Blender shot lands in screenshots/blender, a
    browser shot in screenshots/browser). Unknown sources fall back to screenshots/desktop."""
    n = (tool_name or "").lower()
    if "blender" in n:
        return "blender"
    if "browser" in n:
        return "browser"
    return "desktop"


def _is_browser_view(name: str) -> bool:
    """A browser tool whose output is a heavy, soon-stale page view: an a11y snapshot, a screenshot, or a
    run_code/evaluate page inspection (DOM dumps, link lists — often 6-10k chars). All go stale once the page
    changes, and re-sending each every iteration is what drained the tokens-per-minute window (the long
    pacing waits). Keeping only the LATEST is the big TPM saver — see _elide_browser_history."""
    return isinstance(name, str) and name.startswith("browser__") and any(f in name for f in _BROWSER_VIEW_FRAGMENTS)


def _elide_browser_history(input_items: list, view_call_ids: list[str]) -> None:
    """Keep only the LATEST browser view in the input; replace every earlier snapshot/screenshot output with
    a short stub. Their refs are already stale after the page changed, and re-sending each full snapshot +
    image every iteration is exactly what blew the tokens-per-minute ceiling. Idempotent."""
    if len(view_call_ids) <= 1:
        return
    stale = set(view_call_ids[:-1])
    for it in input_items:
        if (
            isinstance(it, dict)
            and it.get("type") == "function_call_output"
            and it.get("call_id") in stale
            and it.get("output") != _ELIDED_VIEW
        ):
            it["output"] = _ELIDED_VIEW


def _result_text(name: str, ok: bool, result: str) -> str:
    """The result line: the REAL output (stdout/exit, bytes written, matches), trimmed for the panel.

    What the trim leaves out is not lost — `_full_result` carries the whole thing on the same frame,
    behind an expander. `delegate` is the exception and says an outcome rather than a result: the
    helper's write-up is already on its own roster row and in her reply.

    The MCP lines shorten what the SERVER did; they no longer read a HUMAN's decision out of her
    prose. Rewording "held off" as "cancelled by user" put a ✓ over a cancellation and blamed the user
    for a card that had merely expired. An ending at a card comes from the witness instead."""
    text = (str(result) if result is not None else "").strip()
    if not ok:
        first = text.splitlines()[0] if text else "no result"
        return f"  ! {first[:160]}"
    if name in ("shell", "execute_code"):
        lines = [ln for ln in text.splitlines() if ln.strip()][:4]
        return "\n".join("  " + ln[:160] for ln in lines) if lines else "  (no output)"
    if name == "delegate":
        return "  the helper came back"
    if name in ("mcp_find", "mcp_install"):
        low = text.lower()
        if low.startswith("connected"):
            return "  connected ✓"
        if "already connected" in low:
            return "  already connected"
        if "couldn't find" in low or "looked in the mcp registry" in low or "don't recognize" in low:
            return "  no match"
        if "didn't offer any" in low:
            return "  connected but no tools"
        return "  " + (text.splitlines()[0][:60] if text else "done")
    return "  " + (text.splitlines()[0][:160] if text else "done")


def _full_result(result, cap: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    """The WHOLE output, carried beside the line _result_text trims, for the panel's expander.

    The trim is not wrong — the card is a compact row in a narrow panel — but it was the only copy to
    leave this process. Measured: asked four times how many things were in one folder she answered 23,
    14+9, 92 and 25, and the listing that would have settled it reached the screen as 18 of its 443
    characters. Carried on the same frame, since an expander that can fail to load stops being trusted.

    The cap is the MODEL's own, per tool, so the two copies cannot disagree: everything she was allowed
    to read is readable here. When it does clip it says so — a silent second truncation recreates the
    whole defect. A row whose line came from _refusal_line carries none of this: nothing ran."""
    text = (str(result) if result is not None else "").strip()
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n\n[{len(text) - cap} more characters — the panel stops here, the tool did not]"


_OFFLINE_MSG = (
    "I'm here, but my language model isn't connected yet — run kotoba setup and I'll be able to "
    "think properly."
)


_GENERIC_VOICE = {
    "before": "Okay, let me take care of that...",
    "heartbeat": ["Working on it...", "Almost there..."],
    "after": "Done! Here's what I found:",
    "fail": "Hmm, that didn't work — let me try another way.",
}


def _is_mcp(spec) -> bool:
    """Did this tool arrive from an MCP server? (`toolset` is "mcp:<server>".)

    One helper because two frames need the same answer and only one of them used to compute it. The
    narration gate in _run_iterations read it off the spec inline; execute_with_heartbeat read nothing,
    so the canned lines it emits outlived the gate that exists to suppress them."""
    return spec is not None and str(getattr(spec, "toolset", "")).startswith("mcp:")


def _voice_for(tool_name: str, soul_patterns: dict) -> dict:
    """Voice patterns for a tool: SOUL override → the tool module's own ANNOUNCE/... → generic default."""
    p = soul_patterns.get(tool_name)
    if p:
        return p
    spec = registry().get(tool_name)
    mod = getattr(spec, "module", None)
    if mod is not None and hasattr(mod, "ANNOUNCE"):
        return {
            "before": getattr(mod, "ANNOUNCE", ""),
            "heartbeat": list(getattr(mod, "HEARTBEAT", []) or []),
            "after": getattr(mod, "COMPLETE", ""),
            "fail": getattr(mod, "FAIL", ""),
        }
    return _GENERIC_VOICE


def _after_line(patt: dict, outcome: str) -> str:
    """The canned sentence she SAYS OUT LOUD once a tool call is over, or nothing at all.

    Chosen by the same witness the terminal row is drawn from. The row moved onto `_step_outcome`
    while the voice was left on `ok`, so a command that exited 2, an action the user declined and one
    still on an unanswered card all reached the transcript as "Done! Here's what came back:".

    Only two endings have a line, because only two have anything to confirm. `refused`, `pending` and
    `interrupted` are not failures of hers, so a `fail:` pattern would blame the tool for the user's
    decision — and `refused` is four endings under one mark, which no canned sentence can cover. Each
    already returns a first-person sentence she relays in the user's own language."""
    if outcome == "ok":
        return patt.get("after", "")
    if outcome == "failed":
        return patt.get("fail", "")
    return ""


def _expr_for(tool_name: str, state: str, default: str) -> str:
    """The face a tool wears for a process state, from its EXPRESSIONS profile (ToolSpec.expressions).
    Falls back to a sensible default so the face always tracks the real work — a stumble looks like a
    stumble, not a cheerful focus frame.

    Only `focus` and `fail` are emitted. Success is deliberately silent: the face for a finished turn
    comes from the tag she actually wrote (`_emit_face_emotion` → stream.TAG_TO_FACE), which is one
    source for voice, browser and terminal instead of three that can disagree. Tools still DECLARE a `done` face —
    nothing reads it today, and adding an emission here would fight the tag a moment later. `fail` is
    the asymmetry with a reason: a stumble has to show mid-turn, before she has said anything."""
    spec = registry().get(tool_name)
    profile = getattr(spec, "expressions", None) or {}
    return profile.get(state, default)


async def _emit_face_emotion(session_id: str | None, text: str, run_id: str = "") -> None:
    """Drive the avatar's face from the tag she wrote, resolved by the one map every surface reads. Fall
    back to a lightweight inference only when the reply carries no tag at all."""
    emotion = emotion_from_text(text)
    if emotion is None:
        emotion = await extract_emotion(text)
    await emit_emotion(session_id, emotion, run_id=run_id)


async def _offline_reply(stream_queue: asyncio.Queue, session_id: str | None) -> str:
    await stream_queue.put(_OFFLINE_MSG)
    await emit_emotion(session_id, "neutral")
    return _OFFLINE_MSG


async def agentic_loop(
    input_items: list,
    session_id: str | None,
    db,
    stream_queue: asyncio.Queue,
    soul_patterns: dict,
    max_iterations: int | None = None,
    allow_risk: set[str] | None = None,
    toolset_filter: str | None = None,
    mcp: object = None,
    mode: str = "companion",
    channel: str = "voice",
    exclude_tools: frozenset[str] | None = None,
    run_id: str | None = None,
    seam: str = "",
    register: str = "voice",
    narrate_tools: bool = True,
) -> str:
    """The ONE agentic loop. There is no keyword intent router: she gets her full toolset every turn,
    gated only by real AVAILABILITY through each tool's check(), and the MODEL decides what to call.
    Setup is lazy; the approval gate's `workspace_root` is the jailed workdir, which is a security
    line and not a convenience — a host-executed auto-safe read must stay inside it.

    `run_id` is this run's origin mark, on every frame the run emits — the only way a client tells a
    detached job's frames from the turn it is awaiting. `seam` marks where one iteration's text ends
    and carries stream.FLUSH_SENTINEL, without which a reply ending "…the code." sits in the spoken
    filters until the NEXT iteration's text arrives (measured: 10.09s of silence on a 10s tool).
    The finally runs even on CancelledError, or a cancelled turn leaves terminal cards spinning."""
    client = get_client()
    if client is None:
        return await _offline_reply(stream_queue, session_id)

    from kotoba.core import interaction
    from kotoba.core.approval import ApprovalGate
    from kotoba.core.interaction import request_approval
    from kotoba.core.workspace import resolve_workdir

    ctx = ToolContext(db=db, session_id=session_id, client=client, mode=mode, channel=channel, mcp=mcp)
    ctx.soul_patterns = soul_patterns
    ctx.run_id = run_id or uuid.uuid4().hex[:8]
    ctx.announce_turn = bool(exclude_tools and "start_work" in exclude_tools)
    ctx.excluded_tools = frozenset(exclude_tools or ())
    ctx.user_texts = _recent_user_texts(input_items)
    if ctx.user_texts:
        ctx.user_text = ctx.user_texts[0]
    ctx.workdir = resolve_workdir(session_id)
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    ctx._grants: dict[str, tuple[str, str]] = {}

    async def _audit(action: str, risk: str, ok: bool, who: str, detail: str) -> None:
        try:
            await db.insert_audit_log(
                action=action, risk_kind=risk, approved=ok, approver=who, session_id=session_id,
                detail=detail,
            )
        except Exception:
            pass

    async def _ask(action: str, risk: str, family: str | None = None):
        """The gate's asker: one card, and which of its five endings happened.

        The ending is the fourth answer and not an afterthought — a No and a card nobody answered both
        come back False, so without it the trail recorded a typed `df -h` that expired unread as
        `approved=0, approver="user"`: the log claiming the user saw it and refused it.

        It refuses to "ask" when nobody can hear: with no consumer registered the emit is a silent
        no-op, the wait runs its full window, and the (False, False) would again be audited as the
        user's own No. Raising instead lands on the gate's asker-failed path → approver="error". This
        is the DEFAULT path: `cannot_block` is true only under `/v1`, so local voice blocks inline."""
        from kotoba.core.events import has_listener

        if not has_listener(session_id):
            interaction.note_no_run(ctx, interaction.UNREACHABLE)
            raise RuntimeError("no approval channel for this session — cannot ask")
        card: dict = {}
        approved, always = await request_approval(
            session_id, action, channel=channel, family=family, card=card,
            el_agent=interaction.el_agent_turn(ctx),
        )
        verdict = interaction.verdict_of(card, approved)
        if verdict != interaction.APPROVED:
            interaction.note_no_run(ctx, verdict)
        return (approved, always, bool(card.get("always_exact")), verdict)

    async def _persist_approval(family: str) -> None:
        await db.save_approved_command(family, "command")

    async def _persist_exact(command: str) -> None:
        await db.save_approved_command(command, "exact")

    def _note_verdict(family: str, who: str) -> None:
        if who in ("saved", "saved-exact"):
            ctx._grants[getattr(ctx, "call_id", "")] = (who, family)

    from kotoba.core.sandbox import backend_name

    try:
        saved_rows = await db.list_approved_commands()
        saved_cmds = {r["pattern"] for r in saved_rows
                      if r.get("pattern") and r.get("scope") != "exact"}
        saved_exact = {r["pattern"] for r in saved_rows
                       if r.get("pattern") and r.get("scope") == "exact"}
    except Exception:
        saved_cmds, saved_exact = set(), set()
    ctx.approval = ApprovalGate(
        ask=_ask,
        audit=_audit,
        saved_commands=saved_cmds,
        saved_exact=saved_exact,
        on_persist=_persist_approval,
        on_persist_exact=_persist_exact,
        on_verdict=_note_verdict,
        host_exec=(backend_name() == "local"),
        workspace_root=ctx.workdir,
    )
    if allow_risk is None:
        allow_risk = {"read", "write", "exec", "network"}
    if max_iterations is None:
        max_iterations = _default_max_iterations(mode)

    try:
        return await _run_iterations(
            client, ctx, input_items, stream_queue, soul_patterns,
            max_iterations, mode, allow_risk, toolset_filter,
            exclude_tools=exclude_tools, seam=seam, register=register,
            narrate_tools=narrate_tools,
        )
    finally:
        _open = getattr(ctx, "_open_steps", None)
        if _open:
            for _cid in list(_open):
                try:
                    await emit_task(
                        session_id, "step", phase="done", id=_cid, ok=False, interrupted=True,
                        outcome="interrupted",
                        result=INTERRUPTED_TEXT, text=INTERRUPTED_TEXT, run_id=ctx.run_id,
                    )
                except Exception:
                    pass
            _open.clear()
        try:
            await _peek(ctx, session_id, "")
        except Exception:
            pass
        if getattr(ctx, "_working_emitted", False) and not ctx.subagent_id:
            from kotoba.core import work_state

            if not work_state.is_running(session_id):
                await emit_task(session_id, "working", on=False, run_id=ctx.run_id)
        try:
            from kotoba.core import citations

            for _rel in citations.complete_reports(ctx):
                _publish_file(session_id, ctx.workdir, _rel)
        except Exception:
            pass
        await ctx.cleanup()


def _preexists(ctx, raw) -> bool:
    """Whether write_file's target already exists — read BEFORE the tool runs, because afterwards the
    file exists either way. Decides the artifact frame's action: FilesPanel badges "created" as NEW,
    and every overwrite was wearing that badge. An out-of-jail or unresolvable path answers False; the
    write itself refuses those, fails the call, and no artifact frame is emitted."""
    if not raw or getattr(ctx, "workdir", None) is None:
        return False
    try:
        from kotoba.core.path_security import validate_within_dir

        return validate_within_dir(str(raw).strip(), ctx.workdir).exists()
    except Exception:
        return False


async def _run_iterations(client, ctx: ToolContext, *args, **kwargs) -> str:
    """The tool-calling loop, plus the one thing no caller may forget: a SUBAGENT run that dies in
    here is a tool call that RAN and FAILED.

    delegate catches that exception on purpose — it has a chibi to close and citations to merge — and
    hands the parent "(helper failed: …)". A non-empty string, so the wrapper graded it ok=True: a
    green ✓, an `executed` audit row, the duplicate guard's "it is DONE" branch, and the spoken
    COMPLETE line over a crash. The witness lives HERE, not in the tool, because the swallow is the
    tool's legitimate business. Not a refusal: the helper ran and may have written files before dying.
    Cancellation cannot reach here — a barge-in raises CancelledError, which `except Exception` misses."""
    try:
        return await _iterate(client, ctx, *args, **kwargs)
    except Exception as exc:
        if getattr(ctx, "subagent_id", None):
            note_tool_failure(f"{type(exc).__name__}: {exc}")
        raise


async def _iterate(
    client,
    ctx: ToolContext,
    input_items: list,
    stream_queue: asyncio.Queue,
    soul_patterns: dict,
    max_iterations: int,
    mode: str,
    allow_risk: set[str] | None,
    toolset_filter: str | None,
    model_role: str | None = None,
    exclude_tools: frozenset[str] | None = None,
    max_tool_calls: int | None = None,
    seam: str = "",
    register: str = "voice",
    narrate_tools: bool = True,
) -> str:
    """The tool-calling loop itself, split out so agentic_loop can guarantee sandbox cleanup.

    `max_tool_calls` defaults to the mode's cap; a caller that also sizes `max_iterations` by hand
    must keep iterations = calls + 1, because the gate that withdraws every tool fires on
    `sum(per_tool) >= max_tool_calls` and a smaller budget stops the run mid-chain with empty text.

    Built-in web_search runs server-side and never arrives as a function_call, so per_tool cannot cap
    it: _WEB_SEARCH_LIMIT does, per run, binding each delegate helper too. Its row opens on
    `response.output_item.added` and closes on `.done` — opened on `.done` alone, both frames go out
    after the search finished and every client shows a dead screen, then one ✓."""
    session_id = ctx.session_id
    sub = ctx.subagent_id
    rid = getattr(ctx, "run_id", "")
    final_text = ""
    mode_tool_calls, fail_limit = _caps_for(mode)
    if max_tool_calls is None:
        max_tool_calls = mode_tool_calls
    withheld = frozenset(getattr(ctx, "excluded_tools", None) or ()) | frozenset(exclude_tools or ())
    ctx.excluded_tools = withheld
    exclude_tools = withheld or None

    from kotoba.core import citations
    from kotoba.core import mcp_active as _mcp_active_mod
    _mcp_active = _mcp_active_mod.active(session_id) if mode == "work" else None

    browser_guidance_done = False
    if mode == "work":
        from kotoba.core.tool_guidance import _has_browser_tool

        await _ensure_work_browser(ctx)
        offered_names = {
            t.get("name") or t.get("type") for t in schemas_for(mode, allow_risk, toolset_filter, _mcp_active, exclude_tools)
        }
        _g = _work_guidance_text(offered_names)
        if _g:
            input_items.insert(1, {"role": "developer", "content": _g})
        browser_guidance_done = _has_browser_tool(offered_names)
        _cap = _deferred_servers_block(getattr(ctx, "mcp", None), _mcp_active)
        if _cap:
            input_items.insert(1, {"role": "developer", "content": _cap})
    if not sub:
        from kotoba.core import session_captures, visual_memory

        for _blk in (visual_memory.prompt_block(), session_captures.prompt_block(session_id)):
            if _blk:
                input_items.insert(1, {"role": "developer", "content": _blk})
    ctx.register = register
    ctx.model_role = model_role or mode
    # The canned before/after lines exist so a VOICE does not go silent under a slow tool. Written
    # into a chat they are English sentences in front of the answer, in a conversation that was in
    # another language: filler with nothing to fill, and the terminal draws a spinner anyway. The
    # heartbeats already read the register; the two that bracket the tool did not, so every written
    # turn that touched a tool opened in English whatever language it was being held in.
    narrate_tools = narrate_tools and register == "voice"
    ctx.narrate_tools = narrate_tools
    attempts: dict[str, int] = {}
    outcomes: dict[str, str] = {}
    per_tool: dict[str, int] = {}
    failures = 0
    web_searches = 0
    web_cap_noted = False
    browser_view_ids: list[str] = []

    extract_disabled = not _has_url(input_items)
    narrated_before: set[str] = set()
    narrated_after: set[str] = set()
    shot_n = 0
    from kotoba.core import file_library as _filelib

    _wd_track = bool(ctx.workdir) and not sub
    _wd_is_library = _wd_track and str(ctx.workdir) == str(_filelib.library_dir().resolve())
    _wd_mtimes = _filelib.snapshot_mtimes(ctx.workdir) if _wd_track else {}
    for _ in range(max_iterations):
        tool_calls: list = []
        response_text: list[str] = []

        if failures >= fail_limit or sum(per_tool.values()) >= max_tool_calls:
            use_tools = []
        else:
            if mode == "work":
                _mcp_active = _mcp_active_mod.active(session_id)
            offered = schemas_for(mode, allow_risk, toolset_filter, _mcp_active, exclude_tools)
            use_tools = [
                t for t in offered if not (extract_disabled and t.get("name") == "web_extract")
            ]
            if mode == "work" and not browser_guidance_done:
                from kotoba.core.tool_guidance import _has_browser_tool

                names_now = {t.get("name") for t in offered}
                if _has_browser_tool(names_now):
                    _bg = _work_guidance_text(names_now)
                    if _bg:
                        input_items.append({"role": "developer", "content": _bg})
                    browser_guidance_done = True
        if web_searches >= _WEB_SEARCH_LIMIT:
            use_tools = [t for t in use_tools if t.get("type") != "web_search"]
            if not web_cap_noted:
                web_cap_noted = True
                input_items.append({"role": "developer", "content": (
                    f"You've already run {web_searches} web searches this turn — that's the limit, and "
                    "web_search is no longer available. Do NOT look for more sources or try to search "
                    "again. SYNTHESIZE the answer (or write the report) from the results you already "
                    "have, citing the real source URLs you were given."
                )})
        _src_note = citations.pending_note(ctx)
        if _src_note:
            input_items.append({"role": "developer", "content": _src_note})
        if not sub:
            _refresh_plan_note(ctx, input_items)
        _elide_browser_history(input_items, browser_view_ids)
        turn_items = []
        _rl = 0
        while True:
            tool_calls, response_text, turn_items, ws_seen = [], [], [], 0
            ws_open: set[str] = set()
            try:
                from kotoba.core import ratelimit
                owed = await ratelimit.throttle(max_wait=65.0 if mode == "work" else 5.0)
                if owed:
                    await ratelimit.wait_out(owed)
                stream = await client.responses.create(
                    model=model_name(model_role or mode), input=input_items, tools=use_tools, stream=True,
                    **_web_search_kwargs(use_tools, web_searches),
                    **model_call_kwargs(mode, role=(model_role or mode))
                )
                ratelimit.note_stream(stream)
                async for event in stream:
                    if event.type == "response.output_text.delta":
                        await stream_queue.put(event.delta)
                        response_text.append(event.delta)
                    elif event.type == "response.output_text.annotation.added":
                        citations.collect_from_annotation(ctx, getattr(event, "annotation", None))
                    elif event.type == "response.output_item.added":
                        it = event.item
                        wsid = str(getattr(it, "id", "") or "")
                        if not sub and getattr(it, "type", "") == "web_search_call" and wsid:
                            ws_open.add(wsid)
                            await emit_task(session_id, "step", phase="start", id=wsid,
                                            step_kind="web", action="web search", text="web search",
                                            run_id=rid)
                            _track_step(ctx, wsid, "web search")
                    elif event.type == "response.output_item.done":
                        it = event.item
                        if it.type == "function_call":
                            tool_calls.append(it)
                            turn_items.append(it)
                        elif it.type == "message":
                            turn_items.append(it)
                            for part in (getattr(it, "content", None) or []):
                                for ann in (getattr(part, "annotations", None) or []):
                                    citations.collect_from_annotation(ctx, ann)
                        elif it.type == "reasoning":
                            turn_items.append(it)
                        elif getattr(it, "type", "") == "web_search_call":
                            ws_seen += 1
                            try:
                                did = _web_action_text(it)
                                logging.getLogger("kotoba").info(
                                    "websearch %d/%d %s", web_searches + ws_seen, _WEB_SEARCH_LIMIT, did[:120]
                                )
                                if sub:
                                    await emit_task(session_id, "subagent_step", id=sub, text=did, ok=True,
                                                    run_id=rid)
                                else:
                                    wsid = getattr(it, "id", None) or f"ws-{uuid.uuid4().hex[:6]}"
                                    if wsid not in ws_open:
                                        await emit_task(session_id, "step", phase="start", id=wsid, step_kind="web", action="web search", text="web search", run_id=rid)
                                    ws_open.discard(wsid)
                                    _open_now = getattr(ctx, "_open_steps", None)
                                    if _open_now is not None:
                                        _open_now.pop(wsid, None)
                                    await emit_task(session_id, "step", phase="done", id=wsid, ok=True,
                                                    outcome="ok",
                                                    result=did, text="web search", run_id=rid)
                            except Exception:
                                pass
                break
            except Exception as e:
                if not _is_rate_limit(e) or (response_text and not sub):
                    raise
                _rl += 1
                if _rl > _MAX_RL_RETRIES:
                    raise
                from kotoba.core import ratelimit
                ratelimit.note_headers(getattr(getattr(e, "response", None), "headers", None))
                _wait = ratelimit.backoff_seconds(_rl, _retry_after_seconds(e))
                logging.getLogger("kotoba").warning(
                    "rate limited (TPM); backoff %.2fs then retry (attempt %d/%d)", _wait, _rl, _MAX_RL_RETRIES
                )
                await asyncio.sleep(_wait)

        web_searches += ws_seen
        if ws_seen and mode == "work":
            from kotoba.core import work_state

            work_state.note_acted(session_id)
        final_text = "".join(response_text)

        if not tool_calls:
            if not sub:
                await _emit_face_emotion(session_id, final_text, run_id=rid)
            break

        input_items.extend(turn_items)
        if seam and final_text.strip():
            await stream_queue.put(seam)
        await stream_queue.put(_stream.FLUSH_SENTINEL)

        _parallel_results: dict = {}
        _announced: set[str] = set()
        _closed: set[str] = set()
        _dtcs = [tc for tc in tool_calls if tc.name == "delegate"]
        if len(_dtcs) > 1 and "delegate" not in withheld:
            _items = [(_tc, _parse_tool_args(_tc.arguments)) for _tc in _dtcs]
            _budget = _DELEGATE_LIMIT - per_tool.get("delegate", 0)
            _spawn, _ = _select_delegates_to_spawn(_items, _budget, lambda s: attempts.get(s, 0) >= 1)

            if len(_spawn) > 1:
                if not sub:
                    for _tc, _a in _spawn:
                        await _announce_action(ctx, session_id, "delegate", _a, _tc.call_id)
                        _announced.add(_tc.call_id)
                ctx.call_id = ""
                _sem = asyncio.Semaphore(max(1, _DELEGATE_CONCURRENCY))

                async def _run_delegate(_c, _a):
                    async with _sem:
                        _ok, _res = await execute_with_heartbeat("delegate", _a, stream_queue, soul_patterns, ctx)
                    if not sub:
                        _line = _result_text("delegate", _ok, _res)
                        await emit_task(session_id, "step", phase="done", id=_c, ok=_ok,
                                        outcome="ok" if _ok else "failed",
                                        result=_line, text=_line, full=_full_result(_res), run_id=rid)
                        try:
                            ctx._open_steps.pop(_c, None)
                        except AttributeError:
                            pass
                        _closed.add(_c)
                    return _ok, _res

                _gathered = await asyncio.gather(
                    *[_run_delegate(tc_.call_id, a_) for tc_, a_ in _spawn], return_exceptions=True
                )
                for (_tc, _a), _r in zip(_spawn, _gathered):
                    _parallel_results[_tc.call_id] = (
                        (False, f"(helper failed: {_r})") if isinstance(_r, BaseException) else _r
                    )

        for tc in tool_calls:
            args = _parse_tool_args(tc.arguments)

            logging.getLogger("kotoba").info("toolcall %s %s", tc.name, str(args)[:120])
            sig = f"{tc.name}:{json.dumps(args, sort_keys=True)}"
            attempts[sig] = attempts.get(sig, 0) + 1
            per_tool[tc.name] = per_tool.get(tc.name, 0) + 1

            if attempts[sig] > 1:
                logging.getLogger("kotoba").info("toolskip %s (exact repeat — NOT executed)", tc.name)
                input_items.append({
                    "type": "function_call_output",
                    "call_id": tc.call_id,
                    "output": (
                        "You ALREADY called this exact tool with these arguments and it is DONE — the "
                        "earlier result for this call still stands. Do NOT call it again and do NOT say "
                        "it failed or that you 'already tried'. Just continue from that result and answer "
                        "the user now, in your own words."
                        if outcomes.get(sig) == "ok" else
                        _withheld_text(tc.name) if tc.name in withheld else
                        _never_ran_text(tc.name) if outcomes.get(sig) in ("refused", "pending") else
                        "You already made this EXACT call and it did NOT succeed — the earlier attempt "
                        "failed or never ran, so repeating it verbatim will not help. Do NOT retry the "
                        "identical call: try a DIFFERENT approach (change the arguments or use another "
                        "tool), or answer the user now with what you already have."
                    ),
                })
                continue

            browser_high_cap = mode == "work" and tc.name.startswith("browser__")
            tool_cap = _BROWSER_TOOL_LIMIT if browser_high_cap else (
                _DELEGATE_LIMIT if tc.name == "delegate" else (
                _TODO_TOOL_LIMIT if tc.name in ("todo", "cronjob") else _PER_TOOL_LIMIT))
            if per_tool[tc.name] > tool_cap and tc.call_id not in _parallel_results:
                logging.getLogger("kotoba").info(
                    "toolskip %s (per-tool cap %d — NOT executed)", tc.name, tool_cap)
                input_items.append({
                    "type": "function_call_output",
                    "call_id": tc.call_id,
                    "output": (
                        f"THIS CALL DID NOT RUN — you have called '{tc.name}' more times than the cap "
                        f"({tool_cap}) allows, so it was refused before executing and NOTHING it would "
                        "have done has happened. Do NOT keep preparing or re-checking, and do NOT tell "
                        "the user this one is done: if it mattered, say plainly that this last one did "
                        "not go through. Otherwise take the next CONCRETE action (write the file / run "
                        "it) or answer with what you actually have."
                        if tc.name != "delegate" else
                        "You've delegated enough helpers already. Do NOT spawn more — SYNTHESIZE the helper "
                        "summaries you already have into the final deliverable (write the report / answer) now."
                    ),
                })
                continue

            _spec = registry().get(tc.name)
            is_action = _spec is not None and _spec.risk in ("write", "exec", "network")

            if sub:
                await emit_task(session_id, "subagent_step", id=sub, text=_step_text(tc.name, args), ok=None,
                                run_id=rid)
            elif tc.call_id not in _announced:
                await _announce_action(ctx, session_id, tc.name, args, tc.call_id, is_action)
            is_mcp = _is_mcp(_spec)
            suppress_narration = is_mcp or is_reasoning_model(role=(model_role or mode))
            if tc.name == "mcp_install":
                from kotoba.core import session_state
                from kotoba.core.mcp.known import resolve_known

                _r = resolve_known(str(args.get("name") or ""))
                canon = _r[0] if _r else None
                if canon and session_state.mcp_announced(session_id, canon):
                    suppress_narration = True
                elif canon:
                    session_state.mark_mcp_announced(session_id, canon)

            if (tc.name not in narrated_before and not suppress_narration and narrate_tools
                    and tc.name not in withheld and dispatchable(tc.name) is not None):
                await narrate(stream_queue, _voice_for(tc.name, soul_patterns).get("before", ""))
                narrated_before.add(tc.name)

            overwrote = tc.name == "write_file" and _preexists(ctx, args.get("path"))
            exit_codes: list[int] = []
            if tc.call_id in _parallel_results:
                ok, result = _parallel_results[tc.call_id]
            else:
                ctx.call_id = tc.call_id  # so a tool that DEFERS can complete this row after the turn
                with record_exits() as exit_codes:
                    ok, result = await execute_with_heartbeat(tc.name, args, stream_queue, soul_patterns, ctx)
            logging.getLogger("kotoba").info(
                "toolresult %s ok=%s len=%d", tc.name, ok, len(str(result) if result is not None else "")
            )
            from kotoba.core import deferred_exec as _deferred

            deferred = _deferred.is_pending(ctx, tc.call_id)
            outcome = _step_outcome(ctx, tc.call_id, ok, deferred, exit_codes)
            outcomes[sig] = outcome
            if not ok and outcome != "refused":
                failures += 1
                if tc.name == "web_extract":
                    extract_disabled = True
                if not sub:
                    await emit_emotion(session_id, _expr_for(tc.name, "fail", "sad"), run_id=rid)

            if is_action:
                if not _nothing_ran(ctx, tc.call_id):
                    try:
                        await ctx.db.insert_audit_log(
                            action=f"{tc.name} {json.dumps(args)[:180]}",
                            risk_kind=_spec.risk, approved=False, approver="agent-loop",
                            session_id=session_id, detail="executed" if ok else "executed:failed",
                        )
                    except Exception:
                        pass
                _refused = _refusal_line(ctx, tc.call_id)
                _detail = _refused or _result_text(tc.name, ok, result)
                if sub:
                    await emit_task(session_id, "subagent_step", id=sub, text=_detail, ok=ok, run_id=rid)
                elif tc.call_id not in _closed:
                    await emit_task(
                        session_id, "step", phase="done", id=tc.call_id, ok=ok, pending=deferred,
                        outcome=outcome,
                        note=_grant_note(ctx, tc.call_id),
                        result=_detail, text=_detail,
                        full="" if _refused else _full_result(result, _output_cap(tc.name)),
                        run_id=rid,
                    )
                    try:
                        ctx._open_steps.pop(tc.call_id, None)
                    except AttributeError:
                        pass
                if ok and tc.name in ("write_file", "patch") and args.get("path"):
                    action = ("edited" if overwrote else "created") if tc.name == "write_file" else "edited"
                    path = str(args["path"])
                    await emit_task(session_id, "artifact", path=path, action=action, run_id=rid)
                    try:
                        # Read back from disk — write_file may have appended sources. ctx.read_text is OUR
                        # async helper: it fixes encoding itself, and passing one is a TypeError this `except` would swallow.
                        content = await ctx.read_text(path)
                        if content is None and tc.name == "write_file":
                            content = args.get("content")
                        content = content if isinstance(content, str) else None
                        await asyncio.to_thread(_publish_file, session_id, ctx.workdir, path, content)
                    except Exception:
                        pass
            elif not sub:
                await _peek(ctx, session_id, "")
                if mode == "work" and outcome != "refused":
                    from kotoba.core import work_state

                    work_state.note_acted(session_id)

            shot_note = ""
            shot_images = getattr(result, "images", None) if ok else None
            if tc.name in ("recall_image", "view_capture"):
                shot_images = None
            if shot_images and not sub:
                from kotoba.core import file_library, file_store

                saved = []
                for url in shot_images:
                    shot_n += 1
                    name = f"screenshots/{_capture_folder(tc.name)}/screenshot-{shot_n}-{uuid.uuid4().hex[:6]}.png"
                    file_store.stash_image(session_id, name, url)
                    await emit_task(session_id, "artifact", path=name, action="created", run_id=rid)
                    await asyncio.to_thread(file_library.save_image, name, url)
                    from kotoba.core import session_captures

                    session_captures.record(session_id, name, "")

                    async def _bg_caption(_url=url, _name=name, _sid=session_id):
                        try:
                            cap = await session_captures.caption_image(client, _url)
                            if cap:
                                session_captures.set_caption(_sid, _name, cap)
                        except Exception:
                            pass

                    asyncio.create_task(_bg_caption())
                    saved.append(name)
                if saved:
                    shot_note = f" [Saved to the user's Files as {', '.join(saved)} — refer to it by that name.]"

            if ok and _wd_track and tc.name in ("shell", "execute_code"):
                try:
                    _track = _filelib.note_changes if _wd_is_library else _filelib.import_workdir
                    new_files = await asyncio.to_thread(_track, ctx.workdir, _wd_mtimes)
                    for rel, action in new_files:
                        await emit_task(session_id, "artifact", path=rel, action=action, run_id=rid)
                    if new_files:
                        _wd_mtimes = await asyncio.to_thread(_filelib.snapshot_mtimes, ctx.workdir)
                except Exception:
                    pass
            if ok and not sub and tc.name in ("shell", "execute_code"):
                await emit_task(session_id, "files_changed", run_id=rid)

            if tc.name not in narrated_after and not suppress_narration and narrate_tools:
                line = _after_line(_voice_for(tc.name, soul_patterns), outcome)
                if line:
                    await narrate(stream_queue, line)
                    narrated_after.add(tc.name)

            if ok:
                fail_note = shot_note
            elif outcome == "refused":
                fail_note = ""
            elif tc.name == "web_extract":
                why = getattr(ctx, "web_extract_error", "") or "that page couldn't be read"
                fail_note = (
                    f" [YOU DID NOT READ THAT PAGE — {why}. Do NOT retry it, and do NOT attribute "
                    "anything to it: saying \"the page says/explains…\" would be a lie. Use web_search "
                    "now to find the same information from another source, then answer in ONE piece: a "
                    "short clause saying the link didn't open and why, followed by what you actually "
                    "found and where. Never end this turn announcing that you are about to search — the "
                    "correction and the answer belong together, in this turn.]"
                )
            else:
                fail_note = " [tool failed — do not retry the same call; try another approach.]"

            input_items.append(_call_output(tc.call_id, result, fail_note, _output_cap(tc.name)))
            if _is_browser_view(tc.name):
                browser_view_ids.append(tc.call_id)

    return final_text


_NEUTRAL_FILLER = ("Mmm...", "Hmm...", "Mm...", "Hmmm...")


def _heartbeat_lines(tool_name: str, soul_patterns: dict, register: str, role: str | None,
                     is_mcp: bool = False) -> list:
    """The canned "still working" phrases for this tool, or none — the ONE place that decides.

    They exist so a voice does not go silent while a tool runs long enough to read as a hang; there is
    no protocol floor. A reasoning model narrates its own tool use in the user's language, so the
    English literals are a duplicate there — but suppressing them outright assumed something else
    fills the gap, true only while an ElevenLabs agent talks: locally, a 10s tool went silent 10.09s.

    Those paths — MCP tools included — get a wordless hum instead, and a `text` register there gets
    nothing: it draws a spinner. Its trailing dots are ASCII, since the spoken sentence split is
    [.!?] plus newline, and a "…" would sit unreleased."""
    from kotoba.core import app_settings

    lines = list(_voice_for(tool_name, soul_patterns).get("heartbeat", []) or [])
    if not (is_mcp or is_reasoning_model(role=role)):
        return lines
    if register != "voice":
        return []
    local_voice = app_settings.runtime_value("voice_mode", "KOTOBA_VOICE_MODE", "local") == "local"
    return list(_NEUTRAL_FILLER) if local_voice else []


def _asking_human(ctx) -> bool:
    """Is this turn blocked on a card the person has to answer off-channel?

    core.interaction.has_pending is exactly that question and no other: `request_approval`,
    `request_input` (ask_secret, request_credential) and the OAuth wait each register a Future there
    and block on it, while `open_input_card`/`open_link_card` register nothing because their answer is
    meant to come back as an ordinary turn. So the non-blocking cards — the ones that WANT to be
    spoken to — are excluded by construction rather than by a list this could fall behind."""
    from kotoba.core import interaction

    return interaction.has_pending(getattr(ctx, "session_id", None))


def _withheld_text(tool_name: str) -> str:
    return (
        f"You do not have '{tool_name}' in this conversation, so NOTHING ran and there is no "
        "result. This is not something that failed and not an obstacle you hit — you simply were "
        "not given it here. Do not call it again, do not reach for another tool to get the same "
        "thing, do not describe what it would have returned, and do not tell them it worked. Say "
        "plainly that you cannot do that part here, and go on with what you do have."
    )


def _never_ran_text(tool_name: str) -> str:
    return (
        f"You already made this EXACT call to '{tool_name}' and it did NOT succeed — it never ran: it "
        "ended at the user's card, or was refused before it started, and the earlier result says which "
        "ending that was. Do NOT repeat it, do NOT reach for another tool or different arguments to get "
        "the same thing done, and do NOT say it is done: answer from the earlier result, in your own words."
    )


def _name_the_stray(tool_name: str, task: asyncio.Task, caught: BaseException | None = None) -> None:
    """Report a cancellation nobody asked for, from the one place its origin still exists.

    `shield` hands the waiter a BARE cancellation and keeps the tool task's own exception, so a line
    written anywhere above this frame can only ever name the await that noticed. The inner one is
    recoverable ONCE, by asking the finished task for its result — which is why a caller that already
    awaited the task has to hand over what it caught: asking again returns a fresh, empty one.

    Silent when somebody DID ask — a turn the user stopped comes through here too."""
    me = asyncio.current_task()
    if me is not None and me.cancelling():
        return
    inner = caught if (caught is not None and caught.__traceback__ is not None) else None
    if inner is None and task.done() and task.cancelled():
        try:
            task.result()
        except BaseException as raised:
            inner = raised
    logging.getLogger("kotoba").error(
        "tool %s ended CANCELLED with nobody requesting it (task.cancelled=%s, said=%r)",
        tool_name, task.cancelled(), getattr(inner, "args", None), exc_info=inner or True)


async def execute_with_heartbeat(
    tool_name: str,
    args: dict,
    stream_queue: asyncio.Queue,
    soul_patterns: dict,
    ctx: ToolContext,
    timeout: int = TOOL_TIMEOUT,
) -> tuple[bool, str]:
    """Run a tool while emitting heartbeat phrases; returns (ok, result_for_the_model).

    Cadence is 9s, then every 21s: no protocol floor survives, so the curve is only about how long a
    wait goes unacknowledged before it reads as a hang, and each phrase is its own TTS request — four
    hums inside twelve seconds was measured as grating. Beats are DEFERRED, not skipped, while the
    tool blocks on a HUMAN: the card already says everything, and narrating at it read as work.

    An unregistered name is a real FAILURE, never a quiet (True, ""), and so is a tool that REFUSED
    its own arguments — `ok` is what the ✓, the `executed` audit row and the duplicate guard rest on.
    Dispatch goes through `registry.dispatchable`, which knows whether the family is switched OFF too."""
    if tool_name in (getattr(ctx, "excluded_tools", None) or ()):
        note_tool_refusal(ctx)
        return False, _withheld_text(tool_name)
    spec = dispatchable(tool_name)
    if spec is not None and getattr(ctx, "mode", "companion") == "companion" \
            and isinstance(spec.toolset, str) and spec.toolset not in COMPANION_TOOLSETS:
        # The turn's exclusion is a snapshot taken before it began, so a tool registered since — an
        # MCP server reconnecting mid-turn — is in no list and was reaching a guest by name. Mode is
        # not a snapshot: it is what this turn IS, and none of these are offered to a spoken one.
        note_tool_refusal(ctx)
        return False, (
            f"'{tool_name}' is not part of a conversation — it belongs to a background job. NOTHING "
            "ran and there is no result. Do not call it again and do not describe what it would have "
            "returned; if the work really needs it, start the job and it will be there."
        )
    if spec is None and tool_name in registry():
        note_tool_refusal(ctx)
        return False, (
            f"'{tool_name}' is switched OFF in the user's settings, so nothing ran and there is no "
            "result. Do NOT call it again this turn. Tell them plainly that those tools are turned off "
            "and they can switch the family back on in Settings if they want it."
        )
    if spec is None:
        return False, (
            f"There is no tool called '{tool_name}' — it does not exist, or its server just "
            "disconnected. Nothing ran and there is no result. Do NOT call it again and do NOT claim "
            "it worked: use a tool you actually have, or answer the user in words from what you "
            "already know."
        )
    if spec.built_in:
        return True, ""
    tool = spec.module

    from kotoba.core.interaction import el_agent_turn

    timeout = _tool_budget(tool, spec, args, timeout, getattr(ctx, "channel", "voice"),
                           el_agent=el_agent_turn(ctx))

    def _worked(failed: list) -> bool:
        return not failed and not tool_refused(ctx, getattr(ctx, "call_id", ""))

    if getattr(tool, "INTERACTIVE", False):
        with record_tool_failures() as failed:
            try:
                result = await tool.execute(args, ctx)
            except Exception:
                return False, "The tool errored."
            if not result or not str(result).strip():
                return False, "The tool returned nothing."
            return _worked(failed), str(result)

    heartbeat = _heartbeat_lines(
        tool_name, soul_patterns,
        getattr(ctx, "register", "voice"), getattr(ctx, "model_role", None), _is_mcp(spec),
    ) if getattr(ctx, "narrate_tools", True) else []
    spoken = 0
    elapsed = 0.0
    next_hb = _HEARTBEAT_FIRST

    with record_tool_failures() as failed:
        task = asyncio.create_task(tool.execute(args, ctx))
        try:
            while not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=_HEARTBEAT_TICK)
                except asyncio.TimeoutError:
                    elapsed += _HEARTBEAT_TICK
                    if heartbeat and elapsed >= next_hb:
                        if _asking_human(ctx):
                            next_hb = elapsed + _HEARTBEAT_FIRST
                        else:
                            idx = spoken if spoken < len(heartbeat) else len(heartbeat) - 1
                            await narrate(stream_queue, heartbeat[idx])
                            spoken += 1
                            next_hb = elapsed + _HEARTBEAT_EVERY
                    if elapsed >= timeout:
                        return False, "The tool timed out."
                except asyncio.CancelledError:
                    _name_the_stray(tool_name, task)
                    raise
                except Exception:
                    return False, "The tool errored."

            try:
                result = await task
                has_content = bool(str(result).strip()) or bool(getattr(result, "images", None))
                if not result or not has_content:
                    return False, "The tool returned nothing."
                return _worked(failed), result
            except asyncio.CancelledError as caught:
                _name_the_stray(tool_name, task, caught)
                raise
            except Exception:
                return False, "The tool errored."
        finally:
            if not task.done():
                task.cancel()
