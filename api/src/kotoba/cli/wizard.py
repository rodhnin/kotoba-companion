"""First run: her, asking for the four things she is made of, before any of her exists.

Every word on these screens is HARDCODED ENGLISH in her voice: nothing here may be generated, because
the thing that would generate it is the thing being configured. Each answer lands where the product
already reads it — `user_name` to `user_profile['name']`, `companion_name` and `language` to soul config.

The order is brain → model → key: asking which brain but never which of its models leaves a stranger
paying whatever the default costs, and checking the key against the chosen model makes it fail here
rather than on her first real sentence. It is verified BEFORE it is stored, encrypted, never in .env.
"""
from __future__ import annotations

from kotoba import DIST_NAME

import logging
import re
import signal

from kotoba.cli import secret
from kotoba.cli.render.ascii_fold import fold
from kotoba.core import app_settings, first_run, llm, providers, voice_key
from kotoba.db.database import Database

log = logging.getLogger("kotoba.cli")

# Enough to prove the key answers. NOT free on a reasoning model, which is the only kind offered: the
# reasoning tokens come out of this same budget, so the probe returns `incomplete` with nothing visible
# and the thinking is billed. The 400 this check exists to catch arrives before any generation.
_PROBE_TOKENS = 16
_MAX_TRIES = 3          # a wrong answer asks again; it does not ask forever
_LANGUAGE_CODE = re.compile(r"^[A-Za-z]{2,3}$")     # the voice config's own rule, so a pin really pins
_NAME_LIKE = re.compile(r"[A-Za-zÀ-ÿ぀-ヿ一-鿿]")

# Her whole first run, in her own words. Hardcoded because what would generate it is being configured.
HELLO = (
    "Hi. I'm Kotoba — or I will be, in about a minute.\n\n"
    "Right now there's nothing of me on this machine: no brain to think with, no name for you, no "
    "idea which language the two of us speak. So I'm going to ask. Six questions and one you can "
    "skip, and the answers are what I'm made of."
)
LEAVING = "Ctrl+C stops this wherever you are, and nothing is written until you answer."
BRAIN = (
    "First: what should I think with?\n\n"
    "These two for now — they serve the same shape of API, and that shape is what my head is built "
    "on. More will come."
)
MODEL = (
    "{label}, good. Now which of theirs?\n\n"
    "One key opens all of them, so this is not another thing to sign up for — it picks the model "
    "behind the key, and the model is what the thinking costs you. They're cheapest first: the top "
    "one is the least {label} charges, and every other row says how much more that one wants for "
    "each word I say back."
)
KEY = (
    "{label}, then. Paste the key: I won't echo it while you type, I won't print it back, and it "
    "goes into my database encrypted — never into a file in plain text.\n\n"
    "No key yet? Enter on an empty line stops here until you have one — they're made at: {url}"
)
KEY_SHOWN = (
    "{label}, then. Paste the key — and one warning first: there's no terminal here to turn an echo "
    "off on, so it WILL show as you type. I won't print it back afterwards, and it goes into my "
    "database encrypted — never into a file in plain text.\n\n"
    "No key yet? Enter on an empty line stops here until you have one — they're made at: {url}"
)
YOUR_NAME = (
    "Good — that's my head sorted. Now, what should I call you?\n\n"
    "Whatever you actually go by. I'll use it, and I'll still know it next time."
)
MY_NAME = (
    "And me? I'm Kotoba — it means *words*. Keep it, or give me another name and I'll answer to it."
)
LANGUAGE = (
    "Nearly done: which language should the two of us speak?\n\n"
    "This decides two things, not one. It sets how I reply, and it sets how I HEAR you — pinned to a "
    "language, transcription listens for that one instead of guessing, which is what stops a short "
    "Spanish sentence coming back as Portuguese and me answering in Portuguese."
)
VOICE = (
    "One more, and this one is genuinely optional: do you want me to have a voice?\n\n"
    "With an ElevenLabs key I can hear you and speak back — you talk, I answer out loud. Without one "
    "I read and write, which is all of me except the sound. Everything else works either way.\n\n"
    "Keys are made here: {url}"
)
VOICE_LATER = (
    "One more, and this one is genuinely optional. I can have a voice — you talk, I answer out loud "
    "— except the part of me that does it isn't installed on this machine: that's "
    f"`pip install \"{DIST_NAME}[voice]\"`, whenever you want it.\n\n"
    "I'll still take an ElevenLabs key now and keep it for then, or you can skip it. Keys are made "
    "here: {url}"
)
# Where the key lives decides which of these she says. Told "environment" over a key they had typed,
# somebody goes looking for a variable that does not exist and cannot change the key they own.
VOICE_HELD_APP = "There's an ElevenLabs key saved here in the app"
VOICE_HELD_ENV = "There's an ElevenLabs key in this machine's environment"
VOICE_ALREADY = (
    "One more — except it's already done. {held}, so I have a voice: you can talk to me and I'll "
    "answer out loud."
)
VOICE_ALREADY_LATER = (
    "One more — and the key half is already done: {held}. The part of me that speaks isn't installed "
    f"here yet, so the sound is one command away: `pip install \"{DIST_NAME}[voice]\"`."
)
CAN_DO = (
    "Here's what I can already do: search the web and read the pages I find, {hands}remember what "
    "matters between conversations, and go work on something long on my own while you get on with "
    "something else."
)
NEXT_STEP_VOICE = ("Type `kotoba` and I'm here. `kotoba serve --open` opens the web app in your "
                   "browser, where I have a face and a voice.")
NEXT_STEP_QUIET = ("Type `kotoba` and I'm here. `kotoba serve --open` opens the web app in your "
                   "browser, where I have a face — run `kotoba setup` again whenever you want the voice "
                   "too.")
NEXT_STEP_UNVOICED = ("Type `kotoba` and I'm here. `kotoba serve --open` opens the web app in your "
                      f"browser, where I have a face — and `pip install \"{DIST_NAME}[voice]\"` is the "
                      "piece I still need for the sound.")
# The same three, for the install that has no renderer to hand over TO: the package with no extras
# runs `_Plain`, and `kotoba` on its own answers with the command that adds the terminal.
NEXT_STEP_BARE = (
    f"`kotoba --once \"what can you do?\"` asks me one thing from right here — that much already works. "
    f"The terminal I actually live in is one install away: `pip install \"{DIST_NAME}[cli]\"`, and then "
    "`kotoba` on its own opens it.")
NEXT_STEP_BARE_QUIET = (
    f"`kotoba --once \"what can you do?\"` asks me one thing from right here — that much already works. "
    f"The terminal I actually live in is one install away: `pip install \"{DIST_NAME}[cli]\"`, and then "
    "`kotoba` on its own opens it. Run `kotoba setup` again whenever you want the voice too.")
NEXT_STEP_BARE_UNVOICED = (
    f"`kotoba --once \"what can you do?\"` asks me one thing from right here — that much already works. "
    "The terminal I live in and the part of me that speaks are both one install away: "
    f"`pip install \"{DIST_NAME}[cli,voice]\"`, and then `kotoba` on its own opens it.")
HANDING_OVER = "That's everything I needed. Let me say hello properly."
HANDING_OVER_QUIET = ("That's everything I needed — and `kotoba setup` adds the voice whenever you want "
                      "it. Let me say hello properly.")
HANDING_OVER_UNVOICED = ("That's everything I needed — the sound needs one more piece, "
                         f"`pip install \"{DIST_NAME}[voice]\"`. Let me say hello properly.")
KEPT_NO_RUNTIME = ("that key is real and I've kept it — but the part of me that speaks isn't installed "
                   f"here. `pip install \"{DIST_NAME}[voice]\"` and I'll have a voice.")
LEFT = "All right — I'll be here. `kotoba setup` picks this up from the top whenever you want."
GAVE_UP = (
    "Let's stop here rather than keep guessing. `kotoba setup` starts this again whenever you're ready."
)

LANGUAGES = (
    ("auto", "auto", "I match whoever is speaking, every message"),
    ("en", "English", ""),
    ("es", "Spanish", ""),
    ("ja", "Japanese", ""),
)


async def needed(db: Database) -> bool:
    """True when no provider has a usable key — neither saved nor in the environment. Lives in
    `core.first_run` because the web's first-run screen asks the same question; re-exported here so
    `kotoba setup`'s own callers keep the name they were written against."""
    return await first_run.needed(db)


async def run(db: Database, caps=None, *, ask=input, ask_secret=secret.read, screen=None,
              face: bool = True, ascii_only: bool = False) -> bool:
    """Walk the person through brain → model → key → their name → her name → language → voice.

    Returns False only when they left before a key was accepted: everything after it is skippable, and
    a Ctrl+C once she has a head keeps the head. Each write after the key stands alone on purpose — it
    lands the moment it is answered, so an interrupted first run leaves what was already said rather
    than nothing. The two BEFORE it are not, and `_put_back` is why: a `model` written without the
    `provider` that serves it is not partial progress, it is an install that 404s."""
    # Before anything is asked: an install from a wheel has no personality file on disk, so the
    # invitation to make her yours had nothing to open. Never overwrites what is already there.
    from kotoba.soul.loader import seed_home_copies

    seed_home_copies()
    stage = _stage(caps, screen, face, ascii_only)
    stage.open()
    stage.she(HELLO, "happy")
    stage.chrome(LEAVING)
    brain = _brain()

    spec = _choose_provider(stage, ask)
    if spec is None:
        stage.she(LEFT, "sad")
        return _put_back(stage, brain)
    if _choose_model(stage, ask, spec) is None:
        stage.she(LEFT, "sad")
        return _put_back(stage, brain)
    if not await _take_key(stage, db, spec, ask_secret):
        return _put_back(stage, brain)

    voiced = ""
    try:
        await _ask_your_name(stage, db, ask)
        await _ask_her_name(stage, db, ask)
        await _ask_language(stage, db, ask)
        voiced = await _ask_voice(stage, db, ask_secret)
    except _Left:
        pass
    _farewell(stage, spec, voiced)
    stage.close()
    return True


def _brain() -> tuple[str, str]:
    """The pair a run that never finishes must not have moved: which provider answers, and which of
    its models."""
    return providers.active_provider_id(), llm.model_name("companion")


def _put_back(stage, brain: tuple[str, str]) -> bool:
    """Undo the half of the brain that was already written, close the turn, and say it did not take.

    `model` lands the moment it is answered and `provider` only when a key is accepted, so leaving
    between the two left `provider: openai` beside `model: grok-4.3` — an install where every turn
    comes back 404, made by pressing Enter. Only a value that actually moved is written back, so a
    first run that changed nothing still writes nothing. The close is what the session behind a
    borrowed screen needs: left open, her greeting arrived with no nameplate on it."""
    stage.close()
    provider, model = brain
    if providers.active_provider_id() != provider:
        app_settings.set_runtime("provider", provider)
    if llm.model_name("companion") != model:
        app_settings.set_runtime("model", model)
    return False


class _Left(Exception):
    """Ctrl+C or a closed stdin after the key was accepted: stop asking, keep what was answered."""


def _choose_provider(stage, ask):
    options = list(providers.PROVIDERS.values())
    stage.she(BRAIN, "thinking")
    stage.options([(str(i), spec.label,
                    f"key starts {spec.key_prefix_hint}"
                    + ("  ·  the one I open with" if spec.id == providers.DEFAULT_PROVIDER else ""))
                   for i, spec in enumerate(options, 1)])
    for attempt in range(_MAX_TRIES):
        raw = _read(stage, ask, "which one?", "[1]")
        if raw is None:
            return None
        raw = raw.strip().lower()
        if not raw:
            return options[0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        for spec in options:
            if raw in (spec.id, spec.label.lower()):
                return spec
        if attempt < _MAX_TRIES - 1:
            stage.mark("ask", f"I don't have a {_shown(raw)} — it's "
                              f"{' or '.join(str(i) for i in range(1, len(options) + 1))}.", "sun")
    stage.she(GAVE_UP, "sad")
    return None


def _choose_model(stage, ask, spec):
    """Which of the brain's models, asked BEFORE the key so the key is checked against the model the
    person actually picked. Returns the model now configured, or None when they asked to leave.

    The list is what she OFFERS, cheapest first — never everything the provider sells — so a typed id
    stays a first-class answer. One that belongs to the other company is refused with the reason rather
    than stored: that mismatch is the 404 `first_run.pin_model` exists to prevent. A bare Enter takes
    whatever is ALREADY configured when this provider serves it, so re-running `kotoba setup` for the
    voice key cannot quietly move somebody off a model they chose."""
    options = list(spec.models)
    if not options:
        return first_run.pin_model(spec.id)
    current = llm.model_name("companion")
    keep = current if providers.serves_model(current, spec.id) else spec.default_model
    default = next((i for i, m in enumerate(options) if m.id == keep), -1)
    stage.she(MODEL.format(label=spec.label), "thinking")
    stage.options([(str(i), m.id, _model_note(spec, m, i - 1 == default))
                   for i, m in enumerate(options, 1)]
                  + [(str(len(options) + 1), "something else",
                      f"type the exact id of any other {spec.label} model")])
    for attempt in range(_MAX_TRIES):
        raw = _read(stage, ask, "which one?", f"[{default + 1 if default >= 0 else keep}]")
        if raw is None:
            return None
        raw = raw.strip()
        if not raw:
            return _pin_model(stage, spec, keep)
        if raw.isdigit():
            if 1 <= int(raw) <= len(options):
                return _pin_model(stage, spec, options[int(raw) - 1])
            if int(raw) == len(options) + 1 and attempt < _MAX_TRIES - 1:
                stage.mark("ask", "That last row isn't one to pick by number — type the model's own "
                                  "id instead.", "sun")
                continue
        elif providers.serves_model(raw, spec.id):
            return _pin_model(stage, spec, raw)
        else:
            owner = first_run.model_owner(raw, spec.id)
            if attempt < _MAX_TRIES - 1:
                stage.mark("ask", f"{_shown(raw)} is {owner.label}'s and I'm set to ask "
                                  f"{spec.label} — one of theirs, or start me again and pick "
                                  f"{owner.label}.", "sun")
            continue
        if attempt < _MAX_TRIES - 1:
            stage.mark("ask", f"I don't have a {_shown(raw)} — a number from 1 to {len(options)}, "
                              f"or the exact id of another of theirs.", "sun")
    stage.she(GAVE_UP, "sad")
    return None


def _pin_model(stage, spec, choice):
    """Write the answer the moment it is given, and say back what it costs relative to the cheapest.

    The row is looked up by id rather than taken from the caller: a bare Enter arrives here as the id of
    whatever was already configured, and that is usually one of the offers — answering it with `I'll ask
    them for that one` would drop the price from the most common path through this screen.

    An id she has never heard of gets the SUN mark and not the tick. The escape takes anything the other
    provider does not claim, so a typo is accepted as readily as a model shipped tomorrow, and `✓ pizza
    it is` said in mint is her vouching for something she never checked — then the key step blames the
    person's key for it. The tick is reserved for a row off the list."""
    model = first_run.select_model(spec.id, choice if isinstance(choice, str) else choice.id)
    known = next((m for m in spec.models if m.id == model), None)
    hint = providers.cost_hint(spec, known) if known is not None else ""
    floor = min(spec.models, key=lambda m: m.output_cost, default=None)
    if known is None:
        stage.mark("ask", f"{model} — not one I know, so I'll take your word for it and we'll find "
                          f"out together when the key arrives.", "sun")
        return model
    if hint:
        said = f"{model} it is — {hint} the price of the cheapest, for every word I say back."
    elif floor is not None and known.id == floor.id:
        said = f"{model} it is — the least {spec.label} charges me."
    else:
        # No ratio is printed under 1.05x, and this branch used to read the silence as "cheapest".
        said = f"{model} it is — as cheap as anything they do."

    stage.mark("ok", said, "mint")
    return model


def _model_note(spec, choice, is_default: bool) -> str:
    """One option's clause: what it is for, then only the facts that decide between them."""
    bits = [choice.note]
    hint = providers.cost_hint(spec, choice)
    if hint:
        bits.append(f"{hint} the price per word I say back")
    if choice.context:
        bits.append(f"{_tokens(choice.context)} of memory")
    if is_default:
        bits.append("the one I open with")
    return "  ·  ".join(bits)


def _tokens(n: int) -> str:
    return f"{n // 1_000_000}M" if n >= 1_000_000 else f"{n // 1_000}k"


def _key_row_hints(hidden: bool) -> tuple[str, str]:
    """What the prompt row may claim about this terminal, long form and short. Told rather than asked,
    so it cannot disagree with the sentence above it."""
    if hidden:
        return ("(hidden — nothing shows as you type)", "(hidden)")
    return ("(this terminal shows what you type)", "(it shows)")


async def _key_held(db, spec) -> bool:
    """Whether this provider already has a usable key, saved here or in the environment. A placeholder
    is not one, by the same rule the rest of first run uses."""
    import os

    saved = (await db.get_key(f"llm:{spec.id}:api_key")) or ""
    if saved.strip() and not llm.looks_placeholder(saved):
        return True
    env = os.getenv(spec.key_env, "") or ""
    return bool(env.strip()) and not llm.looks_placeholder(env)


async def _take_key(stage, db, spec, ask_secret) -> bool:
    """The one screen that must not overstate what the machine under it can do.

    `secret.can_hide` is asked ONCE and both surfaces are told: her sentence and the prompt row would
    otherwise be two independent claims about one terminal. Where nothing here can turn an echo off — a
    CI runner, `docker run` without `-t` — the promise is withdrawn instead of made and broken, because
    the echo is happening in a terminal this process does not have. The reader is asked about rather
    than the injected `ask_secret`, which is a test seam: `secret.read` is what production passes."""
    hidden = secret.can_hide()
    held = await _key_held(db, spec)
    stage.she((KEY if hidden else KEY_SHOWN).format(label=spec.label, url=spec.key_url), "neutral")
    if held:
        # The browser's key step has always let an empty field mean "keep it". Here the same Enter ended
        # the run, so reaching the voice question again meant re-pasting and re-verifying a working key.
        stage.chrome("You already have one — Enter on an empty line keeps it.")
    for attempt in range(_MAX_TRIES):
        key = _read(stage, ask_secret, f"{spec.label} key", *_key_row_hints(hidden))
        if key is not None and not key.strip() and held:
            stage.mark("ok", "keeping the key you already have.", "mint")
            return True
        if key is None or not key.strip():
            stage.she(LEFT, "sad")
            return False
        key = key.strip()
        if llm.looks_placeholder(key):
            # Not a warning like the prefix hint below: `get_client` refuses to build on a placeholder,
            # so asking anyway reaches no provider and the failure comes back as "that's this install",
            # sending the person to `doctor` for a missing piece that is not missing. The voice step
            # already refuses it here; this one used to warn and carry on.
            stage.mark("ask", "That's the placeholder out of the example file, not a key — the real "
                              f"one is made at {spec.key_url}", "sun")
            continue
        trouble = _looks_wrong(spec, key)
        if trouble:
            stage.mark("ask", trouble, "sun")
        stage.mark("give", f"asking {spec.label} whether this key is real…", "grape")
        ok, detail = await _verify(spec.id, key)
        if ok:
            await db.save_key(f"llm:{spec.id}:api_key", key)
            app_settings.set_runtime("provider", spec.id)
            llm.set_provider_key(spec.id, key)
            stage.mark("ok", f"that key works — I'll be thinking with {llm.model_name('companion')}.",
                       "mint")
            return True
        stage.mark("fail", detail, "live")
        if attempt < _MAX_TRIES - 1:
            stage.chrome("Try another one, or press Enter on an empty line to stop.")
    stage.she(GAVE_UP, "sad")
    return False


async def _ask_your_name(stage, db, ask) -> None:
    stage.she(YOUR_NAME, "happy")
    value = _read(stage, ask, "what I should call you", "[Enter skips]", "[skip]")
    if value is None:
        raise _Left
    value = value.strip()
    if not value:
        stage.chrome("No name yet — I'll pick it up as we talk.")
        return
    await db.upsert_user_profile("name", value[:60])
    stage.mark("ok", f"{value[:60]}. Got it.", "mint")


async def _ask_her_name(stage, db, ask) -> None:
    stage.she(MY_NAME, "affectionate")
    value = _read(stage, ask, "what to call me", "[Kotoba]")
    if value is None:
        raise _Left
    value = value.strip()
    if not value:
        return
    if len(value) > 40 or not _NAME_LIKE.search(value):
        stage.mark("ask", "That one I can't wear — I'll stay Kotoba.", "sun")
        return
    await db.update_soul_config(name=value)
    if not await _took(db, "name", value):
        stage.mark("ask", "That one didn't stick — I'll stay Kotoba.", "sun")
        return
    stage.called(value)
    stage.mark("ok", f"{value}, then. I'll answer to it.", "mint")


async def _ask_language(stage, db, ask) -> None:
    stage.she(LANGUAGE, "thinking")
    stage.options([(str(i), label, note) for i, (_c, label, note) in enumerate(LANGUAGES, 1)]
                  + [(str(len(LANGUAGES) + 1), "something else",
                      "type its code instead — de, fr, pt…")])
    for attempt in range(_MAX_TRIES):
        raw = _read(stage, ask, "which one?", "[1]")
        if raw is None:
            raise _Left
        raw = raw.strip()
        if not raw:
            raw = "1"
        if raw.isdigit() and int(raw) == len(LANGUAGES) + 1 and attempt < _MAX_TRIES - 1:
            stage.mark("ask", "That last row isn't one to pick by number — type the code itself, "
                              "like `de` or `pt`.", "sun")
            continue
        picked = _language(raw)
        if picked:
            code, label = picked
            await db.update_soul_config(language=code)
            if not await _took(db, "language", code):
                stage.mark("ask", "That didn't stick — I'll stay on auto.", "sun")
                return
            # The list offers names, so the answer says the name back: `es it is` read like a leak
            # of the value the row stores. Only a code off the list has no name, and stays as typed.
            stage.mark("ok", "I'll follow you between languages." if code == "auto"
                       else f"{label} it is — I'll reply in it and listen for it.", "mint")
            return
        if attempt < _MAX_TRIES - 1:
            stage.mark("ask", f"I don't know {_shown(raw)} — a number from the list, or a code "
                              "like `es`.", "sun")
    # `/set` refuses `language` on purpose — it changes where it was made — so the
    # route named here is the one that works from a terminal.
    stage.chrome("Leaving it on auto — `kotoba setup` asks again whenever you want.")


async def _ask_voice(stage, db, ask_secret) -> str:
    """The one question whose best answer is often no. Returns what she ended up with: "voice", or
    "stored" for a good key on an install that has nothing to speak it with, or "".

    Skipping is a choice, not a failure: text is a complete way to use her, `doctor` says so in the same
    words, and a stranger with no ElevenLabs account has to be able to finish this and get a companion.
    So the prompt advertises the skip, the skip gets a sentence of its own, and the closing line changes
    to name the way back rather than pretending the voice is there."""
    speaks = _voice_runtime()
    held = await voice_key.where(db)
    if held:
        # Same rule as `needed()`: an env var is a perfectly good configuration, and asking someone who
        # already has one to type a key again is a bug, not a courtesy. Asked through the voice package
        # this missed a SAVED key on an install without that extra, and offered to take it again.
        said = VOICE_HELD_APP if held == "app" else VOICE_HELD_ENV
        stage.she((VOICE_ALREADY if speaks else VOICE_ALREADY_LATER).format(held=said), "happy")
        return "voice" if speaks else "stored"
    stage.she((VOICE if speaks else VOICE_LATER).format(url=voice_key.VOICE_KEY_URL), "affectionate")
    for attempt in range(_MAX_TRIES):
        key = _read(stage, ask_secret, "ElevenLabs key", "[Enter skips — text works without it]",
                    "[Enter skips]")
        if key is None:
            raise _Left
        key = key.strip()
        if not key:
            stage.chrome("No voice then — she reads and writes, and that is all of her but the sound.")
            return ""
        if llm.looks_placeholder(key):
            stage.mark("ask", "That's the placeholder from the example file rather than a key.", "sun")
            continue
        stage.mark("give", "asking ElevenLabs whether this key is real…", "grape")
        ok, detail = await voice_key.verify(key)
        if ok:
            await voice_key.save(db, key)
            if not speaks:
                stage.mark("ok", KEPT_NO_RUNTIME, "sun")
                return "stored"
            stage.mark("ok", detail or "that key works — I can hear you and speak.", "mint")
            return "voice"
        stage.mark("fail", detail, "live")
        if attempt < _MAX_TRIES - 1:
            stage.chrome("Try another one, or press Enter to go without a voice.")
    stage.chrome("Going without a voice for now — `kotoba setup` asks again whenever you want.")
    return ""


def _voice_runtime() -> bool:
    """Whether the code that would DO the speaking is installed at all.

    `voice_key.configured()` answers a different question and says False for a missing extra and a
    missing key alike, so a good key on a `kotoba[cli]` install was answered `I can hear you and speak`
    and then sent back to `kotoba setup` — two sentences, both untrue, neither naming the real gap."""
    from importlib.util import find_spec

    try:
        return find_spec("websockets") is not None
    except (ImportError, ValueError):
        return False


def _farewell(stage, spec, voiced: str = "") -> None:
    """The closing names the way back to whatever was skipped. Not one ending but nine: `kotoba setup`
    still owns the screen or the session is about to take it, this install has her terminal or has only
    `--once`, and she has a voice, has none, or has a key and nothing installed to speak it with — and a
    closing that promised a voice she cannot perform would be the first thing she said that was not true.

    `renders` is the axis that was missing. `_Plain` runs on an install with no `rich`, where `kotoba`
    on its own names the extra that adds the terminal and exits 1 — so "Type `kotoba` and I'm here" sent the
    one person who most needed a working command at the one command this install refuses."""
    if not stage.renders:
        closing = (NEXT_STEP_BARE_UNVOICED if voiced == "stored"
                   else NEXT_STEP_BARE if voice_key.configured() else NEXT_STEP_BARE_QUIET)
    elif voiced == "stored":
        closing = HANDING_OVER_UNVOICED if not stage.owns else NEXT_STEP_UNVOICED
    elif not stage.owns:
        closing = HANDING_OVER if voice_key.configured() else HANDING_OVER_QUIET
    else:
        closing = NEXT_STEP_VOICE if voice_key.configured() else NEXT_STEP_QUIET
    stage.she(f"That's me. I think with {llm.model_name('companion')} through {spec.label}, "
              f"and everything you just told me is written down.\n\n{CAN_DO.format(hands=_hands())}"
              f"\n\n{closing}", "excited")


def _hands() -> str:
    from kotoba.core import approval, sandbox

    backend = sandbox.backend_name()
    if backend == "none":
        return ""
    if backend == "docker":
        return "run commands and code in a container — only something dangerous asks you first — "
    if approval._windows_shell():
        return "run commands and code on this machine — I ask you first, every time — "
    return ("run commands and code on this machine — a plain read inside my workdir goes straight "
            "through, everything else asks you first — ")


def _language(raw: str) -> tuple[str, str] | None:
    """One row of the list, however it was named — its number, its code, or the name printed on it.

    Typing what you can SEE was refused: the list prints `auto`, `English`, `Spanish`, and the code
    rule takes two or three letters, so `auto` could never have matched it. A code that IS one of the
    rows now answers with that row's name too, which is the whole point of confirming by name — a
    person typing `es` got `es it is`, the stored value read back as if it were the answer."""
    want = raw.strip().lower()
    if want.isdigit() and 1 <= int(want) <= len(LANGUAGES):
        code, label, _note = LANGUAGES[int(want) - 1]
        return code, label
    for code, label, _note in LANGUAGES:
        if want in (code, label.lower()):
            return code, label
    if _LANGUAGE_CODE.match(want):
        return want, want
    return None


async def _took(db, column: str, value: str) -> bool:
    """Whether the row really holds what was just written.

    `db.update_soul_config` refuses values that do not look like a name and coerces junk languages to
    `auto`, both silently and both on purpose (the LLM name-extractor emits `42`). It is also an UPDATE
    over one row, so on a database nothing seeded it writes nothing at all. Reading back is the only way
    this screen can say `I'll answer to it` and be telling the truth."""
    soul = await db.fetch_soul_config()
    return (soul.get(column) or "").strip().lower() == value.strip().lower()


def _looks_wrong(spec, key: str) -> str:
    """A warning, never a refusal — `key_prefix_hint` is soft validation by design, because a provider
    may change the shape of its keys tomorrow. The placeholder is handled by the caller instead: that
    one is not a guess about shape, it is the example file's dummy, and no round trip can survive it."""
    for other in providers.PROVIDERS.values():
        if other.id != spec.id and other.key_prefix_hint and key.startswith(other.key_prefix_hint):
            return (f"That looks like {_a(other.label)} {other.label} key and I'm set to ask "
                    f"{spec.label}. I'll try it, but expect a no.")
    return ""


async def _verify(provider_id: str, key: str) -> tuple[bool, str]:
    """A real call, not a format check: a well-formed key with no credit reads as valid otherwise.

    Returns (ok, a sentence a person can act on). The provider's own exception never leaves this
    function — `_explain` is the only thing that reads it."""
    previous = llm._provider_keys.get(provider_id)
    llm.set_provider_key(provider_id, key)
    app_settings.set_runtime("provider", provider_id)
    try:
        client = llm.get_client()
        if client is None:
            return False, _explain(providers.get_spec(provider_id), None, key)
        await client.responses.create(
            model=llm.model_name("companion"), input="ping",
            max_output_tokens=_PROBE_TOKENS, **llm.model_call_kwargs(),
        )
        return True, ""
    except Exception as e:
        # Two branches of `_explain` end by naming this file; DEBUG under an INFO config wrote nothing.
        log.warning("first-run key check failed: %s", _redact(f"{type(e).__name__}: {e}", key))
        llm.set_provider_key(provider_id, previous)
        return False, _explain(providers.get_spec(provider_id), e, key)


def _explain(spec, exc, key: str = "") -> str:
    """What went wrong, said the way she would say it — no class name, no status code, no JSON.

    The provider's 401 body is the thing this replaces, and it is worth being precise about what was
    lost: it arrived truncated at 160 characters, mid-word, and the only useful half of it — the page
    where a key is made — was what the cut removed. So every branch that can name that page names it,
    and names it LAST: a URL too wide for the window is lifted onto its own row (`markdown.lift_urls`),
    which left `Top it up at and this same key will work` on a 52-column terminal."""
    status = getattr(exc, "status_code", None)
    code, param, blurb = _body_of(exc)
    if exc is None:
        return (f"I couldn't even build a connection to {spec.label} — that's this install, not your "
                f"key. `kotoba doctor` says which piece is missing.")
    if _no_route(exc):
        return (f"I couldn't reach {spec.label} at all. That's the network on this machine or "
                f"something in front of it — a proxy, a firewall, a VPN — and not the key.")
    if not _came_from_the_provider(exc):
        return (f"That failed before it ever reached {spec.label}, so it is this install and not your "
                f"key — nothing was wrong with what you typed. The whole of it is in the log: "
                f"{_log_path()}")
    if status is None:
        return (f"Something went wrong on the way to {spec.label} that I can't put simply. "
                f"The whole of it is in the log: {_log_path()}")
    if status == 401:
        wrong = next((o for o in providers.PROVIDERS.values()
                      if o.id != spec.id and o.key_prefix_hint and key.startswith(o.key_prefix_hint)),
                     None)
        if wrong is not None:
            return (f"{spec.label} doesn't know that key, and I can see why: it's "
                    f"{_a(wrong.label)} {wrong.label} key. Start me again and pick {wrong.label}, or "
                    f"paste {_a(spec.label)} {spec.label} one.")
        return (f"{spec.label} doesn't recognise that key. A stray space often comes along with a "
                f"paste — otherwise make a fresh one at {spec.key_url}")
    if status == 429 and (code == "insufficient_quota" or _mentions(blurb, "quota", "credit", "billing")):
        return (f"The key is real — the {spec.label} account behind it has no credit left. Add some "
                f"and this same key will work: {spec.credit_url}")
    if _about_the_model(code, param):
        return _wrong_model(spec)
    if status == 429:
        return f"{spec.label} is rate-limiting that key right now. Give it a moment and try it again."
    if status == 403:
        return (f"{spec.label} took the key but won't let it do this. The account usually needs "
                f"verifying first, and which keys can do what is here: {spec.key_url}")
    if status in (400, 404) and param in ("", "model"):
        if _looks_like_a_page(getattr(exc, "body", None)):
            # A PAGE came back where an error object belongs, so this is not the provider's verdict on
            # anything. A bare 400/404 with no body still is: that was measured on a real key with a
            # mistyped model, and it is why this branch reads the body rather than the status.
            return (f"Something answered instead of {spec.label} — a sign-in page on this network, or "
                    f"a proxy, does that, and it is not about your key. The log has the whole of it.")
        if not code and not param and _is_one_we_offered(spec):
            # Measured on a live install: a bare 400 with an EMPTY body, on the default model picked
            # off this very list, reported as "your model is wrong". A model we offered cannot be the
            # thing the provider does not know, so the honest answer is that it said nothing at all.
            return (f"{spec.label} refused that and gave no reason at all. It is not the model — that "
                    f"one came off my own list — and a key it disliked would say so. Nothing is saved; "
                    f"try again in a moment, and the whole answer is in the log: {_log_path()}")
        return _wrong_model(spec)
    if status >= 500:
        return f"{spec.label} is having trouble at their end. Nothing wrong with the key — try again shortly."
    return (f"{spec.label} refused that call and didn't say anything I can put simply — and it may "
            f"not be the key at all. The whole answer is in the log: {_log_path()}")


def _about_the_model(code: str, param: str) -> bool:
    """Whether the provider is objecting to the MODEL rather than to the key, by its OWN account.

    Structured signals only. The obvious third one — the model's name appearing in the message — reads
    a rate-limit ("Rate limit reached for gpt-5.6-luna in organization org-…") as a broken model, which
    is the same misattribution one step sideways."""
    return code in ("model_not_found", "model_not_supported") or param == "model"


def _wrong_model(spec) -> str:
    """The key is fine and the model is not — said so that nobody goes and makes a second key.

    A bare 400 or 404 on THIS call means the same thing whatever the body says: the request is a fixed
    sixteen-token ping and the only part of it the person chose is the model. It stops meaning that the
    moment the provider blames some OTHER field, which is our own request being wrong.

    It does not say the model is imaginary. A key restricted to a subset of models is answered "no such
    model" exactly as a typo is, so naming a live model as non-existent sends somebody to check a
    spelling that was right. `/set model` is not the route out either: the key was never stored, so
    `kotoba` only re-opens this wizard."""
    return (f"The key is fine — it's the model. {spec.label} would not serve "
            f"{llm.model_name('companion')} to this key: either the name is wrong, or this key is not "
            f"allowed that model, which a project key often is not. Run `kotoba setup` again and pick "
            f"another from the list.")


def _came_from_the_provider(exc) -> bool:
    """Whether the provider answered at all. Anything else is OUR install failing on the way out.

    Measured under a simulated Windows: a dependency that imports differently there raised inside the
    probe, landed on the no-status branch, and told the person something went wrong on the way to
    OpenAI — for a call that never left the machine, sending them to re-make a key that was fine."""
    try:
        from openai import APIError
    except ImportError:
        return False
    return isinstance(exc, APIError)


def _no_route(exc) -> bool:
    """Nothing answered at the other end. Asked by TYPE rather than by a missing status, or every
    exception with no HTTP status behind it — a bug of ours included — would be reported to a person
    as their own network being down."""
    # The local-file family is NOT the network, and it is the shape our own faults take: a CA bundle
    # named by SSL_CERT_FILE that is not there raises FileNotFoundError while the client is being
    # built, which is the commonest corporate-proxy misconfiguration there is. Caught here it sent
    # somebody to inspect a firewall over a missing file.
    if isinstance(exc, (FileNotFoundError, PermissionError, IsADirectoryError,
                        NotADirectoryError, FileExistsError)):
        return False
    try:
        from openai import APIConnectionError   # APITimeoutError is a subclass of it
    except ImportError:
        return isinstance(exc, OSError)
    return isinstance(exc, (APIConnectionError, OSError))


def _is_one_we_offered(spec) -> bool:
    """Whether the configured model is a row off this provider's own list rather than a typed id.

    The typed one is what `_wrong_model` was measured against; the offered one cannot be the model the
    provider has never heard of, so a refusal that names no field is not about it."""
    from kotoba.core import llm

    model = llm.model_name("companion")
    return any(getattr(m, "id", m) == model for m in spec.models)


def _looks_like_a_page(body) -> bool:
    """Whether what came back is markup rather than an error object — a captive portal, a proxy."""
    return isinstance(body, str) and body.lstrip()[:1] == "<"


def _body_of(exc) -> tuple[str, str, str]:
    """(the provider's own error code, the field it blames, its message) — read defensively, because
    this runs on the path where something already went wrong and a second failure here would hide the
    first. `param` was being dropped, and it is the plainest thing either provider says about which
    half of the request they are refusing."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        if isinstance(inner, dict):
            return (str(inner.get("code") or ""), str(inner.get("param") or ""),
                    str(inner.get("message") or ""))
    return "", "", str(getattr(exc, "message", "") or "")


def _a(label: str) -> str:
    """`an OpenAI key`, `an xAI key`, `a Google key`. Provider labels are read aloud letter by letter
    as often as not, so the rule is the SOUND of the first letter rather than whether it is a vowel."""
    return "an" if (label or " ")[0].upper() in "AEIOUFHLMNRSX" else "a"


def _mentions(text: str, *words: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in words)


def _redact(text: str, key: str) -> str:
    """The one thing the wizard ever writes to disk about a failed key. Providers usually mask the key
    in their own refusal, and `usually` is not a property to rest a secret on.

    Which is also why no traceback goes with it: `exc_info` renders the exception's own text into
    `exc_text`, past every redaction here, and a caught test proved it put a whole key in the file.
    The class and the provider's sentence are the answer; the frames are the SDK's, not ours."""
    return text.replace(key, "<the key>") if key else text


def _log_path() -> str:
    from kotoba.core import logs

    try:
        return str(logs.path())
    except Exception:
        return "~/.kotoba/cli.log"


def _shown(raw: str) -> str:
    """Something they typed, quoted back short. Never more than a few cells: this is a complaint, and
    a complaint that reprints a paragraph is a wall."""
    clean = "".join(c for c in raw if c.isprintable())[:16]
    return f"`{clean}`" if clean else "that"


def _read(stage, ask, label: str, *hints: str) -> str | None:
    """What they typed, or None when they asked to leave. Ctrl+C and a closed stdin are the same
    answer here, and both are advertised on the first screen."""
    stage.prompt(label, *hints)
    try:
        typed = _uninterrupted(ask)
    except (KeyboardInterrupt, EOFError):
        stage.broke()
        return None
    stage.answered()
    return _repair(typed)


def _uninterrupted(ask):
    """The read, with the handler that RAISES in force for the length of it.

    `asyncio.Runner` points SIGINT at cancelling its main task, and this task is blocked inside a
    synchronous read, so the first Ctrl+C reached nobody at all — and the cancellation it left behind
    went off at the next await, which is the one that stores the key. Measured on the binary: a
    traceback over her first-run screen, and a key the person had just pasted not saved."""
    try:
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    except (ValueError, OSError):
        return ask("")          # not the main thread: nothing to swap, and nothing to restore
    try:
        return ask("")
    finally:
        signal.signal(signal.SIGINT, previous)


def _repair(typed: str) -> str:
    """An answer a non-UTF-8 stdin could not decode, put back together.

    `LC_ALL=C` gives stdin an ascii decoder with `errors="surrogateescape"`, so `José` arrives as
    surrogate halves — and SQLite refuses to store one, which ended first run on a traceback with the
    key already saved. The surrogates ARE the original bytes, so re-encoding recovers the real name;
    on a UTF-8 terminal this is a no-op."""
    if not typed:
        return typed
    return typed.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _stage(caps, screen, face: bool = True, ascii_only: bool = False):
    """Her chrome when this terminal has it, plain text when the install never had `rich`.

    `kotoba setup` is reachable from an install with no extras at all, and it is the one
    command that install most needs. So the renderer is optional and the words are not.

    `ascii_only` is carried separately because `caps` is what usually carries it and there is no `caps`
    on that install — `__main__._flags` returns None when the renderer will not import, so the flag was
    parsed and then dropped on the one path least likely to be able to draw an em dash."""
    if screen is None and caps is None:
        try:
            from kotoba.cli.render.caps import detect

            caps = detect(ascii_only=ascii_only)
        except ImportError:
            return _Plain(ascii_only)
    try:
        from kotoba.cli.render.first_run import Stage
    except ImportError:
        return _Plain(ascii_only)
    return _Themed(Stage(caps, screen, face=face))


class _Themed:
    """The wizard's vocabulary, spoken through the first-run stage."""

    renders = True

    def __init__(self, stage) -> None:
        self.stage = stage
        self.owns = stage.owns

    def open(self) -> None:
        self.stage.open()

    def she(self, text: str, emotion: str = "neutral") -> None:
        self.stage.she(text, emotion)

    def options(self, items) -> None:
        self.stage.options(items)

    def mark(self, glyph: str, text: str, style: str = "chrome") -> None:
        self.stage.mark(glyph, text, style)

    def chrome(self, text: str) -> None:
        self.stage.chrome(text)

    def prompt(self, label: str, *hints: str) -> None:
        self.stage.prompt(label, *hints)

    def answered(self) -> None:
        self.stage.answered()

    def called(self, name: str) -> None:
        self.stage.called(name)

    def broke(self) -> None:
        self.stage.broke()

    def close(self) -> None:
        self.stage.close()


class _Plain:
    """The same words with none of the chrome, for an install that never got the `cli` extra.

    `renders` is False and `_farewell` reads it: this install answers `kotoba` with
    the extra that adds the terminal and exit 1, so the endings that say "Type `kotoba` and I'm here" are
    not endings it may be given.

    `--ascii` is honoured here through the same table every other surface folds with — kept apart from
    the theme precisely so it can be imported with no rich. The fold is the table and nothing else, so
    a name keeps its accents."""

    owns = True
    renders = False

    def __init__(self, ascii_only: bool = False) -> None:
        self.ascii_only = ascii_only

    def _t(self, text: str) -> str:
        return fold(text) if self.ascii_only else text

    def open(self) -> None:
        print()

    def she(self, text: str, emotion: str = "neutral") -> None:
        print("\n" + self._t(text).replace("`", "").replace("*", "") + "\n")

    def options(self, items) -> None:
        for key, label, note in items:
            print(self._t(f"  {key}  {label}" + (f"    {note}" if note else "")))

    def mark(self, glyph: str, text: str, style: str = "chrome") -> None:
        print(self._t(text).replace("`", ""))

    def chrome(self, text: str) -> None:
        print(self._t(text).replace("`", ""))

    def prompt(self, label: str, *hints: str) -> None:
        hint = hints[0] if hints else ""
        print(self._t(f"\n  > {label}" + (f"  {hint}" if hint else "") + " "), end="", flush=True)

    def answered(self) -> None:
        import sys

        if not sys.stdin.isatty():
            print()

    def called(self, name: str) -> None:
        pass

    def broke(self) -> None:
        print()

    def close(self) -> None:
        print()
