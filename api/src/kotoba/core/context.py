"""load_context — build the Responses API input list.

Voice context comes from ElevenLabs: its Conversational AI keeps the conversation and sends it in
`request.messages` every call, so we use that as the live history and fall back to DB recent_turns
only when it sent just the current message. We still own the system prompt.

`persisted` is the caller stating a fact it knows: the message is already a row, so it is in those
recent turns and must not be appended twice — inferring it from `recent[-1]` failed whenever another
row landed in the write-then-read window. `unprompted` is the opposite fact, derived HERE rather than
passed in: a trigger sentinel arrived, nobody spoke, and no transport can forget to say so."""
from __future__ import annotations

import os

from kotoba.models.schemas import ChatRequest
from kotoba.soul.prompt import build_system_prompt


def _max_history() -> int:
    """How many recent conversation turns to feed the model.

    The number stays; the reason it was written for is dead. It said ElevenLabs resends the whole
    conversation every turn and eventually drops the voice WebSocket over the latency — an ending only
    an EL agent call has. What actually justifies the cap is paid by every transport: an uncapped
    history is prompt cost on every turn and first-token latency on a reasoning model, and both grow
    without limit in a long session whoever is resending it (EL's own history over /v1, ours from
    fetch_recent_turns on the local socket and in the CLI). Deliberately NOT split by transport — the
    two would need the same number, and a second one is only a way to get it wrong."""
    try:
        return max(2, int(os.getenv("KOTOBA_MAX_HISTORY", "24")))
    except ValueError:
        return 24


_EVENT_HEAD = (
    "[BACKGROUND EVENT — the user did not write or say this. They asked you nothing. Something "
    "happened on its own and you are the one bringing it up.]"
)
_EVENT_OPEN = (
    " The conversation above is already open: this is not a new conversation and they have not just "
    "arrived."
)
_EVENT_TAIL = (
    "Speak to them about THIS now, in your own words. The event is the whole subject of this turn. "
    "The messages above it were already answered and none of them is waiting on you."
)
_EVENT_NOTHING = (
    "[BACKGROUND EVENT — the user did not write or say this. They asked you nothing.] It has already "
    "been dealt with and there is nothing left to tell them. Say nothing. The messages above were "
    "already answered and none of them is waiting on you."
)


def _event_message(notes: list[str], input_items: list[dict]) -> dict:
    """The background event, in the USER position, because that is the position the turn is missing.

    A trigger turn (`__work_done__`, `__reminder__`) exists only because something happened with no one
    speaking, and the sentinel is dropped from history — so the model saw the system prompt, the user's
    last real message, HER answer to it, and a developer note: a conversation ending on her own reply
    with nothing in the one position a reply answers. Measured live: one "Hola." drew three greetings
    two minutes apart, each re-answering that line with the news hung off the end as a postscript.

    Role is the fix, not wording. `developer` is an instruction ABOUT the turn; `user` is the turn. The
    "already open" clause is omitted with no conversation above, so a first event may still greet."""
    head = _EVENT_HEAD + (
        _EVENT_OPEN if any(m.get("role") in ("user", "assistant") for m in input_items) else ""
    )
    body = "\n".join(notes)
    return {"role": "user", "content": f"{head}\n{body}\n{_EVENT_TAIL}" if notes else _EVENT_NOTHING}


def inject_work_note(input_items: list[dict], session_id: str | None,
                     *, unprompted: bool = False) -> list[dict]:
    """Append a note about background work so the turn can report progress or announce results. No-op
    when there is nothing to say — unless `unprompted`, when the turn has nothing else in it.

    At the TAIL, never the head. Prepended at index 0 the note sat in front of the whole system prompt,
    and on a __work_done__ turn the LAST thing the model read was its own "I'm on it, watch the screen"
    line from when the job STARTED — so it announced the start again at the moment the job ended.

    `unprompted` puts the event in the user position instead. The WAITING-ON-APPROVAL note never travels
    there: its content is "say nothing further about this", and the subject slot would order silence and
    speech at once. The OPEN TASK LIST is not here either — this runs once, and the loop iterates."""
    from kotoba.core import deferred_exec, pending_reminder, work_state

    notes: list[str] = []
    waiting = deferred_exec.prompt_note(session_id) if session_id else ""
    if waiting:
        notes.append(waiting)
    # PEEK only (consume=False): a superseded/failed turn must not eat the reminder. All three transports
    # (server, voice WS, CLI) clear it only after the assistant row is written — only once she spoke.
    rem = pending_reminder.prompt_note(session_id, consume=False) if session_id else ""
    if rem:
        notes.append(rem)
    note = work_state.prompt_note(session_id) if session_id else ""
    if note:
        notes.append(
            f"{note} If the user is asking about it, tell them where it stands in your own words. If it just "
            f"finished or failed, ANNOUNCE the result/issue naturally now, then carry on. Do not call "
            f"start_work again for the same thing."
        )
    if not unprompted:
        if not notes:
            return input_items
        return input_items + [{"role": "developer", "content": "\n".join(notes)}]
    items = list(input_items)
    if waiting:
        items.append({"role": "developer", "content": waiting})
        notes.remove(waiting)
    return items + [_event_message(notes, items)]


class NoUserMessage(ValueError):
    """A turn was requested with nothing from the user in it."""


def latest_user_message(messages: list[dict]) -> dict:
    """The most recent user message, or raise.

    Raises a plain ValueError, not HTTPException: this is the turn BUILDER, and the CLI drives it in
    process, where a FastAPI exception has nobody to translate it. The route maps it back
    to a 400 — the HTTP shape belongs to the HTTP layer."""
    msg = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if msg is None:
        raise NoUserMessage("No user message in request")
    return msg


def text_of(content) -> str:
    """A plain-text rendering of a message's content for DB logging / FTS — never the raw base64 of an
    image. A multimodal list collapses to its text parts plus an '[image]' marker."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits = []
        for p in content:
            if isinstance(p, str):
                bits.append(p)
            elif isinstance(p, dict):
                if p.get("type") in ("image_url", "input_image", "image"):
                    bits.append("[image]")
                elif isinstance(p.get("text"), str):
                    bits.append(p["text"])
        return " ".join(b for b in bits if b).strip()
    return str(content) if content is not None else ""


def _normalize_content(content):
    """Pass conversation content to the Responses API, preserving images.

    Plain string → unchanged. A multimodal list (ElevenLabs forwards an uploaded image/PDF as content
    PARTS) → map each part to a Responses API input part: text → `input_text`, image → `input_image`
    (accepts a URL or a base64 data URL). Without this, `str(content)` would flatten the list and the
    image would be lost before it ever reached the vision model. Unknown parts are dropped. Returns a
    string when there's no image (back-compat) or a parts list when there is."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content is not None else ""

    parts: list[dict] = []
    has_image = False
    for p in content:
        if isinstance(p, str):
            if p:
                parts.append({"type": "input_text", "text": p})
            continue
        if not isinstance(p, dict):
            continue
        ptype = p.get("type")
        if ptype in ("image_url", "input_image", "image"):
            img = p.get("image_url")
            url = img.get("url") if isinstance(img, dict) else (img if isinstance(img, str) else None)
            url = url or p.get("url") or p.get("data")
            if url:
                parts.append({"type": "input_image", "image_url": url})
                has_image = True
            continue
        txt = p.get("text") if isinstance(p.get("text"), str) else None
        if txt:
            parts.append({"type": "input_text", "text": txt})

    if not has_image:
        return " ".join(pt["text"] for pt in parts if pt["type"] == "input_text").strip()
    return parts


_TRIGGER_SENTINELS = {"__work_done__", "__reminder__"}


def is_trigger_sentinel(text) -> bool:
    """True for a pure proactive-speech trigger token (e.g. '__work_done__'), so callers can drop it from
    history / DB / memory instead of treating it as a user message. These are frontend announce-triggers,
    never user content, and must not reach the model, the log, or memory. __image_only__/__file_only__
    are deliberately NOT sentinels — they pair with a real file and take the attachment path."""
    return isinstance(text, str) and text.strip() in _TRIGGER_SENTINELS


def _history_from_request(messages: list[dict]) -> list[dict]:
    """Keep only user/assistant turns from ElevenLabs (drop any system msg — we use our own, and drop pure
    proactive-speech trigger sentinels). Image content parts are preserved (see _normalize_content) so
    vision actually reaches the model."""
    history: list[dict] = []
    for m in messages or []:
        role = m.get("role")
        if is_trigger_sentinel(m.get("content")):
            continue
        content = _normalize_content(m.get("content"))
        if role in ("user", "assistant") and content:
            history.append({"role": role, "content": content})
    return history[-_max_history():]


async def load_context(
    request: ChatRequest, db, session_id: str | None, consume_attachments: bool = True,
    connected_mcp: list[dict] | None = None, register: str = "voice", persisted: bool = False,
    exclude_tools: frozenset[str] | None = None, personal: bool = True,
) -> list[dict]:
    unprompted = is_trigger_sentinel(text_of(latest_user_message(request.messages).get("content", "")))

    history = _history_from_request(request.messages)

    if len(history) <= 1:
        recent = await db.fetch_recent_turns(session_id, limit=_max_history())
        if recent:
            cur = history[-1] if history else None
            history = recent if (cur is None or persisted) else recent + [cur]

    import asyncio

    from kotoba.core import skill_docs, user_memory

    # Attachments reach us out-of-band (ElevenLabs can't forward files to a custom LLM). Only consume on
    # a real user-initiated turn — an EL silence turn would otherwise "steal" the pending file first.
    from kotoba.core import attachments

    pending = attachments.take(session_id) if consume_attachments else []
    bare_attachment = False
    if pending:
        last_user_idx = next(
            (i for i in range(len(history) - 1, -1, -1) if history[i]["role"] == "user"), None
        )
        if last_user_idx is None:
            # take() is one-shot and irreversible, so there must ALWAYS be somewhere to put the file:
            # with no user message to attach to, the parts were silently discarded and the upload was gone.
            history.append({"role": "user", "content": list(pending)})
            bare_attachment = True
        else:
            existing = history[last_user_idx]["content"]
            parts: list[dict] = []
            if isinstance(existing, str):
                # __image_only__/__file_only__ (file with no caption): DROP the sentinel — a hardcoded
                # English prompt here broke scene/language. Leave the turn image-only + add a note below.
                if existing and existing.startswith("__") and existing.endswith("__"):
                    bare_attachment = True
                elif existing:
                    parts.append({"type": "input_text", "text": existing})
            elif isinstance(existing, list):
                parts.extend(existing)
            parts.extend(pending)
            history[last_user_idx] = {"role": "user", "content": parts}

    soul = await db.fetch_soul_config()
    # Her person's profile and everything she has learned about him. Withholding the tool that reads
    # them back is not enough on a surface where a stranger gets a turn: they are already IN the
    # prompt, and the only thing between them and the room is a sentence asking her not to tell.
    user_profile_md = await db.fetch_user_profile_as_markdown() if personal else ""
    memory_facts = await asyncio.to_thread(user_memory.facts_for_prompt) if personal else []
    skills = await asyncio.to_thread(skill_docs.skill_titles)
    try:
        from kotoba.core.mcp import pending as _pending

        pending_mcp = _pending.list_pending()
    except Exception:
        pending_mcp = None
    # The prompt's capability claims are written from what the turn will really be offered — the same
    # call the loop makes each iteration. A check() may probe (docker), so keep it off the event loop.
    from kotoba.tools.registry import schemas_for

    # The turn may withhold tools from this speaker. Describing the whole toolset then tells her
    # she can do things this turn will refuse, and the refusal reads as a fault rather than a rule.
    offered = await asyncio.to_thread(schemas_for, "companion", exclude_tools=exclude_tools)
    available_tools = {t.get("name") or t.get("type") for t in offered}
    system_prompt = build_system_prompt(
        soul, user_profile_md, memory_facts, session_id, skills,
        connected_mcp=connected_mcp, pending_mcp=pending_mcp, register=register,
        available_tools=available_tools,
    )

    items = [{"role": "developer", "content": system_prompt}]
    card_note = _pending_card_note(session_id)
    if card_note:
        items.append({"role": "developer", "content": card_note})
    shared_note = attachments.prompt_note(session_id)
    if shared_note:
        items.append({"role": "developer", "content": shared_note})
    if bare_attachment:
        items.append({"role": "developer", "content": (
            "The user just shared an image/file with NO caption. Do NOT give a generic, detached "
            "description and do NOT switch languages. CONTINUE exactly what you and the user were doing "
            "right before this — the same scene/roleplay/task and the SAME language you've been speaking — "
            "and react to what you see from inside that context, staying in character."
        )})
    items += history
    return inject_work_note(items, session_id, unprompted=unprompted)


_CARD_LABEL_BUDGET = 140


def _pending_card_note(session_id: str | None) -> str:
    """The one line that tells her a card is still waiting in front of the user, or "" when none is.
    Needed because a card no longer dies when the user speaks over it, so she can talk straight past it.

    Per-turn and OUTSIDE the cached system prefix. It states the boundary out loud — she cannot answer
    the card — because "I approved it for you" is the untrue-about-her-own-reach failure tracked hardest.

    ONE CARD, ONE NOTE: whatever deferred_exec is already narrating is skipped, or the model gets "say
    nothing further" and "say it is still waiting" at once. Labels are clipped to their first line and
    a budget — an approval's label is the FULL command by design, and one heredoc put 8,400 characters
    into this note every turn. SURFACE-NEUTRAL: "card" is the one word every register shares."""
    from kotoba.core import deferred_exec, interaction

    narrated = deferred_exec.narrated_actions(session_id)
    labels = [
        l for l in interaction.pending_labels(session_id)
        if l and deferred_exec._norm_action(l) not in narrated
    ]
    if not labels:
        return ""

    def _clip(label: str) -> str:
        line = label.split("\n", 1)[0].strip()
        cut = line[:_CARD_LABEL_BUDGET].rstrip()
        return f"{cut}…" if cut != label.strip() else cut

    what = "; ".join(f"“{_clip(l)}”" for l in labels[:3])
    return (
        f"A card is in front of the user RIGHT NOW, waiting for their own answer: {what}. Only THEY can "
        "answer it, on the card itself — you cannot approve or decline it for them, and their talking "
        "to you does not make it go away. Say once, briefly and in your own words, that it is still "
        "waiting; then answer whatever they actually said. If nobody answers, it clears itself."
    )
