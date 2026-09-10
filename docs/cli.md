<a href="https://kotoba.rodhnin.com/docs/cli"><img src="assets/banners/cli.webp" alt="The terminal" width="100%"></a>

Kotoba has three faces. One is a Live2D companion in a browser that you talk to out loud. One is
this: a terminal, where she writes instead of speaking. The third is a Discord bot, covered at the
bottom of this page.

Same engine, same memory, same tools, same files. Ask her something here and she remembers it in the
browser tomorrow.

Full guide: **[kotoba.rodhnin.com/docs/cli](https://kotoba.rodhnin.com/docs/cli)**

<img src="assets/deco/divider-tape.webp" alt="" width="100%">

## Install

```bash
pip install "kotoba-companion[cli]"   # or: pip install -e "api/[cli]" from a clone
kotoba doctor                         # what is missing, and what it costs you
kotoba setup                          # pick a provider, hand over a key
kotoba --once "hola"                  # ask one thing
```

`doctor` is the one to run first. It checks in the order that decides whether she can run at all —
platform, interactive terminal, Python version, soul file, database, encryption, file permissions,
saved keys, model key, reasoning, voice key, voice tags, sandbox, web UI — then the optional extras,
then what is on this machine (Node,
a browser, ripgrep), then your MCP servers. A check that cannot be answered because an earlier one
failed is skipped and says which one to fix first. It never says only "not found": it says what you
lose.

> <img src="assets/deco/badge-note.webp" alt="Note" width="26" align="left">
>
> The sharpest portrait is drawn by `chafa`, a system package rather than a Python one. Without it
> she falls back to half-blocks, drawn in-process, which still look like her. `doctor` does not check
> for chafa.

<img src="assets/deco/divider-ticket.webp" alt="" width="100%">

## Flags

| Flag | What it does |
|---|---|
| `--once TEXT` | ask one thing, print the answer, exit |
| `--sessions` | open on your past conversations, and read one back |
| `--settings` | open on the configuration, a section at a time |
| `--version` | print the version and exit |
| `--plain` | no colour |
| `--ascii` | no unicode either |
| `--calm` | reduced motion: nothing on screen moves |
| `--no-face` | no portrait, just her kaomoji |

`--sessions` and `--settings` open on a menu rather than on her greeting: you came to look at
something, and being said hello to first is two seconds of streaming in front of it. Both together is
one screen, so the conversations win. `/sessions` lists them too, but only this picker **replays**
one — the slash command deliberately refuses to reopen a conversation from inside another.

**`--no-face` is not the only thing that drops the portrait.** `--plain` and `--ascii` drop it too:
the portrait needs both colour and unicode, so removing either removes her. So does a terminal
narrower than 62 columns, output that is not a terminal at all, and an install without the `cli`
extra. `--no-face` is simply the way to ask for it deliberately. In every case she falls back to the
kaomoji, which is a face too.

The four rendering flags work on `kotoba setup` on either side of the subcommand — `kotoba --ascii
setup` and `kotoba setup --ascii` are the same thing. `doctor` takes the two it can act on (`--plain`
drops its status colour, `--ascii` folds its em dashes) and refuses `--calm` and `--no-face` by name,
saying why; `serve` refuses all four, because nearly every line it prints belongs to the two servers
it is watching. A flag a subcommand cannot act on is refused rather than swallowed, whichever side it
is typed.

The fold is a table of chrome — em dashes, `·`, `→`, the box rules — and nothing beyond it, so
`kotoba --ascii --once "¿qué tal?"` still comes back with its accents. A flag for a terminal that
cannot draw a glyph is not a flag for a product that speaks your language.

<img src="assets/deco/divider-stars.webp" alt="" width="100%">

## First run

`kotoba setup` is her, asking. It is also the first thing `kotoba` itself does when no key is
configured, so most people never type it — they type `kotoba` and she opens with it.

She asks six things and offers a seventh, and each answer is a real field rather than a form entry:

| She asks | Where it lands | What it changes |
|---|---|---|
| which brain | `provider` | who answers your turns |
| which model | `model` | which of that company's models does the thinking — **and what it costs you**. The list is cheapest first, and a row that costs meaningfully more than the top one says how much more per word she says back, and how much of the conversation it can hold. Type any other id of theirs and she takes it — saying plainly that she has not checked it |
| its key | the keystore, encrypted in the database — never a file in plain text | whether she can think at all. It is proved with a real call before it is stored |
| your name | `user_profile.name` | what she calls you, in every conversation |
| her name | `soul_config.name` | what she answers to, and what her nameplate says |
| which language | `soul_config.language` | how she replies **and** how she hears you — a pinned language pins transcription too, so a short sentence is not detected as the wrong one |
| a voice (optional) | the ElevenLabs key, encrypted beside the other one | whether she can hear you and speak. **Skip it and everything else still works** — she reads and writes. `kotoba setup` asks again whenever you want it. The speaking itself is the `voice` extra: without it she says so, keeps the key, and names `pip install "kotoba-companion[voice]"` |

The model is asked **before** the key on purpose: the key is proved with a real call to the model you
just picked, so a key with no access to it fails there — where you can still paste another one —
instead of on your first real question. A model belonging to the other company is refused with the
reason rather than stored. Any other id is taken on your word, and if the provider turns out not to
have it, the refusal names **the model**, never the key: that misattribution sends people off to make
a second key that fails the same way.

Each key is checked against its service before it is stored: a well-formed key with no credit is
indistinguishable from a good one until the first real question. The ElevenLabs check asks
`/v1/voices` and not `/v1/user` — keys there carry scopes, and a key that speaks perfectly answers
`/v1/user` with a 401, so the obvious check would call a good key bad.

Nothing from either service reaches you as a stack trace. A key it does not know, an account with no
credit, no network at all, a model the provider does not serve, and a key belonging to the other
provider each get their own sentence — and the page where a key is made survives every one of them.

Nothing is a dead end either. A wrong option or a key that does not work is asked again, up to three
attempts in all, and every question tells you what an empty line does there. **Ctrl+C stops it
wherever you are**, and everything after the model key is skippable, so an interrupt there keeps what
you already answered.

`pip install kotoba-companion` with no extras still runs `kotoba setup`, `kotoba doctor` and
`kotoba --once`, and the wizard drops to plain text there — same words, no chrome. What that install
does not have is her terminal: `kotoba` on its own prints `pip install "kotoba-companion[cli]"` and
exits, and `kotoba serve` names `kotoba-companion[server]` the same way.

<img src="assets/deco/divider-tape.webp" alt="" width="100%">

## Talking to her

```bash
kotoba --once "explain what a WAL journal is"
kotoba --once "read notes.md and tell me what changed"
```

She writes for a screen here: fenced code with a language, real file paths, real URLs, digits. That is
a deliberate switch — over voice all of those are banned, because "backtick backtick backtick python"
is not something anyone wants to hear. Nothing configures this: the register follows the surface she
is speaking on, so you never have to keep two settings in step.

**Ctrl+C stops the turn, not the program.** Whatever she had already said is kept — a half answer is
still something the next turn has to know she said. At an idle, empty prompt there is no turn to stop,
so the first Ctrl+C warns you and the second leaves.

<img src="assets/deco/divider-ticket.webp" alt="" width="100%">

## At the prompt

Nineteen commands. `/help` prints them, `/help keys` prints the keys.

| Command | What it does |
|---|---|
| `/help` | what she can do here — `/help keys` opens just the keys |
| `/plan` | the current plan: the steps, and where it has got to |
| `/settings` | everything she has configured |
| `/set` | change one of them — `/set sandbox docker` |
| `/approvals` | see or revoke what she runs without asking — `/approvals rm 3` |
| `/work` | look inside a long job, running or finished — `/work 1` |
| `/stop` | call off the long job she is running in the background |
| `/sessions` | the conversations you have had before this one |
| `/open` | open something she handed you — `/open 1` |
| `/attach` | send her a file — `/attach ~/shot.png` |
| `/helpers` | the full log of her helpers, out now or last time |
| `/last` | print the last tool result in full |
| `/model` | see or switch the model — `/model grok-4.3` |
| `/face` | set one emotion — `/face excited` |
| `/emotions` | all 14 faces at once |
| `/clear` | clear the screen, keep the session |
| `/calm` | toggle reduced motion |
| `/plate` | quieten the nameplate on her everyday replies |
| `/quit` | leave — `ctrl-d` and `/exit` do the same |

And the keys:

| Key | What it does |
|---|---|
| `alt-enter` | start a new line without sending |
| `enter` on an empty line | show whatever is waiting out here |
| `@` | name a file in her workdir — completes as you type |
| typing while she works | it waits; press Enter and it becomes the next turn on its own |
| `y` `n` `?` `a` `t` | answer an approval — no enter, the card draws its own keys |
| `ctrl-c` | stop the turn; twice on an empty box leaves |
| `ctrl-d` | leave |

<img src="assets/deco/divider-stars.webp" alt="" width="100%">

## When she asks permission

Anything that runs a command on your machine stops and asks. In the interactive terminal the card
draws its own keys and takes **one keypress, with no Enter**:

```
git status
y go ahead   a always allow git   t always allow just this line   n no   ? what it touches
```

The rail names the keys **that card** offers, never a fixed list. `a` is withheld for a family that
carries data off the machine — `curl` and its siblings are always one line at a time.

- **y** runs it, once, right now, in the same turn.
- **n** refuses. She is told, and says so plainly instead of pretending.
- **a** remembers the command family (`git`, `npm`) so it stops asking.
- **t** remembers that one line, matched byte for byte, and nothing else.
- **?** shows what the command reaches — the rest of a line too long for the card included.

Outside the interactive session — under `--once` — the same question arrives as a line prompt that
does take Enter:

```
git status
allow? [y/N/a/t] — a = always allow git — t = always allow just this line
```

The bracket shrinks to `[y/N/t]` or `[y/N]` when a key is withheld, and `?` is not offered there.

Revoke a grant later with **`/approvals`** in the terminal, or in the browser under
Settings → Security → Allowed commands.

Not every command is offered every key. A line that chains commands or carries shell metacharacters
gets no **a**, because the family name would no longer describe what runs — she says so on the card. A
destructive command gets neither **a** nor **t** — on the line prompt that leaves `[y/N]` alone, and
that is not a bug: a blanket "always allow" over `rm -rf` is not a promise this program will let you
make. The card always keeps `?`, whatever else it withholds.

Reading is free. `read_file`, `search_files`, a `ls` or a `cat` inside her working folder never
interrupt you, because a gate that asks about everything trains you to approve without reading.

<img src="assets/deco/divider-tape.webp" alt="" width="100%">

## Running both faces

```bash
kotoba serve                        # backend on 8000, web UI on 3000; one Ctrl+C stops both
kotoba serve --port 8080            # the backend somewhere else
kotoba serve --web-port 3100        # and the web UI somewhere else
kotoba serve --open                 # and open the web UI in your browser
```

A wheel carries the web UI already built, so there is nothing to start and nothing to install: the
backend serves it from its own port, at `/app`. The development server is preferred whenever it can
run — your edits have to show — so in a clone with Node it starts as before.

Each child runs in its own process group. `npm run dev` forks a worker that outlives npm, so
signalling npm alone leaves something holding port 3000 — the whole group is signalled instead. With
neither a development server nor a built one, it runs the backend alone and says which piece is
missing. `kotoba doctor` reports the same thing under `web ui`.

`--open` waits for the web UI to answer on its port before opening anything, and says so if it never
does. A browser window at a port that never came up is worse than a sentence. It is off by default on
every platform.

<img src="assets/deco/divider-stars.webp" alt="" width="100%">

## Discord

A third face. She lives in a Discord server: reading a channel, answering when called, reshaping the
server for an administrator, talking out loud in a voice channel. Each channel is its own conversation,
on the same engine, database and workspace as the terminal.

Full guide: **[kotoba.rodhnin.com/docs/discord](https://kotoba.rodhnin.com/docs/discord)**

### Install and run

```bash
pip install "kotoba-companion[discord]"   # or: pip install -e "api/[discord]" from a clone
kotoba discord --save-token               # paste the bot token; it is stored encrypted
kotoba discord                            # connect, and stay until Ctrl+C
kotoba discord --guild 000000000000000000 # only this server (repeatable)
```

One more process beside whatever you already run. It starts no cron ticker of its own, refuses the
four rendering flags by name, and holds her Discord tools alone: nothing else sees them.
`--save-token` puts the token in the same encrypted keystore as your model key, which the bot reads
first. `DISCORD_BOT_TOKEN` is honoured too, but it is the escape hatch for a first run with no
database yet. Neither prints it: a token-shaped string is redacted out of any error she reports.

### Making the bot

In the Developer Portal: **New Application**, then **Bot**, then **Reset Token** — that is the token,
shown once. Invite her from **OAuth2 → URL Generator** with the **`bot`** scope, and nothing else: she
registers no slash commands, so you talk to her in ordinary messages.

| What she does | Permission |
|---|---|
| read a channel, answer in it, reply to what called her | View Channels, Send Messages, Read Message History |
| draw an approval card (an embed with buttons), attach a file from her workspace | Embed Links, Attach Files |
| create, rename, move, delete channels, categories, forums; topics, slowmode | Manage Channels |
| create and hand out roles, and set who can see a channel | Manage Roles |
| rename, time out, kick, ban, move between voice channels | Manage Nicknames, Moderate Members, Kick Members, Ban Members, Move Members |
| join a voice channel and speak | Connect, Speak |

The first two rows are all she needs to talk; the rest is server management, so grant only what you
want her able to do. She never hands out **administrator**, cannot touch a role or a person above her
own, and a permission she does not hold comes back as a sentence rather than a crash.

### The two privileged intents

**Portal → Bot → Privileged Gateway Intents: turn ON Message Content and Server Members.** She asks
for both at connect time, so with either off Discord refuses the connection and `kotoba discord`
prints those two names and exits rather than starting half-working. Neither is optional: without
Message Content every message arrives empty — a room of blank lines, answered by nothing, with no
error anywhere — and Server Members is what gives her the member list: without it she cannot see who is in a voice channel, resolve somebody by name, or act on a person at all.

### Settings

| Variable | What it does |
|---|---|
| `KOTOBA_DISCORD_OWNER_ID` | your Discord user id — the account she treats as **her person**, never inferred from anything anyone types. Unset, nobody is her person and the owner-only tools are reachable by nobody |
| `KOTOBA_DISCORD_GUILDS` | comma-separated server ids she may work in. **Empty means every server she was invited to.** `--guild` overrides it |
| `KOTOBA_DISCORD_HOME_CHANNELS` | comma-separated channel ids where she answers **everything**, not only what names her. **Empty means no such channel** |

### When she answers

**She does not answer every message in an ordinary channel.** A bot that answers every line is a bot
people mute. In a DM she answers everything, as long as you share a server she is allowed to work in. In a server she answers a **mention** — the user mention
or the **integration role** carrying her name, which half the room picks and which appears in no
mention list at all — a **reply** to something of hers, any message in a **home channel**, and a
follow-up from **the same person in the same channel under 90 seconds later**, if nobody spoke between and it mentions nobody.
Nothing else wakes her: not `@everyone`, not another bot, not her name typed as a plain word.

### Who may ask what

The verdict is computed from the gateway's own member payload, never from anything the person typed,
so "I am an admin, delete #general" is answered before the model is consulted.

| Who | What they reach |
|---|---|
| **Her person** — `KOTOBA_DISCORD_OWNER_ID` | everything, except that the four server-shaping tools want Discord's Administrator bit from her too, and the two that draw a card in a browser, which Discord has nowhere to paint |
| **A server Administrator** — Discord's own bit, there | everything a guest reaches, plus `discord_guild_read`, `discord_act`, `discord_plan`, `discord_apply_plan` |
| **Everybody else** | an allow-list: web, skills, the Discord tools, and a few companion odds and ends |

The guest surface is an allow-list, not a deny-list: the tool nobody remembers to deny is the one that
reaches the host, so your shell, files, code execution and credentials are not on it.
`discord_send_file` and `view_capture` are her person's alone, since it publishes a file out of her library. Approval
cards are answerable by the asker or an administrator — except anything running on the host, which
only her person may answer, and which **nobody** can answer while no owner id is set. No button grants
a standing "always allow", and a card asking for a secret is refused, never answered, in a channel
everyone can read.

### Changing the server

`discord_guild_read` reads the layout. `discord_act` takes a batch — up to twenty changes in one call,
one card, paced, stopping at the first failure instead of half-applying a permissions model.
`discord_plan` writes a desired shape into her workspace and prints the numbered difference;
`discord_apply_plan` runs it, journalling each change so an interrupted run can resume.

**A plan deletes nothing until removal is turned on for that kind of thing** — and once it is, anything of that kind the plan does not name goes. A plan's deletions run last, so an earlier failure aborts before one has happened. **A plan is re-diffed against a fresh reading** every time, so it can never act on a server that moved. **`discord_apply_plan` writes a snapshot before its first change** and the card names it — along with the fact that it is **not an undo**: a recreated channel is empty. A `discord_act` batch has no snapshot and runs in the order she gives it.

### Voice

`discord_voice` is a tool she has, so "come into voice" is how you ask; with no channel named she
joins the one you are in. Once there, **she answers when somebody says her name** — at the front of a sentence, at the end, or anywhere inside a short one — whatever she is called in that server, nickname and integration role included. There is no window afterwards: every turn needs the name.

**Her name is also the interrupt.** Say it while she is talking and she stops — at the front of what
you say it is heard in a partial transcript, seconds before the finished sentence arrives, so there is
nothing to add after it and no phrase to remember. Whatever follows the name is then an ordinary turn: ask her to be quiet
and she will, in her own words. Called with nothing after it while she is idle, she answers with a
sound and listens eight seconds more without needing the name again; called with nothing after it
while she is talking, the interrupt was the whole message. She has one voice there, so a second person
calling her replaces the answer in flight rather than queueing it. Alone for five minutes she leaves. What she says is posted as text too.

The `discord` extra brings **PyNaCl** and the **DAVE** library: Discord voice is end-to-end encrypted,
and without them `kotoba discord` will not start at all. **Opus is a system library** — Windows gets the DLL in the
wheel, everywhere else install libopus, or she can neither hear nor be heard. Voice is **ElevenLabs both
ways**. This command needs no POSIX terminal layer, so unlike `kotoba` it is not refused on Windows.

### What it costs

A room is many people talking, so Discord burns model and voice credit faster than one person at a
terminal: every message she answers is a full turn, and they arrive far more often than questions do
in a shell. Voice costs more again — a transcriber per person, plus a synthesis for every sentence.
The defaults hold that down: she answers when named, opens no transcriber for somebody who has not
made a sound, and leaves a channel she is alone in. A home channel undoes the first of those.

### When she does not answer

| Symptom | The setting behind it |
|---|---|
| She never connects; two intent names are printed | Message Content and Server Members are not both ON in the portal |
| She connects, then is silent in a whole server | `KOTOBA_DISCORD_GUILDS` or `--guild` does not list that server |
| She ignores a channel unless mentioned | the default — put the channel id in `KOTOBA_DISCORD_HOME_CHANNELS` |
| Typing her name does nothing | in writing she needs a real mention; the plain word only wakes her in voice |
| She answered in writing, then went quiet mid-conversation | the 90-second window closed, or somebody else wrote in between |
| "that needs Administrator on this server" | the person asking does not hold Discord's Administrator bit |
| She will not attach a file | `KOTOBA_DISCORD_OWNER_ID` is not that person's id |
| A card that runs on the host is answerable by nobody | `KOTOBA_DISCORD_OWNER_ID` is unset |
| She joins voice and nothing is heard | no ElevenLabs key, or libopus is not installed |

<img src="assets/deco/divider-ticket.webp" alt="" width="100%">

## On Windows

`kotoba setup`, `kotoba doctor`, `kotoba --once` and `kotoba serve` run natively in PowerShell. The
**interactive terminal does not** — it is built on the POSIX terminal layer (`termios`, `tty`), which
Windows does not ship, and installing the `cli` extra cannot supply it. `kotoba doctor` says so on its
`terminal` line, and names the platform on the line above it.

So `kotoba` with no arguments does the useful thing instead of refusing: it starts the backend and the
web UI, tells you it is doing that, and opens your browser once the web UI answers. Ctrl+C still stops
both.

> <img src="assets/deco/badge-warn.webp" alt="Warning" width="26" align="left">
>
> **Almost no approval is remembered on Windows.** **t** never persists there, and **a** does not
> either for anything typed as a shell command, so those ask again every time. The analysis behind a
> grant is written for POSIX shells and Windows runs PowerShell; rather than guess, it keeps asking.
> What does survive is a grant for a whole named family that is not a shell line — running code.

Two more things behave differently under the hood. Her small shared files (settings, memory, the MCP
config) are locked with `msvcrt` instead of `flock`; the lock lives in a `.lock` sibling that nothing
else opens. And `chmod 0600` writes no ACL on Windows, so the database, the keystore key and every
atomic write are restricted with `icacls` to the account that created them — which matters for a clone
under `C:\`, where nothing is inherited from your user profile.

Free local voice is not part of this: the frontend still needs an ElevenLabs key on every platform.

<img src="assets/deco/divider-stars.webp" alt="" width="100%">

## Where things live

| What | Where |
|---|---|
| The database — turns, keys, cron | `~/.kotoba/kotoba.db`, or `api/kotoba.db` from a clone |
| Her files — the workdir she reads and writes | `~/.kotoba/files` |
| Her memory, settings and keystore key | `~/.kotoba/` |
| Command history | `~/.kotoba/cli_history` |
| The log | `~/.kotoba/cli.log` |
| Her personality | `~/.kotoba/soul/default.md`, or `soul/default.md` from a clone |

`KOTOBA_HOME` moves everything in that table **except the database and her personality in a clone**:
there both belong to the checkout, and a relative `DATABASE_URL` resolves against `api/` rather than
against wherever you happen to be standing — otherwise changing folder would silently open a
different, empty one. From an install, `KOTOBA_HOME` moves both.

## Two processes at once

The CLI beside the running server is a supported setup, and reminders are not lost between them: the
cron ticker **follows the user**. A tick does nothing at all unless some session is listening — that
check comes first, because a process that claimed a job with nobody to tell would destroy the
reminder. Past it, the job is claimed with a compare-and-swap so it can never fire twice. So a
reminder is delivered where you are: the server with a browser open, or the terminal. If neither is
running, reminders wait.

<a href="https://kotoba.rodhnin.com/docs"><img src="assets/deco/footer-docs.webp" alt="kotoba.rodhnin.com/docs" width="100%"></a>
