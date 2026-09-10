# kotoba-companion

An AI companion you talk to out loud. She has an anime face that lip-syncs and changes expression,
real-time two-way voice, and a full agent underneath that runs **on your machine**: she searches the
web, remembers across sessions, runs code and shell commands behind an approval gate, and manages a
file library.

A browser, a terminal and a Discord bot are three front doors onto one engine — one database, one
memory, one file library.

## Install

```
pip install "kotoba-companion[server,voice,cli,web,mcp]"
kotoba setup
kotoba serve
```

The web interface ships already built inside the package, so running it needs no Node. Leaving `web`
out does not disable reading pages — it sends the addresses she reads to a public reader service
instead of fetching them here, which is rarely what somebody self-hosting wants.

The Discord bot is one extra more, since it pulls a voice stack most people running the browser app
never load:

```
pip install "kotoba-companion[discord]"
```

`kotoba setup` asks for the keys and stores them encrypted: an OpenAI or xAI key for the brain, and
an ElevenLabs key if you want her to speak. Python 3.11 or newer.

`kotoba doctor` explains anything that is missing, in the order that decides it.

Source, issues and documentation: <https://github.com/rodhnin/kotoba-companion> · MIT licence.
