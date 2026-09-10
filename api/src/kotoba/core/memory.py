"""Proactive memory — the "information collector".

After each reply, a lightweight background pass reads the user's message and, without being asked and
without adding latency, captures durable facts about the USER (preferences, relationships, job,
ongoing situations) into USER.md, the user's own name into user_profile.name, and a name the user
gives the COMPANION ("you'll be called X") into soul_config.name.

The rule: only things still true in about seven days. Questions, requests, smalltalk and ephemeral
task results are ignored."""
from __future__ import annotations

import asyncio
import json

_EXTRACT_PROMPT = """You are the memory of an AI companion. Read the user's latest message and pull out
anything worth remembering long-term — NOT only personal traits about the user, but ANYTHING important:
their projects, ongoing situations, decisions, goals, things they're building or care about, context
you'd want next time. File each item under a topic ALREADY IN USE when one fits — a near-synonym
("reminder" beside "reminders") splits one memory across two files. Invent a new topic only when none
of these fits:
{topics}

NEVER extract any of these — they are the conversation, not the person, and a memory full of them
buries the real facts:
- something they asked you to DO, then or now ("wants you to run ls", "asked you to send a message")
- a passing reaction, a joke, a provocation, an insult, or anything said to get a rise out of you
- anything about what you or the conversation are doing right now ("you started answering again")
- an inference about their identity, health, beliefs or politics that they did not plainly state
  about themselves as a fact they want kept
A fact earns a place only if it would still be true and still be useful in a month.

The message:
"{msg}"

ALREADY REMEMBERED (do NOT extract a fact that any of these already covers — even if it is reworded,
split or merged differently, or written in another language; the agent may have just saved it):
{known}

Return ONLY a JSON object of this exact shape:
{{
  "user_name": <the USER's own name if they just told you it, else null>,
  "companion_name": <ONLY if the user is DIRECTLY and UNAMBIGUOUSLY renaming YOU, the companion, with an
                     explicit naming act addressed to you — e.g. "your name is Yuki now", "you will be called X",
                     "from now on you're X", "I'll name you X". This must be a clear
                     command/decision to change YOUR name. Otherwise null. Be STRICT — when unsure, null.
                     null in ALL of these: mentioning any other person's or thing's name; talking about a
                     third party ("my friend Yuki", "a tool called X"); greeting/calling you by your CURRENT
                     name; complimenting a name ("what a lovely name"); asking what your name is; quoting or
                     hypotheticals. A name appearing in the message is NOT enough — it must be a direct
                     rename of you.>,
  "facts": [<objects {{"fact": "...", "topic": "..."}} for short facts worth keeping — about the user OR
             about their projects/work/anything important. `topic` is a one or two word category — an
             existing one from the list above when one fits, a new one only when none does. Examples:
             {{"fact": "Has a dog named Luna", "topic": "pets"}},
             {{"fact": "Works as a security engineer", "topic": "work"}},
             {{"fact": "Building Kotoba, an AI VTuber companion", "topic": "kotoba-project"}},
             {{"fact": "Wants the landing page in an idol-pop style", "topic": "kotoba-project"}}.
             Empty list if nothing durable.>]
}}

Write every fact in ENGLISH regardless of the message's language, and make each fact ATOMIC — one entry
per distinct fact ("likes jasmine tea and the color indigo" → two entries), never a combined sentence.
Only include things that will still be true in a week. Ignore questions, jokes, commands, and
small talk. If nothing is worth saving, return {{"user_name": null, "companion_name": null, "facts": []}}."""


async def extract_and_save_memory(user_msg: str, db) -> None:
    """Best-effort, fire-and-forget. Never raises into the request path."""
    from kotoba.core import user_memory
    from kotoba.core.llm import utility_extract

    if not user_msg or not user_msg.strip():
        return
    # Feed the extractor what's already stored: the LLM does the cross-language/rewording de-dup the
    # write-time keyword check can't — else "recuerda: me gusta X" gets a Spanish AND an English entry.
    known = await asyncio.to_thread(user_memory.recent_facts)
    known_block = "\n".join(f"- {f}" for f in known) if known else "(nothing yet)"
    # Free-form topics were what this prompt asked for, and it got 78 of them for 175 facts — 35 of
    # those names are siblings of a bigger one ("reminder"/"reminders", six spellings of "research").
    topics_block = await asyncio.to_thread(user_memory.topic_summary, 12, False) or "(none yet)"
    # Responses API via utility_extract — chat.completions `max_tokens` 400s on the default gpt-5.x,
    # which left memory silently DEAD out-of-the-box.
    raw = await utility_extract(
        _EXTRACT_PROMPT.format(msg=user_msg[:600], known=known_block, topics=topics_block),
        max_output_tokens=800,
    )
    if not raw:
        return
    try:
        s = raw[raw.find("{"): raw.rfind("}") + 1] or raw  # tolerate stray prose around the JSON
        data = json.loads(s)
        if not isinstance(data, dict):
            return
    except Exception:
        return

    user_name = data.get("user_name")
    if isinstance(user_name, str) and user_name.strip():
        try:
            await db.upsert_user_profile(key="name", value=user_name.strip())
        except Exception:
            pass

    companion_name = data.get("companion_name")
    if isinstance(companion_name, str) and companion_name.strip():
        try:
            await db.update_soul_config(name=companion_name.strip())
        except Exception:
            pass

    facts = data.get("facts")
    if isinstance(facts, list) and facts:
        for item in facts:
            if isinstance(item, dict):
                text = str(item.get("fact") or "").strip()
                topic = str(item.get("topic") or "").strip()
            elif isinstance(item, str):
                text, topic = item.strip(), ""
            else:
                continue
            if not text:
                continue
            try:
                # The extractor turns a whole conversation into candidates, so it is the one caller that
                # needs task narration screened out. An explicit memory_write does not.
                if topic:
                    await asyncio.to_thread(user_memory.append_fact, text, topic, filter_ephemeral=True)
                else:
                    await asyncio.to_thread(user_memory.append_fact, text, filter_ephemeral=True)
            except Exception:
                pass
