"""Single source of tool-usage guidance, injected tool-aware.

Two problems this fixes: the work loop ran with almost no system guidance — no anti-hallucination, no
per-tool workflow — so it stalled and fabricated success ("Done! I'm signed in" when it had failed);
and the browser workflow text was hardcoded in three places.

Guidance is injected ONLY when the relevant tool or toolset is loaded, so the model is never told how
to drive a browser it does not have, and an anti-fabrication block always rides along.
`guidance_for(offered_tool_names, mode)` returns just the blocks whose tools are present, and the
browser text lives here ONCE."""
from __future__ import annotations

# THE single source for "how to drive the browser" — reused by the MCP schema enrichment AND the work-loop
# system message. The leading " — " is for appending after a tool's own description in _to_function_schema.
BROWSER_GUIDANCE = (
    " — YOUR OWN BROWSER: you work in your OWN browser profile — everything open in it is YOURS (your tabs, "
    "your logins), so use it freely. ONCE, at the very START (before you navigate), you MAY browser_snapshot "
    "to see if a tab you already have open is useful for this task and reuse it. That's a one-time check — "
    "do NOT re-open tabs or re-do steps mid-task. Above all: DON'T REPEAT WORK YOU ALREADY DID. If a step "
    "succeeded (a page loaded, a screenshot was taken), move ON — never navigate to the same page twice or "
    "take a second screenshot of the same thing. When the user asks for ONE screenshot, take EXACTLY ONE. "
    "HOW TO USE THE BROWSER: it is driven by the accessibility SNAPSHOT. Before acting on ANY element you "
    "MUST first call browser_snapshot and read the element references it lists (they look like \"e5\", "
    "\"e12\"). Then pass that EXACT reference as `target`. NEVER invent a CSS/XPath selector (\"#email\", "
    "input[name=q], .btn) from memory — selectors you didn't read from the live snapshot fail with \"does "
    "not match any elements\". After the page changes (navigation, a search submit, a click that loads new "
    "content) the old references are stale: take a FRESH browser_snapshot before the next action. To VERIFY "
    "a result (logged in? results on screen?), take a browser_snapshot and READ it before claiming success. "
    "When the task is to operate a website (open it, log in, search, post), your FIRST tool call is "
    "browser_navigate — do NOT web_search or use shell to 'research' it. To fill a field, snapshot, find the "
    "field's ref, then browser_type(target=<ref>, text=...) or browser_fill_form with refs; if a step errors "
    "(e.g. 'not an <input>'), the ref was wrong — re-snapshot and pick the actual input's ref. Prefer the "
    "accessibility flow; the raw-JS tools (run_code/evaluate) are a LAST resort. When the snapshot text isn't "
    "enough to understand the page — a captcha or 'is this you?' checkpoint, an unexpected layout, or you're "
    "stuck after one retry — take a browser_take_screenshot and LOOK at it (you have vision) to decide the "
    "next step, instead of repeating the same action. Don't repeat an action that didn't work; change "
    "something (re-snapshot, screenshot, a different element) or report honestly what's blocking you. "
    "If you hit a CAPTCHA or human-verification you can't do yourself, do NOT end the task — call ask_user "
    "to ask the person to solve it on screen and reply when done, WAIT for them, then re-snapshot and "
    "CONTINUE. Stopping at a captcha without asking is wrong. "
    "TO CAPTURE the screen, ALWAYS use the browser_take_screenshot tool — it saves the image to the user's "
    "Files so they can see it. For a normal full-page capture just call browser_take_screenshot with NO "
    "target — it grabs the current viewport; you do NOT need a snapshot or an element ref first, and you do "
    "NOT need to re-navigate. One call = one saved image. NEVER take a screenshot via run_code/"
    "page.screenshot({path}) — that writes to the wrong place and the capture is lost. Use run_code ONLY for things no other browser tool can do (e.g. "
    "scrolling the page); for clicking, typing, reading the page, and screenshots, use the dedicated tools."
)

TARGET_DESC = (
    "The EXACT element reference copied from the most recent browser_snapshot (e.g. \"e5\"). You MUST get it "
    "from a snapshot first. Do NOT guess or recall a CSS/XPath selector (#id, input[name=...]); only a "
    "reference present in the latest snapshot will work."
)

# gpt-family models in an agent loop NARRATE intent instead of acting (the stuck Facebook login).
# Short on purpose — shipped on every work task.
ACTION_ENFORCEMENT = (
    "ACT, DON'T DESCRIBE: you are running this task yourself with tools — not narrating it for someone else "
    "to do. If you say you will do something (open the page, log in, search, type the email), make the "
    "matching tool call in the SAME turn — NEVER end a turn with a promise of future action. Do NOT ask the "
    "user to do something you can do with a tool: YOU fill the form via the browser; the user only supplies "
    "values through the ask_user / ask_secret boxes. Keep calling tools until the task is actually complete "
    "AND verified — don't stop at a plan, a stub, or a 'next I will…'."
)

# Asked who won the LAST Ballon d'Or, she searched "...2024" and answered a stale winner, with
# citations. The year came from training, not from the question — and a cited stale answer reads as true.
SEARCH_RECENCY_GUIDANCE = (
    "SEARCHING FOR THE LATEST: when the question is about the latest / last / current / most recent / newest "
    "anything (who holds a title, who won, the newest version or model, today's price, the current holder of "
    "a post), do NOT put a year in the search query. Any year you'd add comes from your training, not from "
    "the question, and it silently pins the search to a stale edition. Search the plain question (\"current "
    "Ballon d'Or winner\", not \"Ballon d'Or winner 2024\") and read the date OFF the results. Include a year "
    "ONLY when the user named one. If the results are older than the current date you were given, say so "
    "instead of presenting them as current — a confidently cited out-of-date answer is worse than none."
)

# The work-loop's final text becomes the spoken summary — a premature ending = a fabricated "I did it".
VERIFICATION_GUIDANCE = (
    "GROUNDING & HONESTY (this governs your final summary): the deliverable is a real result backed by real "
    "tool output — never a description of one. Before you claim you did something (logged in, found the "
    "person, posted, wrote the file), CONFIRM it with an actual tool result first — re-check the real state "
    "with a tool, don't assume the last action worked. NEVER fabricate or assume success: if a step errored, "
    "was blocked (a checkpoint, a captcha), or you could not verify it, say so plainly in your summary — an "
    "honest \"I got as far as X but hit Y\" is correct and useful; a cheerful \"all done!\" that didn't "
    "actually happen is a failure."
)


def _has_browser_tool(names: set[str]) -> bool:
    # built-in schemas carry no "name", so the set can contain None — guard for str
    return any(isinstance(n, str) and n.startswith("browser__") for n in names)


def skills_prompt(skills: list[dict]) -> str:
    """The skills block for the work loop: list the eligible skills and tell the model to load
    the matching one with skill_view before it acts. Empty list → "".

    This block and the work subsystem's BIAS TO ACTION are the same rule and must keep saying so. They
    contradicted each other for months — this one asked for the skill, the subsystem called a skill_view
    a forbidden 'preparation' step — and the prohibition won every run. What went with it was everything
    a skill knows that nothing else does: a Spanish research request came back as an English report,
    because the language rule lives in the one page the job was told not to open. "Step zero" is the
    name that lets both survive."""
    if not skills:
        return ""
    lines = [f"- {s['name']} — {s['description']}" if s.get("description") else f"- {s['name']}" for s in skills]
    return (
        "SKILLS (playbooks you can load) — this list is your STEP ZERO. Scan it; if one matches the job, "
        "load that ONE with skill_view(name) first and follow what it says. If none matches, there is no "
        "step zero: act straight away. Never a second skill, never a re-read.\n" + "\n".join(lines)
    )


WORKSPACE_ORG_GUIDANCE = (
    "KEEP FILES ORGANIZED: the user's Files panel is the workspace root. Put things in the right folder "
    "(create one just by writing into that path):\n"
    "- research reports → research/ (the research skill handles this)\n"
    "- finished-work reports → reports/ (make_report saves there automatically) — but make_report is "
    "OPTIONAL and only for the END of a LONG job: do the work FIRST, then report once if it's worth it. "
    "Never call make_report before/while working, and skip it entirely for short tasks.\n"
    "- screenshots are auto-filed under screenshots/<source>/ (browser, blender, …) — don't re-save them\n"
    "- a multi-file project → its own folder (e.g. a site under its name). Don't scatter loose files at root.\n"
    "For THROWAWAY temp files (intermediate downloads, scratch, mktemp) use your scratch dir — it's wired as "
    "$TMPDIR for shell/execute_code (TMPDIR/TMP/TEMP all point there). Anything you want the user to keep "
    "goes in the workspace; anything temporary goes in $TMPDIR (it never clutters their Files)."
)


# The model has `delegate` but never reaches for it on its own — this teaches the fan-out rule.
DELEGATION_GUIDANCE = (
    "SPLIT INDEPENDENT WORK WITH delegate: when the task has SEPARATE, self-contained parts that don't "
    "depend on each other — e.g. \"research/compare A, B and C\" (one helper per item), or gathering from "
    "several sources in parallel — hand each part to a `delegate` helper (pick its toolset: research/web/"
    "file/code/browser) and then synthesize the helpers' summaries yourself into the final deliverable. "
    "This is for genuinely independent sub-tasks; a simple linear task you just do yourself. Don't delegate "
    "a single quick step, and don't delegate the final synthesis — that's your job. "
    "IMPORTANT — go WIDE, not one-at-a-time: issue ALL the helper `delegate` calls TOGETHER in a SINGLE step "
    "(multiple tool calls at once) so they run in PARALLEL and finish together. Do NOT delegate one, wait for "
    "it, then delegate the next. Decide UP FRONT: if you're going to split the work into helpers, delegate them "
    "as your FIRST action — do NOT research or search any of those parts yourself first (that's the helpers' "
    "job and doing it too is wasted, duplicated work). After the helpers return, just SYNTHESIZE their summaries. "
    "TOOLSET per helper: for looking things up on the internet, give each helper the 'research' (or 'web') "
    "toolset — fast web_search, light. Reach for the 'browser' toolset ONLY when a subtask genuinely must "
    "operate a live page (log in, fill a form, click through, screenshot a specific site); browser helpers are "
    "heavy and slow, and several at once strain the token budget. "
    "CITE THE HELPERS' SOURCES: after research helpers return, you'll receive an internal note listing the "
    "REAL URLs their searches surfaced — when the deliverable is a report, cite those exact URLs in its "
    "sources (Fuentes) section: the `## Fuentes` list of a .md, and the `sources` field if you present it "
    "with make_report. Never invent, guess, or paraphrase a URL, and never copy the note itself."
)


def guidance_for(offered_tool_names: set[str], mode: str) -> str:
    """The guidance to inject into the work-loop system message, tool-aware. Returns "" for companion
    (its own SOUL prompt governs) — this targets the work-loop, which otherwise runs near-guidance-free.

    - ACTION_ENFORCEMENT + VERIFICATION_GUIDANCE + WORKSPACE_ORG: always in work mode.
    - SEARCH_RECENCY_GUIDANCE: when `web_search` is offered (companion gets it from her own prompt).
    - DELEGATION_GUIDANCE: when the `delegate` tool is offered (split independent work into subagents).
    - BROWSER_GUIDANCE: only when a browser_* tool is actually offered.
    """
    if mode != "work":
        return ""
    blocks: list[str] = [ACTION_ENFORCEMENT, VERIFICATION_GUIDANCE, WORKSPACE_ORG_GUIDANCE]
    if "web_search" in offered_tool_names:
        blocks.append(SEARCH_RECENCY_GUIDANCE)
    if "delegate" in offered_tool_names:
        blocks.append(DELEGATION_GUIDANCE)
    if _has_browser_tool(offered_tool_names):
        # Strip the leading " — " (that prefix is for appending after a tool description, not standalone).
        blocks.append(BROWSER_GUIDANCE.lstrip(" —"))
    return "\n\n".join(blocks)
