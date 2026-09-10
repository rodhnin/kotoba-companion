---
name: research
description: Act as a real research agent — plan sub-questions, search several angles, prefer authoritative sources, synthesize, and (on request) produce a structured .md report with citations. Use whenever the user asks you to research, investigate, look into, dig deep, compare, or get the full picture on something — anything past a one-line fact lookup.
when_to_use: research / investigate / "look into" / "dig deep" / compare / "give me the full picture" / "make me a report" on a topic
---

# Research

Your playbook for being a real research agent. Grounded in how production deep-research systems work
(plan → search → rank sources → synthesize → cite) and tuned for a LIVE VOICE call, where the user hears
every minute you spend. The single most important rule is the split between the two phases below — get
that right and everything else follows.

## ⏱ The dead-time rule (read this first)

The user is waiting in a live conversation. Nothing cuts the turn off, and keeping the line alive is the
runtime's job, not yours — but minutes of one-sided silence are still a terrible call. So:

- **NEVER do heavy, multi-source research inside the live voice turn.** One quick search is fine; a chain
  of searches + reading pages is NOT — it leaves the user listening to nothing.
- The live turn does the LIGHT phase only (one broad search, a short spoken summary, the offer).
- The DEEP phase (3–6 sources, cross-checking, the written report) ALWAYS runs in the background via
  `start_work` — no one is waiting on the line there, and the result is announced when it's ready.

## Phase 1 — Companion (live, spoken, FAST)

1. **One broad search.** Run `web_search` ONCE with a broad query (breadth-first — start wide, you'll go
   deep later). Don't chain searches in the live turn; you're getting the gist, not the full picture.
2. **Note the few sources you used** — outlet/title + what each contributed. Prefer authoritative,
   primary sources (the official site, the original report, a reputable outlet) over SEO/aggregator pages.
3. **Synthesize a SHORT spoken summary** — 3–6 sentences, the user's language, natural for voice: no URLs
   read aloud, no bullet lists, no code. The gist, not a wall of text. Flag uncertainty honestly if
   sources are thin or conflicting.
4. **Pick the PRINCIPAL source** — the single most authoritative/relevant one — and know *why* it's best.
5. **Offer the follow-up**, in one breath, two choices:
   - **open the principal source** — you'll open it in a new tab (you name it + say why it's the best
     starting point), or
   - **a detailed report** — you'll research it properly in the background and leave a full `.md` with all
     the sources in their Files.
   Neither is fine too — just move on.

- On **"open the source"** → call `open_link(url, why)`. That shows a card the user accepts or declines
  before any tab opens — **never open a link without that confirmation**, and always say what the link is.
- On **"write it up for me now"** (they want the polished artifact/PDF in THIS turn, not a background
  `.md`) → call `make_report`, and fill its **`sources`** field with the real URLs from the internal
  "Real URLs captured" note — one `{title, url, note}` per source. That field is what makes them
  clickable in the report and the PDF; a report built on a search that leaves `sources` empty cites
  nothing, and saying in its text that it "includes the links" without filling it is refused.
- On **"the report"** → call `start_work`. **Phrase the goal IN THE USER'S LANGUAGE** (the background
  worker only sees the goal — write it in another language and the report comes back in that one) and
  make the deliverable + language explicit. Asked in English, that reads:
  *"Follow the research skill: research <topic> across 3–6 authoritative sources and SAVE a structured
  .md report IN ENGLISH (with the real URLs) in a single write_file at `research/<topic>-<date>.md`."*
  Asked in any other language, the whole sentence — the named language included — is written in it.
  Say you're on it and you'll tell them when it's ready, then let the turn end — do NOT research it now.

**Summarize and offer FIRST — don't jump straight to `start_work`.** Even for "look into it properly" /
"give me the whole picture", do the quick spoken summary and the offer first; only start the background
report once the user actually wants it (or clearly asked for a written report / .md up front). The point
is they hear something useful immediately and choose whether the full file is worth the wait.

## Phase 2 — Work (background, writes the .md)

Runs inside `start_work`, where you have compute budget and no voice-turn pressure. Follow the
plan → execute → rank → publish pattern.

> **The deliverable is the FILE, not a spoken summary.** You are NOT done until `write_file` has saved the
> `.md` report to disk. Returning a summary without writing the file is a FAILURE of this task — the user
> asked for a report they can open in Files. This job starts a fresh conversation, so `skill_view research`
> is its STEP ZERO — open this skill once before you act, then end the work only after the file is written.

1. **Plan sub-questions.** Break the topic into 3–5 concrete sub-questions covering different angles
   (definition/context, current state, differing viewpoints, implications). Scale effort to the topic —
   a simple topic needs 3 searches, a broad one 6+; don't over- or under-invest.
2. **Execute one search per sub-question.** Start broad, then narrow based on what you find. After each
   result, take a beat to judge quality and spot gaps, then refine the next query. Open a page with the
   browser tools only when a source is worth reading in depth.
3. **Rank + cross-check sources.** Prefer authoritative/primary; cross-check claims across sources; note
   where they agree and where they differ. Drop weak/SEO-only sources.
4. **Publish a structured `.md` — in ONE write.** Gather all your sources and finish the research FIRST,
   then write the complete report in a SINGLE `write_file`. Don't write a draft and rewrite it; if you
   genuinely must fix something afterwards, use `patch` for the small change — never a second full
   `write_file` of the same file.
   - **Path:** save it INSIDE the `research/` folder, named `<topic-in-kebab-case>-<YYYY-MM-DD>.md`
     (e.g. `research/green-tea-benefits-2026-06-14.md`). The folder keeps every investigation together,
     organized by topic and date. Create the folder by just writing into that path.
   - **Language: write the ENTIRE report — title, section headers AND prose — in the user's language**
     (the language stated in your goal / the language they asked in). If they asked in Spanish, EVERYTHING
     is in Spanish; if in English, everything in English. Do NOT default to English, and never mix (Spanish
     headers over English body is wrong). Translate the section names below to that language.
   - **Body** (section names shown in Spanish/English — use the user's):
     - `# <Title>`
     - `## Resumen ejecutivo` / `## Executive summary` — 3–5 sentences, the bottom line.
     - `## Findings` — findings in a few `###` sub-sections by sub-question. Synthesize,
       don't dump; note real disagreements between sources.
     - `## Sources` — numbered; each line: **outlet/title** — the full real URL — one line
       on what it contributed and how reliable it is. Mark the **principal** source.
     - Optional `## Next steps`.
5. **Citation discipline — use the REAL URLs.** As you search, you'll be given an internal note listing
   the real URLs behind your citations. Use THOSE exact URLs in the sources list. Never write the
   inline citation markers, and **never fabricate a source or URL** — if you didn't actually see it, don't
   cite it. Every non-obvious claim should trace to a source in the list.
   - This holds for EVERY shape the deliverable takes: the `## Fuentes` section of a `.md`, and the
     **`sources` field of `make_report`** if you also present it as a report (same URLs, `{title, url,
     note}` each). Both carry real links; neither may claim links it doesn't carry.

6. **Remember what you investigated.** After the report is saved, call `memory_write` (topic `research`)
   with a one-line note: what you investigated, the file path, and the date — e.g. *"Researched
   meditation for anxiety; report at research/meditation-for-anxiety-2026-06-14.md (2026-06-14)."*
   So later, when the user refers back ("what did you find on meditation?", "expand that report"),
   you have the context and the file to work from.

When the file is written it appears in Files and the user is told it's ready (announced on reconnect if
the call dropped meanwhile).

## Continuing or expanding an existing investigation

If the user asks to **keep researching / add to / update / expand** something you already investigated,
that is BACKGROUND WORK too → call **start_work** (in the user's language), with a goal like *"expand the
report research/<file>.md: research <new angle> and add a section with patch"*. Do NOT do the
edit inline in the voice turn, and do NOT improvise with shell `sed`/`python` to rewrite the file. Inside
the work loop:
1. Find the existing report — recall it from memory (the `research` topic) or `search_files` in the
   `research/` folder — and `read_file` it (page through with start/limit if it's long) so you're working
   from its real current content. Use the file tools, not shell, to read and edit it.
2. Research the NEW angle (more searches, as above).
3. **Edit the SAME file with `patch`** — add the new `##`/`###` section where it belongs and append any
   new sources to the sources list. `patch` makes a targeted in-place edit (e.g. insert your new section
   right before `## Fuentes`). Do NOT `write_file` the whole thing again (wipes prior work, risks the
   double-write) and do NOT rewrite it via a shell/python script — `patch` is the tool for editing a file
   that already exists.
4. Update the memory note for that investigation if it materially changed.
The point: an investigation is a living document — you build on it in place with `patch`, in the
background, instead of scattering near-duplicate files or hand-editing it in the voice turn.

## If a tool fails

Don't abandon the task — adapt. A failed/empty search means: try a reworded or broader query, or a
different angle. Note what you couldn't verify rather than inventing it. Partial, honest results beat a
confident wrong answer.
