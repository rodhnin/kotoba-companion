"""What each `/word` actually does — the cost of a command, not the list of them.

Everything here writes at column 0 and returns to the prompt: a command is the machine answering, not
her, so none of it takes her gutter or her nameplate. `run` returns True only for the one that leaves,
and it is async because three of them read the database.

`/settings` and `/set` are the two that change the trust model, and `sandbox` and `provider` come
through `Confirm` — the CONSEQUENCE, never "are you sure". There is deliberately no `--force` twin: a
flag people learn to type reflexively is a gate that has already been passed. `/stop` takes the same
rail with its own two labels, because `y change it` is the wrong answer to killing a job."""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from types import SimpleNamespace

from rich.cells import cell_len
from rich.text import Text

from kotoba.cli import settings_view, state
from kotoba.cli.input import commands
from kotoba.cli.render import cards, listing, rows
from kotoba.cli.render.kaomoji import EMOTIONS
from kotoba.cli.render.rows import _clip, _count
from kotoba.cli.render.text import duration, fit, head, wrap

SESSIONS_SHOWN = 20
SEEN = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf")
ATTACH_MAX = 14_000_000     # the same ceiling the web's own attachment endpoint keeps
SHARED_DIR = "shared"       # where a shared image lands in the Files library, beside `screenshots/`
_MARKER = re.compile(r"\[(?:Image|File) #\d+\]")


def marker(n: int, name: str) -> str:
    """What a file the user just handed her LOOKS like on the input line: `[Image #1]`, a reference and
    not a sentence. The old `look at @clip-1.png` put English words in the mouth of whoever was writing
    Spanish, and the `@name` was never a handle — `_carry` attaches from `app.clips`, so nothing
    downstream ever read the line for it."""
    return f"[{'File' if name.lower().endswith('.pdf') else 'Image'} #{n}]"


def shown(text: str) -> str:
    """What a stored user turn LOOKED like when it was typed. A share with no caption is kept as the
    sentinel both surfaces send, so a replay printing the row raw showed `__image_only__` to someone who
    had typed nothing at all — and would have, for a web turn, long before the CLI ever sent one."""
    return {"__image_only__": "[Image]", "__file_only__": "[File]"}.get((text or "").strip(), text)


def only_marks(line: str) -> bool:
    """True for a line that is one or more markers and nothing else — a share with no caption."""
    return bool(_MARKER.search(line)) and not _MARKER.sub("", line).strip()


def unmark(line: str, mark: str) -> str:
    """The line without one marker, for a clip that was announced in the box and then not carried.

    The marker goes in at paste time and the bytes only travel on the way out, so a file refused by the
    per-session cap left `[Image #5]` in a line that carried four: the person is told, and she is handed
    a reference to nothing.

    Dropped, not renumbered. The number counts the SESSION, not the position in this message, so it
    never was an index into the parts she receives and renumbering would invent a correspondence
    nothing downstream has. One horizontal space goes with it, so a caption gains no gap."""
    return re.sub(r"[^\S\n]*" + re.escape(mark), "", line, count=1).strip()


def said(line: str, attached: bool) -> str:
    """The text a marked line actually SENDS. Markers and nothing else is a share with no caption, which
    the context builder already has a branch for — it drops the sentinel and tells her to go on in the
    language she was already speaking rather than describing a picture from outside the conversation.
    The same two sentinels the web sends, so both surfaces reach that branch the same way. A line with
    words of its own is that caption, and rides as it was typed.

    `attached` is asked of `core.attachments`, not of the bookkeeping: ctrl-v and `/attach` put the part
    there by different roads and the only thing that matters is whether one is really waiting. Without
    it a marker nothing carried would send a sentinel with no file behind it — and the sentinel, left
    unclaimed by the attachment branch, reaches the model as those literal characters."""
    if not attached or not only_marks(line):
        return line
    found = _MARKER.findall(line)
    return "__file_only__" if all(m.startswith("[File") for m in found) else "__image_only__"


async def run(app, cmd: commands.Command) -> bool:
    if not cmd.known:
        app.screen.face.set("confused", instant=True)
        app.screen.separate()
        app.screen.chrome(f"{cmd.name}? not one of mine — /help lists them")
        app.screen.blank()
        return False
    if cmd.name == "/quit":
        return True
    out = HANDLERS[cmd.name](app, cmd.arg)
    if asyncio.iscoroutine(out):
        await out
    return False


# --- what she can do -------------------------------------------------------------------------------

HELP = (
    ("COMMANDS", ()),
    ("LAUNCH", (
        ("kotoba --once TEXT", "ask her one thing, print the answer, leave"),
        ("kotoba setup", "choose a provider and hand over a key"),
        ("kotoba doctor", "say what is missing before she can run"),
        ("kotoba serve", "run the backend and the web UI together"),
        ("kotoba --sessions", "open on your past conversations, and read one back"),
        ("kotoba --settings", "open on the configuration, a section at a time"),
        ("kotoba --calm", "reduced motion: nothing on screen moves"))),
    ("KEYS", (
        ("alt-enter", "start a new line without sending"),
        ("enter on an empty line", "show me whatever's waiting out here"),
        ("@", "name a file in her workdir — completes as you type"),
        ("type while she works", "it's still in the box when she's free"),
        ("y n ? a t", "answer an approval — no enter, the card draws its keys"),
        ("ctrl-c", "stop her — twice on an empty box leaves"),
        ("ctrl-d", "leave"))),
    ("TRY", (
        ("look up <anything> for me", "she searches the web and says where it came from"),
        ("what do you know about me", "her memory, and the tools that read it"),
        ("read @<file>", "anything in her workdir — @ completes as you type"),
        ("run <command>", "an approval card — it draws the keys it takes"),
        ("look into <topic> properly", "a long job with helpers — /work looks in"))),
)


def _help(app, arg: str) -> None:
    """Four two-column lists, each column as wide as its own content plus two. Hard-coded widths are
    how two entries ended up welded into one word: the longest entry decides. The width comes off the
    WHOLE table, never off the rows drawn, so a fitted list and a `/help commands` line up.

    The four are BUDGETED: together they are 45 rows, and the twenty a person sees on a classic 80x24
    are the LAST twenty. So the window is fitted and what is missing is named — `/help keys` opens one
    part, and the commands cut off the first list are named against `/`. Neither approval row names a
    key LIST: a list belongs to one card, and `/help` is printed with no card open. A pair's row is
    measured, never counted — at 60 columns half of them wrap, and a list sized one row per entry drew
    twice the window it had been fitted to."""
    want = arg.strip().lower()
    parts = [(name, pairs or tuple(commands.help_rows())) for name, pairs in HELP]
    app.screen.separate()
    if want and not any(name.lower().startswith(want) for name, _ in parts):
        app.screen.chrome(f"no part of /help called {want!r} — "
                          + " · ".join(name.lower() for name, _ in parts))
        app.screen.blank()
        return
    picked = [(n, p) for n, p in parts if not want or n.lower().startswith(want)]
    wide = max(cell_len(n) for n in commands.COMMANDS) + 2
    tall = []
    for name, pairs in picked:
        col = wide if name == "COMMANDS" else max(cell_len(left) for left, _ in pairs) + 2
        tall.append([app.screen.rows_of(Text(f"{left:<{col}}{app.caps.t(right)}"))
                     for left, right in pairs])
    whole, spare = listing.plan(app.caps, [(1 if i else 0) + 1 + sum(rows_each)
                                           for i, rows_each in enumerate(tall)], note=3)
    drawn, cut = picked[:whole], 0
    if not whole:
        name, pairs = picked[0]
        take, used = 0, 0
        for n in tall[0]:
            if used + n > spare - 1:
                break
            used, take = used + n, take + 1
        drawn, cut = [(name, pairs[:take])], len(pairs) - take
    for i, (name, pairs) in enumerate(drawn):
        if i:
            app.screen.blank()
        _tag(app, name)
        if name == "COMMANDS":
            for label, desc in pairs:
                app.screen.row(Text.assemble((f"{label:<{wide}}", ""), (app.caps.t(desc), "chrome")))
        else:
            _pairs(app, pairs)
    rest, room = picked[len(drawn) if cut else whole:], app.screen.rw
    if cut or rest:
        app.screen.blank()
    if cut:
        many = _count(cut, "command")
        app.screen.chrome(fit(room, f"+{many} not drawn — / lists every one of them, and scrolls",
                              f"+{many} not drawn — / lists them all", f"+{cut} more — press /",
                              f"+{cut} more"))
    if rest:
        named = " · ".join(name.lower() for name, _ in rest)
        app.screen.chrome(fit(room, f"also /help {named}", f"also /help {rest[0][0].lower()} …",
                              "/help <part>"))
    app.screen.blank()


def _plan(app, arg: str) -> None:
    """The snapshot on demand — a command is an explicit ask, so this is the one place the full plan
    still prints while the band carries it live. With an argument it used to discard the text and
    answer `no plan open right now`, which treats an instruction as a failed query.

    A forty-step list is forty-two rows, and eighty-two once the steps wrap at forty-four columns, so
    the head — which carries `done of total` — went into the scrollback with every finished step.
    Fitted, and head first: the number printed beside a step is the one she names for it.

    Cutting the tail puts the step she is ON out of reach, which is the row somebody typed `/plan` to
    find, so the note NAMES it rather than pretending the missing rows are interchangeable."""
    app.screen.separate()
    if arg.strip():
        app.screen.chrome("plans are hers to open, not /plan's — ask her for one in the box and "
                          "she'll draw it up")
        app.screen.blank()
        return
    if not app.plan:
        app.screen.chrome("no plan open right now")
        return
    drawn = rows.plan_rows(app.caps, app.plan.frame, app.screen.rw)
    head_row, steps = drawn[0], drawn[1:]
    whole, _ = listing.plan(app.caps, [app.screen.rows_of(row) for row in steps], spent=1, note=2)
    app.screen.row(head_row)
    for row in steps[:whole]:
        app.screen.row(row)
    cut = len(steps) - whole
    if cut:
        tasks = sorted(app.plan.frame.get("tasks") or [], key=lambda t: t.get("order", 0))
        at = next((i for i, t in enumerate(tasks) if t.get("status") == "active"), -1)
        on = tasks[at].get("order", 0) if at >= whole else 0
        many = _count(cut, "step")
        app.screen.chrome(fit(app.screen.rw,
                              f"+{many} not drawn — she's on step {on}" if on else
                              f"+{many} not drawn — a taller window shows the rest",
                              f"+{many} not drawn", f"+{cut} more"))
    app.screen.blank()


def _bare(app, name: str, does: str, arg: str) -> bool:
    """True when ARG has been ANSWERED, and the command must not go on and do the bare thing.

    The no-argument family used to drop the word on the floor and run anyway, which reads as obedience
    and is not. On the two toggles it was worse than silence: `/calm off` with motion already off
    turned motion ON, so the word was not ignored, it was inverted. What a toggle says back is where
    the flip stands now, because the next thing that person types is the bare command.

    `/stop` and `/clear` were missed for a reason worth keeping: both named their parameter `_`, so a
    sweep reading for a dropped `arg` found nothing. Each answer has to address the thing the word was
    asking for — `/stop 2` and `/clear history` ask two different questions."""
    if not arg.strip():
        return False
    app.screen.separate()
    app.screen.chrome(f"{name} takes nothing — {does}")
    app.screen.blank()
    return True


def _last(app, arg: str) -> None:
    if _bare(app, "/last", "it prints the last thing that ran, whole", arg):
        return
    app.screen.separate()
    app.screen.chrome(app.last_result or "nothing has run yet this session")
    app.screen.blank()


async def _model(app, arg: str) -> None:
    """`/model x` goes through `/set model x`, so the header, the bar and the listing cannot disagree.

    One road is not enough on its own: the bar's copy of the fact was taken once at boot and never
    re-asked, so they disagreed for a whole session anyway. `_set` calls `App.restate_model` on every
    successful write, and this listing reads the setting live.

    Two states this row cannot build for itself — a key that is not there at all, and a model this
    provider does not serve. `/set provider xai` writes the provider and leaves `model` where it was,
    so one command can leave her naming a brain that 404s on every turn while this row reads as ready;
    `facts._model` is that answer, and the tail says how to fix it."""
    from kotoba.cli import facts
    from kotoba.core import llm, providers

    if arg:
        await _set(app, f"model {arg}")
        return
    app.screen.separate()
    app.screen.chrome(f"{facts._model(llm, providers)} — /model <name> switches it")
    app.screen.blank()


def _face(app, emotion: str) -> None:
    """One face by name, and the two ways a name can miss.

    The name is folded the way its neighbours fold theirs, so `/face EXCITED` sets excited. Nothing at
    all is a usage line rather than a refusal of `(nothing)`: `/attach` and `/set` both answer an empty
    argument by naming what to type, and for a face the place the names live is `/emotions`.

    Both misses take the same rail the rest of the file takes and neither touches her face. Setting
    `confused` on a name that does not exist would answer a failed command by doing the thing it was
    asked for."""
    want = emotion.strip().lower()
    app.screen.separate()
    if not want:
        app.screen.chrome("give me a name — /face happy, and /emotions shows all fourteen")
        app.screen.blank()
        return
    if want not in EMOTIONS:
        app.screen.chrome(f"I don't have a face called {emotion.strip()} — /emotions shows the ones "
                          "I do")
        app.screen.blank()
        return
    app.screen.face.set(want, instant=True)
    app.screen.row(app.screen.plate())
    app.screen.blank()


def _emotions(app, arg: str) -> None:
    """As many to a row as the window holds, flush left: fourteen of them one per row is a list, and
    what she has is a set.

    Both columns are MEASURED, never counted. `( ￣_￣ )?` draws ten cells and `( ･ω･ )` seven, and
    `( ･︵･ )` is seven characters that draw eight — so a name padded to a character count starts
    column two at a different x on almost every row. The COUNT was counted too: `i % 3` drew three
    cells of twenty-four into a twenty-eight-cell window and the terminal folded the third wherever it
    liked. A row is now at most one cell short of the window by construction, and the whole grid is
    fitted like every other listing — at thirty columns it is one column and fourteen rows tall. What
    is held back says so, and that `/face <name>` still reaches it."""
    if _bare(app, "/emotions", "it shows all fourteen at once, and /face <name> sets one", arg):
        return
    screen = app.screen
    screen.separate()
    wide, named = screen.face.width + 1, max(cell_len(e) for e in EMOTIONS) + 1
    across = max(1, (screen.rw + 1) // (wide + named))
    grid, row = [], Text()
    for i, emotion in enumerate(EMOTIONS, 1):
        screen.face.set(emotion, instant=True)
        still = screen.face.still()
        row.append(still, style=screen.face.style)
        row.append(" " * (wide - cell_len(still)) + emotion + " " * (named - cell_len(emotion)),
                   style="chrome")
        if i % across == 0:
            grid.append(row)
            row = Text()
    if row.plain.strip():
        grid.append(row)
    screen.face.set("neutral", instant=True)
    whole, _ = listing.plan(app.caps, [1] * len(grid))
    for line in grid[:whole]:
        screen.row(line)
    cut = len(EMOTIONS) - min(len(EMOTIONS), whole * across)
    if cut:
        screen.chrome(fit(screen.rw, f"+{_count(cut, 'face')} not drawn — /face <name> still sets "
                                     "any of them", f"+{_count(cut, 'face')} not drawn",
                          f"+{cut} more"))
    screen.blank()


def _clear(app, arg: str) -> None:
    """The header as it now stands, which is not quite the one that was printed: the MODEL line moves
    with every successful `/set`. Everything else is still the boot reading, because a command that is
    not a turn has no business re-reading the workdir or counting her turns again.

    A word is refused BEFORE the glass is wiped, and the order is the whole decision: this command
    promises the header and nothing else, so a refusal printed under a freshly cleared screen would
    contradict it and would have taken the rows the person was reading with it. It is worth saying at
    all because `/clear history` reads as clearing the history and cleared only the screen, in
    silence."""
    if _bare(app, "/clear", "it wipes the screen and reprints the header — the session is untouched",
             arg):
        return
    app.screen.console.clear()
    app.screen.last_blank = True
    app.screen.header(*app.facts)


def _calm(app, arg: str) -> None:
    if _bare(app, "/calm", "it flips motion, which is "
             + ("off" if app.caps.reduced_motion else "on") + " right now", arg):
        return
    app.caps.reduced_motion = not app.caps.reduced_motion
    app.screen.separate()
    app.screen.chrome("motion " + ("off" if app.caps.reduced_motion else "on"))
    app.screen.blank()


def _plate(app, arg: str) -> None:
    screen = app.screen
    if _bare(app, "/plate", f"it flips the nameplate, which is {screen.plate_mode} right now", arg):
        return
    screen.plate_mode = "quiet" if screen.plate_mode == "turn" else "turn"
    screen.separate()
    screen.chrome(f"nameplate {app.caps.g['arrow']} {screen.plate_mode}"
                  " — quiet only plates her when her mood changes")
    screen.blank()


# --- everything she runs on ------------------------------------------------------------------------

async def _settings(app, arg: str) -> None:
    """One long listing with the web's sections, not an accordion. A panel hides sections because a
    browser has one screenful and a mouse; a terminal has scrollback, so every section open IS the
    accordion, and `/settings brain` is the one that was clicked.

    Long, but never longer than the window. All twelve are 71 rows, and the twenty a classic 80x24
    keeps were the LAST twenty — the tail of TOOLSETS, four empty headings, and not one key `/set` can
    change. So whole sections are drawn while they fit, head first, and the ones that do not fit are
    NAMED. A section is measured before it is drawn, and `_section`'s added sentence is reserved at its
    widest: over-reserving costs a row of listing, under-reserving costs the heading at the top of the
    screen. A section that does not fit whole is drawn keys first: `/set` reaches those."""
    from kotoba.core.settings import build_settings

    want = arg.strip().lower()
    names = [s for s, _ in settings_view.SECTIONS]
    app.screen.separate()
    if want and not any(s.lower().startswith(want) for s in names):
        app.screen.chrome(f"no section called {want!r} — " + " · ".join(n.lower() for n in names))
        app.screen.blank()
        return
    data = await build_settings(app.session.engine.db, app.session.engine.mcp)
    values, room = data["runtime"], app.screen.rw
    picked = [(n, k, settings_view.section_info(n, data)) for n, k in settings_view.SECTIONS
              if not want or n.lower().startswith(want)]
    sizes = []
    for i, (name, keys, info) in enumerate(picked):
        note = _section_note(name, data)
        sizes.append((1 if i else 0) + 1 + len(keys) + len(info)
                     + (len(wrap(app.caps.t(note), room)) if note else 0))
    foot = ("/set <key> <value> changes any row that shows what it takes — the rest are read-only")
    spent = 1 + len(wrap(app.caps.t(foot), room)) if not want else 0
    whole, spare = listing.plan(app.caps, sizes, spent=spent, note=3)
    keys_shown = 0 if whole else min(len(picked[0][1]), max(0, spare - 1))
    info_shown = 0 if whole else min(len(picked[0][2]), max(0, spare - 1 - keys_shown))
    drawn = picked[:whole] or [picked[0]]
    for i, (name, section_keys, info) in enumerate(drawn):
        if i:
            app.screen.blank()
        _tag(app, name)
        for key in (section_keys if whole else section_keys[:keys_shown]):
            app.screen.row(_setting_row(app, key, values))
        if whole:
            _section(app, name, data)
            continue
        for left, right, tail in info[:info_shown]:
            app.screen.row(cards.info_row(
                app.caps, left, _clip(app.caps, right, room - 24 - cell_len(app.caps.t(tail))), tail,
                room))
    rest = picked[whole:] if whole else picked[1:]
    content = [len(k) + len(info) for _, k, info in picked]
    held = (sum(content[whole:]) if whole
            else content[0] - keys_shown - info_shown + sum(content[1:]))
    if held:
        # Rows for what went, NAMES for what to type, as many as the row holds — longest variant first.
        named = [n.lower() for n, _, _ in rest]
        many = _count(held, "row")
        tries = [f"+{many} not drawn — /settings {' · '.join(named[:k])}"
                 + ("" if k == len(named) else " …") for k in range(len(named), 0, -1)]
        app.screen.blank()
        app.screen.chrome(fit(room, *tries, f"+{many} not drawn", f"+{held} more"))
        app.screen.chrome(fit(room, "/set <key> <value> changes any row that shows what it takes",
                              "/set <key> <value> changes a row", "/set <key> <value>"))
    elif not want:
        app.screen.blank()
        app.screen.chrome(foot)
    app.screen.blank()


_SECTION_NOTE = {
    "MCP": "a browser sign-in is the one thing I can't do from a terminal — finish that one in the "
           "web app",
    "SECURITY": "/approvals lists what each one lets her do — /approvals rm <family> takes one back",
}


def _section_note(name: str, data: dict) -> str:
    """The extra line a section prints under its rows, or "" — one answer for the draw and the budget.

    Every OTHER row here is fitted to the width and is one row whatever the window is; this one is
    chrome and WRAPS, so `_settings` budgeted a flat 3 for it. Measured, that number is wrong in BOTH
    directions and right at neither end: the MCP sentence takes 4 rows at 30 columns and 2 at 80. Under
    it, the listing plans a block shorter than the one it draws — the defect the height budgets exist
    to remove; over it, at the width most people run, it gives up a whole section it had the room for.
    It is measured now, by the caller, with the `wrap` it already uses for the foot."""
    if name == "MCP" and data["pending_mcp"]:
        return _SECTION_NOTE["MCP"]
    if name == "SECURITY" and data["security"]["approvals"]:
        return _SECTION_NOTE["SECURITY"]
    return ""


def _section(app, name: str, data: dict) -> None:
    """A read-only row keeps its shape at any width: a skill's own description is a paragraph, and one
    that wrapped back to column 0 stopped being a table."""
    for left, right, tail in settings_view.section_info(name, data):
        room = app.screen.rw - 22 - cell_len(app.caps.t(tail)) - 2
        app.screen.row(cards.info_row(app.caps, left, _clip(app.caps, right, room), tail,
                                      app.screen.rw))
    note = _section_note(name, data)
    if note:
        app.screen.chrome(note)


async def _set(app, arg: str) -> None:
    """One key, one value, and the row it becomes — the write behind every settable line `/settings`
    draws.

    The bar is re-asked after EVERY successful write, never only after `model` and `provider`: a
    `base_url` or a key moves the same fact, and a header still naming the brain she has left is what
    `App.restate_model` exists for.

    A home she cannot write to is not a reason to end the session under her, so a settings file that
    refuses the write is answered in her own words and the session goes on."""
    from kotoba.core import app_settings

    key, _, raw = arg.strip().partition(" ")
    values = app_settings.runtime_all()
    app.screen.separate()
    if not key:
        app.screen.chrome("/set <key> <value> — /settings lists every key and what it takes")
        app.screen.blank()
        return
    if not raw.strip():
        if key in values:
            app.screen.row(_setting_row(app, key, values))
        else:
            app.screen.chrome(settings_view.check(key, "", values)[1])
        app.screen.blank()
        return
    value, why = settings_view.check(key, raw, values)
    if why:
        app.screen.face.set("confused", instant=True)
        app.screen.chrome(why)
        app.screen.blank()
        return
    if values[key] == value:
        app.screen.chrome(f"{key} is already {settings_view.shows(key, values)[0] or 'unset'}")
        app.screen.blank()
        return
    if key in settings_view.GATED_KEYS and not await _confirm(
            app, f"{key} {values[key]} {app.caps.g['arrow']} {value}",
            settings_view.consequence(key, str(value), values)):
        app.screen.blank()
        return
    try:
        app_settings.set_runtime(key, value)
    except OSError:
        app.screen.chrome(f"I couldn't save that — {app_settings.settings_path()} won't take a write")
        app.screen.blank()
        return
    app.restate_model()
    shown, note = settings_view.shows(key, app_settings.runtime_all())
    if key.endswith("model") and not note:
        note = _unlisted(str(value), values)
    app.screen.chrome(f"{key} {app.caps.g['arrow']} {shown or '(unset)'}" + (f" — {note}" if note else ""))
    app.screen.blank()


def _setting_row(app, key: str, values: dict) -> Text:
    value, note = settings_view.shows(key, values)
    take = settings_view.accepts(key, app.screen.rw - 24 - cell_len(value))
    return cards.setting_row(app.caps, key, value, note, take, app.screen.rw)


def _unlisted(name: str, values: dict) -> str:
    """What a model name the provider does not publish is worth saying — once, at the write.

    Nothing is refused: an openai_compatible base_url can be pointed at anything, and a false "there is
    no such model" would be worse than the 404 it saves. But `/model does-not-exist-9000` printed
    `model → does-not-exist-9000` in the same words a real switch prints, so the first news of a typo
    was her next turn failing. One clause on the line reporting the write is the whole warning, and it
    is not repeated on the read-back: a warning that comes back every time you look is one nobody
    reads. A name matching ANOTHER provider's shape is the useful half — `model` is one global setting,
    so a name it refuses is a provider switch half made. A custom `base_url` silences both."""
    from kotoba.core import providers

    if not name or str(values.get("base_url", "")).strip():
        return ""
    spec = providers.get_spec(str(values["provider"]))
    if name in {choice.id for choice in spec.models + spec.code_models}:
        return ""
    if not providers.serves_model(name, spec.id):
        for other in providers.PROVIDERS.values():
            if other.id != spec.id and other.model_match and re.search(other.model_match, name.lower()):
                return (f"{other.label} is the one that serves that name — /set provider {other.id} "
                        "goes with it")
    return f"{spec.label} doesn't list that one — a real name still works, a typo fails on her next turn"


async def _approvals(app, arg: str) -> None:
    """What she runs without asking, and the way to take any of it back.

    Each row says what the grant lets her do NOW, so one neutered by the interpreter denylist reads as
    spent, not live. Revoking only makes her ASK more, so it needs no confirm rail: the friction the
    CLI keeps is for turning a gate OFF. A grant goes by NAME or by NUMBER, because an exact grant is a
    whole command line and asking somebody to retype it is asking them to leave it there — so no two
    rows may draw the same thing: three deploy scripts in different directories all elided to `bash
    /home/…/deploy.sh` at thirty-six columns, and the grant somebody recognised carried a different
    number from the one they meant. Colliding rows are drawn whole, and the listing is fitted head
    first, so a cut tail leaves every number pointing at the grant it was printed beside."""
    db = app.session.engine.db
    verb, _, rest = arg.strip().partition(" ")
    verb, target = verb.lower(), rest.strip()
    app.screen.separate()
    saved = list(await db.list_approved_commands())
    patterns = [r["pattern"] for r in saved]
    if verb in ("rm", "remove", "revoke", "delete"):
        if target.isdigit() and 1 <= int(target) <= len(patterns):
            target = patterns[int(target) - 1]
        if not patterns:
            app.screen.chrome(f"there's nothing to take back — {settings_view.unasked()}")
        elif not target:
            app.screen.chrome("which one? /approvals lists them, then /approvals rm <name-or-number>")
        elif target.isdigit():
            app.screen.chrome(f"there's no {target} — she has {_count(len(patterns), 'grant')}, so the "
                              f"numbers run 1 to {len(patterns)}")
        elif target not in patterns:
            app.screen.chrome(f"{target} isn't one she's saved — /approvals lists the ones that are")
        else:
            await db.delete_approved_command(target)
            app.screen.chrome(f"revoked {target} {app.caps.g['arrow']} {_after_revoke(target)}")
        app.screen.blank()
        return
    if verb:
        app.screen.chrome("/approvals lists them, /approvals rm <name-or-number> takes one back — "
                          f"{verb!r} is neither")
        app.screen.blank()
        return
    if not saved:
        app.screen.chrome(f"nothing is always-allowed — {settings_view.unasked()}")
        app.screen.blank()
        return
    built, drawn = [], []
    for i, grant in enumerate(saved, 1):
        pattern, scope = grant["pattern"], grant.get("scope", "command")
        built.append(rows.grant_rows(app.caps, i, pattern,
                                     settings_view.grant_permits(pattern, scope), app.screen.rw))
        drawn.append(built[-1][0].plain.strip().removeprefix(f"{i}.").strip())
    for i, twin in enumerate(rows.alike(drawn), 1):
        if twin:
            grant = saved[i - 1]
            scope = grant.get("scope", "command")
            built[i - 1] = rows.grant_rows(app.caps, i, grant["pattern"],
                                           settings_view.grant_permits(grant["pattern"], scope),
                                           app.screen.rw, whole=True)
    revoke = "these run without a card — /approvals rm <name-or-number> makes her ask again"
    tall = len(wrap(app.caps.t(revoke), app.screen.rw))
    whole, _ = listing.plan(app.caps,
                            [sum(app.screen.rows_of(row) for row in block) for block in built],
                            spent=2 + tall, note=3 + tall)
    _tag(app, "ALWAYS ALLOWED")
    for block in built[:whole]:
        for row in block:
            app.screen.row(row)
    app.screen.blank()
    held = _count(len(saved) - whole, "grant")
    if len(saved) > whole:
        app.screen.chrome(fit(app.screen.rw,
                              f"+{held} not drawn, all in effect — a taller window lists them",
                              f"+{held} not drawn, all in effect", f"+{held} not drawn"))
    app.screen.chrome(revoke)
    app.screen.blank()


def _after_revoke(family: str) -> str:
    from kotoba.core import approval, sandbox

    backend = sandbox.backend_name()
    if backend == "docker":
        return "in a container that changes nothing — anything that isn't dangerous runs unasked"
    if (backend == "local" and approval.is_auto_safe_family(family)
            and not approval._windows_shell()):
        return "a plain read like that still runs unasked inside her workdir"
    return "she'll ask before that again"


# --- the long job ----------------------------------------------------------------------------------

def _work(app, arg: str) -> None:
    """The receipt so far. Not a second way of drawing anything: the same opening bracket the turn
    already committed, with the clock where `started` was, and under it exactly the rows that will land
    between the brackets when it finishes.

    The rows are drawn STILL — a snapshot goes into the transcript and is never repainted, so a spinner
    frame in it is an animation that stopped. `/work N` opens a past one, and it can only come from
    here: the backend keeps one record per session and overwrites it. FITTED, with both BRACKETS
    outside the budget, because a job that ended showing only the mark it went out with reads as one
    still going: the cut falls on the middle. A folded nameplate is two rows at a narrow window, so
    each block's height is ASKED of the renderer, never assumed to be one row a helper."""
    screen = app.screen
    screen.separate()
    if not app.works:
        screen.chrome("nothing of hers is running out here — /work is for the long job, and there "
                      "isn't one")
        screen.blank()
        return
    if arg:
        n = int(arg) if arg.isdigit() else 0
        if not 1 <= n <= len(app.works):
            span = "job 1" if len(app.works) == 1 else f"1 to {len(app.works)}"
            screen.chrome(f"there's no job {arg} this session — {span} is all of them")
            screen.blank()
            return
        job = app.works[n - 1]
    else:
        job = next((w for w in app.works if w.state == "running"), app.works[-1])
    aside = "what she's thinking isn't kept — this is what she's done, not how she got there"
    ended = job.state != "running"
    mid = [[rows.tool_text(app.caps, tool, screen.rw, still=True)] for tool in job.tools]
    for i, helper in enumerate(job.helpers, 1):
        block = list(rows.helper_rows(app.caps, helper, i, screen.rw, still=True))
        if helper.summary:
            block.append(Text("     " + head(app.caps.t(re.sub(r"[`*]", "", helper.summary)),
                                             screen.w - screen.gutter - 7, app.caps.unicode),
                              style="chrome"))
        mid.append(block)
    said = screen.receipt_rows(job.summary) if ended and job.summary else []
    gifts = [rows.gift_rows(app.caps, app.gifts[n - 1], n, screen.rw)
             for n in (job.gift_ns if ended else ())]
    end = ([said] if said else []) + gifts
    kinds = (["tool"] * len(job.tools) + ["helper"] * len(job.helpers)
             + ["summary"] * bool(said) + ["gift"] * len(gifts))
    spent = 1 + (1 if ended else 0) + len(wrap(app.caps.t(aside), screen.rw))
    whole, spare = listing.plan(app.caps,
                                [sum(screen.rows_of(row) for row in block) for block in mid + end],
                                spent=spent, note=spent + 1)
    # The summary is the one block cut by ROWS instead of dropped (`Screen.receipt_rows`).
    part = said[:spare] if said and whole == len(mid) and spare > 0 else []
    lost = kinds[whole:]
    each = [_count(lost.count(k), k) for k in ("tool", "helper", "gift") if k in lost]
    if "summary" in lost:
        each.append(f"{len(said) - len(part)} rows of the summary")
    many = " and ".join([", ".join(each[:-1]), each[-1]] if len(each) > 2 else each)
    note = fit(screen.rw, f"+{many} not drawn — a taller window opens more of it",
               f"+{many} not drawn", f"+{len(lost)} not drawn", f"+{len(lost)} more") if many else \
        fit(screen.rw, "the rest of it needs a taller window", "+the rest not drawn")
    screen.row(rows.work_row(app.caps, job, screen.rw, opening=True, word=duration(job.elapsed)))
    for block in mid[:whole]:
        for row in block:
            screen.row(row)
    if lost and whole < len(mid):
        screen.chrome(note)
    if ended:
        screen.row(rows.work_row(app.caps, job, screen.rw))
        for block in end[:max(0, whole - len(mid))]:
            for row in block:
                screen.row(row)
        for row in part:
            screen.row(row)
    if lost and whole >= len(mid):
        screen.chrome(note)
    screen.chrome(aside)
    screen.blank()


async def _stop(app, arg: str) -> None:
    """Names what would die, then asks. A turn owns the terminal for as long as it lasts, so `/stop` can
    never be typed at one — what it can find out here is the long job, and `cancel_work` stops it.

    A word is answered before any of that, and this is the member of the no-argument family where the
    silence cost the most. `/work N` establishes that the jobs of a session are NUMBERED, so `/stop 2`
    reads as "stop job 2" — and it opened the rail for whatever happened to be running, which need not
    be job 2, over a card whose one key ends the thing. A number misread on a rail that kills something
    may not go unanswered, so the sentence says which job it would have reached instead."""
    from kotoba.tools.builtin import cancel_work

    if _bare(app, "/stop", "it stops the one that's running, and only ever that one", arg):
        return
    job = app.work if (app.work and app.work.state == "running") else None
    app.screen.separate()
    if job is None:
        app.screen.chrome("nothing of hers is running out here — /stop is for the long job, and there "
                          "isn't one")
        app.screen.blank()
        return
    head_line = f"stop the long job? {duration(job.elapsed)} in"
    why = (f"{_count(len(job.tools), 'step')} so far, on: {job.goal}. None of it is kept if you stop "
           "it now")
    if not await _confirm(app, head_line, why, "stop it", "let it run", "stopped", "left it running"):
        app.screen.blank()
        return
    await cancel_work.execute({}, SimpleNamespace(session_id=app.session.session_id))
    _cut(job)
    app._land_work()


def _cut(job) -> None:
    """An open row is stamped on the way out: a spinner committed to the transcript is an animation
    that has stopped."""
    now = time.monotonic()
    job.state, job.stopped = "interrupted", now
    for tool in job.tools:
        if tool.state == "running":
            tool.state, tool.detail, tool.stopped = "interrupted", "stopped", now
    for helper in job.helpers:
        if helper.state in ("running", "queued"):
            helper.state, helper.stopped = "interrupted", now


# --- what came before, and what she hands you ------------------------------------------------------

async def _sessions(app, arg: str) -> None:
    """The conversations before this one, in the same flush-left listing /settings and /help use.

    None of them reopens. Resuming needs a loop this CLI does not have, so rather than let a numbered
    list imply otherwise the listing takes no argument and says what to do instead — which is the thing
    that actually works today: `session_search` is FTS5 over every turn in this table.

    Newest first, and the window is why that matters: twenty of them with the heading and the two closing
    lines are 24 rows, so at 80x24 the heading and the four NEWEST went into the scrollback — a
    newest-first list losing the exact end it was opened for. Fitted, the OLDEST are the ones that go,
    and how many is said out loud."""
    screen = app.screen
    screen.separate()
    if arg:
        screen.chrome("I can't reopen one of those from here — tell me what you're after and I'll go "
                      "and find it")
        screen.blank()
        return
    listed = await app.session.engine.db.list_sessions(SESSIONS_SHOWN)
    built = []
    for row in listed:
        mine = row["id"] == app.session.session_id
        tail = _count(row["turns"], "turn") + (f" {app.caps.g['bullet']} this one" if mine else "")
        room = screen.rw - 24 - cell_len(tail)
        hint = head(str(row["opened"] or ""), max(room - 4, 8), app.caps.unicode)
        built.append(cards.info_row(app.caps, settings_view.when(row["started_at"]),
                                    f'"{hint}"', tail, screen.rw))
    started = "when each one ended isn't kept — this is when it started and how much is in it"
    reopen = "these don't reopen — say what you're after and she'll go and find it in them"
    tall = len(wrap(app.caps.t(reopen), screen.rw))
    whole, _ = listing.plan(app.caps, [screen.rows_of(r) for r in built],
                            spent=2 + tall + len(wrap(app.caps.t(started), screen.rw)),
                            note=3 + tall)
    _tag(app, "SESSIONS")
    for row in built[:whole]:
        screen.row(row)
    screen.blank()
    older = len(listed) - whole
    if older:
        screen.chrome(fit(screen.rw, f"+{older} older ones not drawn — these are the newest {whole}",
                          f"+{older} older ones not drawn", f"+{older} more"))
    else:
        screen.chrome(started)
    screen.chrome(reopen)
    screen.blank()


def _open(app, arg: str) -> None:
    """`/open N`, and the listing anything that is not one of her numbers falls back to.

    The number is bounded on BOTH sides. Indexing the list directly let `/open 0` reach `-1` and
    `/open -1` reach `-2`, so the two answers a person gives when they mean "the first one" each
    launched a different file from the one on the row — silently, and through `xdg-open`.

    The fallback listing is fitted like every other one: thirty gifts are sixty-one rows, since a link
    takes a second row unwrapped so that it stays selectable. Head first, and what is held back keeps
    its NUMBER — `/open 30` opens the thirtieth whether or not the thirtieth was drawn."""
    screen = app.screen
    screen.separate()
    if not app.gifts:
        screen.chrome("she hasn't handed you anything yet")
        screen.blank()
        return
    try:
        gift = app.gifts[_ordinal(arg, len(app.gifts)) - 1]
    except (ValueError, IndexError):
        built = [rows.gift_rows(app.caps, one, i, screen.rw)
                 for i, one in enumerate(app.gifts, 1)]
        whole, _ = listing.plan(app.caps,
                                [sum(screen.rows_of(row) for row in block) for block in built])
        for block in built[:whole]:
            for row in block:
                screen.row(row)
        held = len(built) - whole
        if held:
            screen.chrome(fit(screen.rw,
                              f"+{held} not drawn \u2014 /open {len(app.gifts)} still opens the last",
                              f"+{held} not drawn, still openable", f"+{held} more"))
        screen.blank()
        return
    problem = _launch(gift.target)
    screen.chrome(problem or f"opening {app.caps.g['arrow']} {gift.target}")
    screen.blank()


def _ordinal(arg: str, count: int) -> int:
    """The 1-based row number ARG names, or a ValueError for anything that is not one of them.

    Python's negative indexing is the trap: a bare `int(arg) - 1` turns `0` into the LAST row and `-1`
    into the second-to-last, so a number nobody printed still resolves to a row. Only 1..count is a
    number she gave out."""
    n = int(arg)
    if not 1 <= n <= count:
        raise IndexError(n)
    return n


def _launch(target: str) -> str | None:
    """Hand `target` to the desktop for real — xdg-open, macOS `open`, Windows `os.startfile`. Returns
    the sentence to print when nothing opened, None when the handoff happened; printing `opening →` and
    spawning nothing is the failure this exists to avoid.

    A path is JAILED to her files before anything is spawned. A gift's target is a string the model
    wrote, the desktop launcher RUNS what it is handed for a `.desktop` file, and `../` resolved
    against her library reaches the whole disk, so a target that leaves the library is refused by name.
    A URL is not a path and skips the jail. Existence is checked FIRST and is the only honest check
    available: the spawn is detached on purpose, so its exit code lands after we have answered. The
    child gets no stdin — a handler that read the tty would take the keys out of her prompt."""
    import shutil
    import subprocess

    from kotoba.core import file_library, path_security

    if not target:
        return "that one carries nothing to open"
    if target.startswith(("http://", "https://")):
        spot = target
    else:
        library = file_library.library_dir()
        try:
            path = path_security.validate_within_dir(target, library)
        except (path_security.PathSecurityError, OSError, ValueError):
            return (f"that one isn't in your files — I only open what's under "
                    f"{cards._shown(library)}, and that path leaves it")
        if not path.exists():
            return f"there's nothing at {cards._shown(path)} to open — I won't pretend there is"
        spot = str(path)
    if os.name == "nt":
        try:
            os.startfile(spot)  # noqa: S606 — the platform's own opener, on the user's explicit /open
        except OSError:
            return f"that didn't open — it's at {spot}"
        return None
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    if shutil.which(opener) is None:
        return f"I can't open things on this machine (no {opener}) — it's at {spot}"
    try:
        subprocess.Popen([opener, spot], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return f"that didn't open — it's at {spot}"
    return None


def _attach(app, arg: str) -> None:
    """She reads what she is given, so a file has to actually reach her: an image or a PDF rides the
    next turn's context, and anything else is copied into the workdir where `read_file` can find it.
    The line lands in the BOX and waits for enter — you may want to say what to look for.

    The box is the prompt's `pending`, which seeds the buffer and leaves the caret in it, NOT the queue
    `_loop` drains around the editor: every line on that list was typed while she worked and already
    carries an enter, so queued there `/attach` bought a turn nobody asked for. Something she can SEE
    goes down the same road as a ctrl-v — an `[Image #n]` marker, no words. A file only copied into the
    workdir keeps a verb and names the file the way `read_file` takes it: `look at @{name}` sent her
    hunting for a literal `@sample.txt`, two rows under the one saying where it had just been put."""
    screen = app.screen
    screen.separate()
    if not arg:
        screen.chrome("give me a path — /attach ~/Downloads/trace.png")
        screen.blank()
        return
    path = os.path.abspath(os.path.expanduser(arg))
    name = os.path.basename(path) or arg
    n = app.shared_n + 1
    seen = os.path.splitext(name)[1].lower() in SEEN
    mark = marker(n, name)
    note = _carry(app, path, name, label=mark if seen else "")
    if note is None:
        screen.blank()
        return
    gift = state.Gift("sent", name, note)
    app.gifts.append(gift)
    for row in rows.gift_rows(app.caps, gift, len(app.gifts), screen.rw):
        screen.row(row)
    if seen:
        app.shared_n = n
    box = app.prompt.pending
    app.prompt.pending = box + ("" if not box or box.endswith((" ", "\n")) else " ") + (
        mark if seen else f"read {name}")
    screen.blank()


def _carry(app, path: str, name: str, label: str = "") -> str | None:
    """(what the gift row says), or None having said why it could not be carried.

    A refused attachment — the fifth file of a message — used to draw the SENT row over the refusal,
    announcing a file she is not carrying. A refusal now takes the branch its neighbours take: her
    sentence, no row, None.

    The other way is quieter, because the copy SUCCEEDS. `save_text` writes into the file library and
    `read_file` is jailed to the resolved workdir, which are the same directory only while
    `KOTOBA_WORKSPACE_DIR` is unset. Point it at a real project and the file lands where the Files
    panel shows it and she cannot read a byte of it — so the row names the place she actually reads."""
    import base64
    import mimetypes

    from kotoba.core import attachments, file_library, workspace

    try:
        with open(path, "rb") as fh:
            raw = fh.read(ATTACH_MAX + 1)
    except OSError:
        app.screen.chrome(f"I can't read {name} — check the path")
        return None
    library, reads = file_library.library_dir(), workspace.resolve_workdir(None)
    workdir = cards._shown(library)
    if len(raw) > ATTACH_MAX:
        app.screen.chrome(f"{name} is too big for me to carry — leave it in {cards._shown(reads)} and "
                          "I'll read it there")
        return None
    ext = os.path.splitext(name)[1].lower()
    if ext in SEEN:
        media = mimetypes.guess_type(name)[0] or "application/octet-stream"
        url = f"data:{media};base64,{base64.b64encode(raw).decode('ascii')}"
        if len(url) > ATTACH_MAX:
            app.screen.chrome(f"{name} is too big for me to carry — leave it in {cards._shown(reads)} and "
                              "I'll read it there")
            return None
        part = ({"type": "input_file", "filename": name, "file_data": url} if ext == ".pdf"
                else {"type": "input_image", "image_url": url})
        if not attachments.add(app.session.session_id, part, name=name):
            app.screen.chrome(f"{name} didn't fit. {_cap_note()}")
            return None
        return "she can see this one" if ext == ".pdf" else _reachable(app, name, url, label)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        app.screen.chrome(f"{name} isn't text and isn't a picture — I'd only see bytes")
        return None
    if file_library.save_text(name, text) is None:
        app.screen.chrome(f"I couldn't put {name} anywhere I can reach")
        return None
    if reads != library:
        return f"copied to {workdir} — but she reads {cards._shown(reads)}, so put it there instead"
    return f"copied to {workdir} — she'll read it there"


def _cap_note() -> str:
    """What both front doors say when the cap refuses a file: the web endpoint's 409 `detail`, word for
    word, said at the terminal too.

    The cap bounds ONE message rather than the session — `attachments.take()` drains the pile as the
    turn goes out — so the honest instruction is send this one first, never "you are out of room". The
    words live in two places for as long as the CLI must not import `kotoba.server`; the two are
    pinned equal rather than left to be kept in step by hand."""
    from kotoba.core import attachments

    return (f"I can only carry {attachments.MAX_PER_SESSION} files in one message — send this one and "
            "I'll take the next lot after it.")


def _reachable(app, name: str, url: str, label: str) -> str:
    """Keep a shared image reachable for the REST of the conversation, not only for the turn it rode in
    on, and say so in the gift row.

    The model is shown base64 it can never quote back, and the part is dropped after one turn, so "what
    was in that picture?" three turns later had nowhere to go. The only tool that could still reach
    those bytes was `remember_image`, which files them in her DURABLE visual memory — that made looking
    again and keeping forever the same gesture. Saved into the library and logged as a session capture
    instead, `view_capture` re-opens it on any later turn.

    Best-effort on purpose: the library can refuse, and a share she can see NOW is worth more."""
    from kotoba.core import file_library, session_captures

    rel = file_library.save_image(f"{SHARED_DIR}/{name}", url)
    if not rel:
        return "she can see this one"
    caption = f"{label} — the user shared this with you" if label else "the user shared this with you"
    session_captures.record(app.session.session_id, rel, caption)
    return "she can see this one — and can open it again later"


def _line_up(app) -> tuple[list, bool]:
    """(the helpers `/helpers` answers about, whether they are out right now).

    One reading, off `rows.work_is_live` — the read the band, the bar's chip and the bar's phrase
    already share — so the command cannot say `no helpers` under a bar that says WORKING. The live
    job's line-up banks on `app.work.helpers` (`app._helper`) and the command read only
    `app.last_helpers`, which the TURN's `_commit_roster` alone ever wrote: a job's three helpers
    were `no helpers yet` while they ran AND after they came back, drawn in full by `/work 1` a row
    above. Ported blind from the prototype, where the job's helpers sat on `w.helpers` just the same;
    a landed job now hands its line-up to `last_helpers` (`app._land_work`), so the two readings are
    the live list while the job runs and the last one back once nothing does."""
    if rows.work_is_live(app._state()):
        return app.work.helpers, True
    return app.last_helpers, False


def _helpers(app, arg: str) -> None:
    """The full log of the line-up that is out, or of the last one back, bounded twice — by how many
    helpers the window holds and by how many steps one of them does.

    Neither had a bound: a line-up of twelve with eight steps each is 133 rows, and one helper on a
    long job outruns any window by itself. Both are fitted head first, because the number on the
    nameplate is what `/helpers 2` names — the same number the roster and `/work N` print, since all
    three count one list in insertion order. The budget counts the plate in ROWS, because a narrow
    window folds it into two; planned as one it would draw a listing taller than the window it just
    measured. The summary is bought BEFORE the steps of a helper that will not fit whole: it is where
    the outcome is said in words, and steps cut off a log that kept it still read as a log."""
    screen = app.screen
    screen.separate()
    line_up, out = _line_up(app)
    if not line_up:
        screen.chrome("none out on this job yet" if out else
                      "no helpers yet — she sends them out from a long job, and none has needed one "
                      "so far")
        screen.blank()
        return
    try:
        picked, alone = [line_up[_ordinal(arg, len(line_up)) - 1]], True
    except (ValueError, IndexError):
        picked, alone = line_up, False
    lead = 0 if alone else 1
    plate = rows.helper_height(screen.rw)
    said = [len(wrap(app.caps.t(h.summary), screen.rw - 5)) if h.summary else 0 for h in picked]
    whole, spare = listing.plan(app.caps,
                                [plate + 1 + len(h.steps) + n for h, n in zip(picked, said)],
                                spent=lead, note=lead + 2)
    drawn, rest = (picked[:whole], picked[whole:]) if whole else (picked[:1], picked[1:])
    cut, ends = 0, True
    if not whole:
        room_steps = spare - plate - 2 - said[0]
        ends = room_steps >= 1
        cut = len(picked[0].steps) - max(0, room_steps if ends else spare - plate - 2)
    if not alone:
        busy = sum(1 for h in line_up if h.state in ("running", "queued"))
        head_line = (f"{_count(len(picked), 'helper')} last time" if not out
                     else f"{_count(busy, 'helper')} out right now"
                     + (f", {len(line_up) - busy} back" if busy < len(line_up) else "")
                     if busy else f"{_count(len(picked), 'helper')} back, the job's still going")
        screen.chrome(f"{head_line} — /helpers 2 for just that one")
    for helper in drawn:
        for row in rows.helper_rows(app.caps, helper, line_up.index(helper) + 1,
                                    screen.rw, still=True):
            screen.row(row)
        for step in helper.steps[:len(helper.steps) - cut]:
            screen.chrome(rows.step_line(app.caps, step, screen.rw))
        if cut:
            screen.chrome(fit(screen.rw, f"     +{_count(cut, 'step')} not drawn", f"     +{cut} more"))
        if helper.summary and ends:
            for line in wrap(app.caps.t(helper.summary), screen.rw - 5):
                screen.row(Text("     " + line, style="chrome"))
        screen.blank()
    if rest:
        screen.chrome(fit(screen.rw,
                          f"+{_count(len(rest), 'helper')} not drawn — /helpers {len(drawn) + 1} "
                          "opens the next one",
                          f"+{_count(len(rest), 'helper')} not drawn", f"+{len(rest)} more"))
        screen.blank()


# --- the two-key rail ------------------------------------------------------------------------------

async def _confirm(app, head_line: str, why: str, *labels: str) -> bool:
    """Only sandbox, provider and `/stop` come through here. The two key labels and the two outcomes are
    the card's, because `y change it` under a question about killing a job is the wrong answer to the
    wrong question. A key that is not one of them is DISCARDED with a flash — "never swallow a
    keystroke" must not apply to a safety gate.

    The glass is the app's card machinery (`app._confirm_card`): one mechanism for every rail, so the
    rail covers the transcript and gives it back, and what stays behind is the one-row receipt. The
    transient Live that used to live here pushed the transcript up by its own height and the erase
    never brought it back."""
    card = cards.Confirm(head_line, why, *labels)
    if not app.caps.interactive:
        app.confirm = card
        try:
            for row in cards.confirm_rows(app.caps, card, app.card_w):
                app.screen.row(row)
            app.screen.chrome("left alone — there's no terminal to ask")
        finally:
            app.confirm = None
        return False
    return await app._confirm_card(card)


# --- the chip a section is headed by -----------------------------------------------------------------

def _tag(app, word: str) -> None:
    row = Text()
    row.append_text(cards._plate(app.caps, " ".join(word), "ink"))
    row.append(" ")
    row.append(app.caps.g["rule"] * max(0, app.screen.rw - row.cell_len - 4), style="chrome")
    app.screen.row(row)


def _pairs(app, pairs: tuple[tuple[str, str], ...]) -> None:
    wide = max(cell_len(left) for left, _ in pairs) + 2
    for left, right in pairs:
        app.screen.row(Text.assemble((f"{left:<{wide}}", "hard"), (app.caps.t(right), "chrome")))


HANDLERS = {"/help": _help, "/plan": _plan, "/last": _last, "/model": _model,
            "/face": _face, "/emotions": _emotions, "/clear": _clear, "/calm": _calm,
            "/plate": _plate, "/settings": _settings, "/set": _set, "/approvals": _approvals,
            "/work": _work, "/stop": _stop, "/sessions": _sessions, "/open": _open,
            "/attach": _attach, "/helpers": _helpers}
