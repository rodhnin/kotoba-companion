<a href="https://kotoba.rodhnin.com/docs/changelog"><img src="docs/assets/banners/changelog.webp" alt="Changelog" width="100%"></a>

Notable changes, newest first.

Versions start at **0.10**. A fix or a small addition moves the last number — 0.10.1, 0.10.2 — and a
release that changes how Kotoba behaves for you moves the middle one. The leading zero stays until
the shape of Kotoba stops moving; a 1 will be a promise about that, not a celebration. Anything that
changes will say so here in plain words before it says so in a number.

Read the middle number as ten, not as one: `0.1` and `0.10` are different versions and `0.10` is the
later of the two. The third number is not decoration either — Python normalises `0.010` to `0.10`, so
a version written with a stray zero publishes as a release that already exists.

Full notes: **[kotoba.rodhnin.com/docs/changelog](https://kotoba.rodhnin.com/docs/changelog)**

<img src="docs/assets/deco/divider-tape.webp" alt="" width="100%">

## Unreleased

Nothing yet.

<img src="docs/assets/deco/divider-ticket.webp" alt="" width="100%">

## 0.10.0 — the first release · 2026-09-10

The first version anybody other than its author can install.

### She runs

- **Three surfaces, one memory.** A browser app with a Live2D avatar you talk to out loud, a full
  terminal where she writes, and a Discord bot that reads and talks in the servers she was invited
  to. Same engine, same conversations, same files.
- **`pip install "kotoba-companion[server,voice,cli,web,mcp]"`** carries the web UI already compiled,
  so an install needs no Node and no build step. `kotoba serve` starts the backend and serves the app
  from it.
- **`kotoba setup`** asks six things, offers a seventh, and proves your key with a real call before storing it, so a key
  that cannot reach the model you picked fails while you can still paste another one.
- **`kotoba doctor`** checks this machine in the order that decides whether she can run, and says what
  each missing piece costs you rather than only that it is missing.

### She can do things

- Shell, code, files, web search and extraction, vision, reports, memory, cron, subagents, and any
  MCP server you connect.
- **On the default backend, every shell command and every snippet of Python stops and asks** — reads
  and `mkdir` aside. You can approve one line, one command family, or one tool, and destructive shapes
  can never be pre-approved at all. Inside a Docker container the container is the boundary instead,
  and only a shape the pre-screen calls dangerous still asks.
- Three execution backends: on the host behind the approval gate, in a locked-down Docker container,
  or not at all.

### She has a face

- A Live2D model of your own, installed onto your machine rather than shipped in this repository —
  their licences forbid redistribution. The browser's first-run screen fetches the free sample for
  you, with its licence terms shown first.
- Fourteen emotions, mapped to expressions per model, and pixel-art faces for the terminal.

### Windows

`setup`, `doctor`, `serve`, `discord` and `--once` run natively. The interactive terminal is POSIX-only for now.

<img src="docs/assets/deco/divider-stars.webp" alt="" width="100%">

### What it does not do yet

> <img src="docs/assets/deco/badge-note.webp" alt="Note" width="26" align="left">
>
> **ElevenLabs is required for the voice** — there is no local speech engine yet. There are two model
> providers, OpenAI and xAI. And the voice is chosen by pasting an ElevenLabs voice ID rather than
> picking from a list.

All three are on the [roadmap](ROADMAP.md), and [SECURITY.md](SECURITY.md) is candid about what the
approval gate does and does not protect.

<a href="https://kotoba.rodhnin.com/docs"><img src="docs/assets/deco/footer-docs.webp" alt="kotoba.rodhnin.com/docs" width="100%"></a>
