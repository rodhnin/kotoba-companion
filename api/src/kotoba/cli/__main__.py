"""`kotoba` — her terminal.

load_dotenv() runs BEFORE anything imports kotoba.core: module-level constants there read os.getenv at
import time, so a value that only exists in .env would silently lose to the hardcoded default. The
one import above it reads no configuration at all, so the ordering it protects still holds.

The three `try` blocks that name `stopped_starting` are the whole of startup that nothing else answers
for: this module's imports, `_interactive`'s import of the app, and whatever `main` touches before a
command owns the terminal. Lexical, not a signal handler, which would change state for every importer.
"""
from __future__ import annotations

from kotoba.cli.interrupt import stopped_starting

try:
    import argparse
    import asyncio
    import platform
    import signal
    import sys

    from dotenv import load_dotenv

    load_dotenv()

    from kotoba.cli import host
    from kotoba.cli.session import Session, _tidy, explain_startup_failure
    from kotoba.core import logs
except KeyboardInterrupt:
    raise SystemExit(stopped_starting()) from None


# What each subcommand can actually ACT on. A flag accepted where it changes nothing is the defect
# these four already had once on `kotoba setup`, so the rest are refused by name — never swallowed.
_FLAGS = {"plain": "--plain", "ascii": "--ascii", "calm": "--calm", "no_face": "--no-face"}
_HONOURED = {"setup": ("plain", "ascii", "calm", "no_face"),
             "doctor": ("plain", "ascii"),      # it paints a status column and writes em dashes
             "serve": (),
             "discord": ()}
# Seven-bit on purpose: this line is what a terminal that asked for `--ascii` gets told, and an em
# dash in the sentence refusing that flag would be the sentence contradicting itself.
_NOTHING_TO_ACT_ON = {
    "doctor": "Nothing on that report moves, and it has no face to leave out.",
    "serve": "Almost every line it prints belongs to the two servers it watches, not to her.",
    "discord": "Everything it prints belongs to the gateway and the guild, not to her.",
}


def _render_flags(p: argparse.ArgumentParser, *, copy: bool = False) -> None:
    """The four flags that decide how she is DRAWN, offered on both sides of the subcommand.

    They are promised to work on `kotoba setup` too, and are passed after it, where argparse refused
    every one. `SUPPRESS` on the copies is what keeps `kotoba --ascii setup` working
    too: without it the subparser's own default overwrites the value the main parser already stored.

    Every subcommand gets the copy, including the two that honour none of them, so that the answer to
    a flag never depends on WHICH SIDE it was typed: `_HONOURED` decides what each one can act on and
    `_swallowed` refuses the rest by name. Argparse's own `unrecognized arguments` would refuse the
    trailing form too, but with no reason attached and a different verdict from the leading one."""
    default = argparse.SUPPRESS if copy else False
    p.add_argument("--plain", action="store_true", default=default,
                   help="no colour, whatever the terminal offers")
    p.add_argument("--ascii", action="store_true", default=default, help="no unicode either")
    p.add_argument("--calm", action="store_true", default=default,
                   help="reduced motion: nothing on screen moves")
    p.add_argument("--no-face", action="store_true", default=default,
                   help="no portrait, just her kaomoji")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="kotoba", description="Talk to Kotoba from the terminal.")
    p.add_argument("--once", metavar="TEXT", help="ask one thing, print the answer, exit")
    p.add_argument("--version", action="store_true", help="print the version and exit")
    _render_flags(p)
    p.add_argument("--sessions", action="store_true",
                   help="open on your past conversations, and read one back")
    p.add_argument("--settings", action="store_true",
                   help="open on the configuration, a section at a time")
    sub = p.add_subparsers(dest="command")
    setup = sub.add_parser("setup", help="choose a provider and hand over a key",
                           description="First run: pick a brain, give it a key, and prove the key works.")
    _render_flags(setup, copy=True)
    doctor = sub.add_parser("doctor", help="say what is missing before she can run",
                            description="Check this machine and report what stops her, in the order "
                                        "that decides it.")
    _render_flags(doctor, copy=True)
    serve = sub.add_parser("serve", help="run the backend and the web UI together",
                           description="Run the backend and the web UI on loopback until Ctrl+C stops both.")
    _render_flags(serve, copy=True)
    serve.add_argument("--port", type=int, default=8000, help="backend port (default: 8000)")
    serve.add_argument("--web-port", type=int, default=3000, help="web UI port (default: 3000)")
    # Offered on every platform, and off by default on all of them: `serve` is a server command, so it
    # stays quiet unless asked. What Windows changes is the OTHER entry point, the argumentless one.
    serve.add_argument("--open", action="store_true",
                       help="open the web UI in your browser once it answers")
    disc = sub.add_parser("discord", help="run her as a Discord bot",
                          description="Answer in the Discord servers she was invited to, until "
                                      "Ctrl+C stops her.")
    _render_flags(disc, copy=True)
    disc.add_argument("--guild", type=int, action="append",
                      help="restrict her to this guild id (repeatable)")
    disc.add_argument("--save-token", action="store_true",
                      help="store a bot token in the keystore and exit")
    return p.parse_args(argv)


def _swallowed(args: argparse.Namespace) -> list[str]:
    """The render flags this subcommand cannot act on, named the way they were typed.

    `kotoba --plain doctor` parsed the flag and then called `doctor.run()` with nothing — accepted and
    thrown away, which is the one thing none of these may be. The session itself (no subcommand) acts
    on all four, so it is never in this table."""
    honoured = _HONOURED.get(args.command or "", tuple(_FLAGS))
    return [flag for field, flag in _FLAGS.items()
            if field not in honoured and getattr(args, field, False)]


def _on_sigint(loop: asyncio.AbstractEventLoop, handler) -> bool:
    """Point Ctrl+C at `handler`, or say it could not be done.

    Only the Unix selector loop implements add_signal_handler; every other one — Windows' proactor, any
    loop not on the main thread — raises. Unguarded on the way in that is a traceback before she has
    said anything, and unguarded on the way out it skips close(), which leaves the database's
    non-daemon worker thread holding the process open with nothing left to run."""
    try:
        loop.add_signal_handler(signal.SIGINT, handler)
        return True
    except (NotImplementedError, RuntimeError, ValueError):
        return False


async def _setup(args: argparse.Namespace) -> int:
    from kotoba.cli import wizard

    try:
        session = await Session.open()
    except Exception as e:
        return explain_startup_failure(e)
    try:
        return 0 if await wizard.run(session.engine.db, _flags(args), face=not args.no_face,
                                     ascii_only=args.ascii) else 1
    finally:
        await session.close()


def _flags(args: argparse.Namespace | None):
    """What the terminal can do, as the flags asked for it. `--ascii` and `--plain` were parsed and
    then dropped on the one command a stranger runs first, so first run drew its chrome anyway.

    None on an install with no renderer: `kotoba setup` is reachable from a bare install
    and it is the command that install most needs, so the words never depend on the extra. `--ascii`
    is passed to `wizard.run` beside this, because None cannot carry it and that install is the one
    least able to draw an em dash."""
    if args is None:
        return None
    try:
        from kotoba.cli.render.caps import detect
    except ImportError:
        return None
    return detect(plain=args.plain, ascii_only=args.ascii, calm=args.calm)


def _folder(args: argparse.Namespace | None):
    """What `--once` prints text THROUGH: the ASCII fold when it was asked for, otherwise nothing.

    The fold is the table and nothing else, so `aquí` keeps its accent and only the glyphs a seven-bit
    terminal cannot draw are replaced — the same rule every other surface follows."""
    if not getattr(args, "ascii", False):
        return lambda text: text
    from kotoba.cli.render.ascii_fold import fold

    return fold


async def _once(text: str, args: argparse.Namespace | None = None) -> int:
    """Ctrl+C cancels the turn, not the process. The cancellation eats `ask`'s return value, so chunks
    are buffered as they land and the partial is printed before the cut is reported — otherwise her
    words are persisted, read back by the next turn, and never shown to the person who asked for them.

    Everything she said reaches the screen BEFORE close(), which drains the turn's memory extraction:
    bounded, but the provider sets no read timeout, so a slow one spends the whole limit. A Ctrl+C in
    that window, and an interrupted turn unasked, give the drain up; both turns are in the database.

    `--ascii` folds HER WORDS here, not only the wizard's chrome: this is the command that prints them,
    and the fold table imports without rich, which is why `--once` runs on an install that has none."""
    from kotoba.cli import wizard

    try:
        session = await Session.open()
    except Exception as e:
        return explain_startup_failure(e)
    # An unconfigured install would otherwise get her "I have no key" line and no way to fix it.
    if sys.stdin.isatty() and await wizard.needed(session.engine.db):
        await wizard.run(session.engine.db, _flags(args),
                         face=not getattr(args, "no_face", False),
                         ascii_only=bool(getattr(args, "ascii", False)))
    show = _folder(args)
    said: list[str] = []
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(session.ask(text, on_text=said.append))
    _on_sigint(loop, task.cancel)
    try:
        try:
            code, reply = 0, await task
        except asyncio.CancelledError:
            code, reply = 130, _tidy("".join(said))
            if reply:
                print(show(reply), flush=True)
            print(show("\n— interrupted —"), file=sys.stderr)
            session.abandon_extractions()
        else:
            if reply:
                print(show(reply), flush=True)
                # She spoke, and what she said may still be an apology. A caller in a script or a cron
                # cannot read the difference out of the text, so the status line carries it.
                code = 1 if session.last_turn_failed else 0
            else:
                code = 1
                print("(she had nothing to say)", file=sys.stderr)
    finally:
        _on_sigint(loop, session.abandon_extractions)
        await session.close()
    return code


def _speak_utf8() -> None:
    """Windows hands back a console encoded cp1252, and her own name does not fit in it: the first
    reply carrying kana died inside `print`, after the model had already been paid for. Redirection
    picks the same encoding, so a log file crashes the same way."""
    for stream in (sys.stdout, sys.stderr):
        if (getattr(stream, "encoding", "") or "").replace("-", "").lower() == "utf8":
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    """The last net under startup: argparse, `logs.to_file()` and the lazy import of a subcommand all
    run before anything owns the terminal, and a Ctrl+C in any of them used to escape as a traceback.

    Nothing that already answers for itself passes through here — `_interactive`, `--once` and the
    three subcommands each return 130 from their own handler first."""
    _speak_utf8()
    try:
        return _main(argv)
    except KeyboardInterrupt:
        return stopped_starting()


def _main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.version:
        from kotoba import __version__

        print(__version__)
        return 0
    refused = _swallowed(args)
    if refused:
        names = ", ".join(refused)
        verb = "does" if len(refused) == 1 else "do"
        print(f"kotoba: {names} {verb} nothing on `kotoba {args.command}`. "
              f"{_NOTHING_TO_ACT_ON[args.command]}", file=sys.stderr)
        return 2
    logs.to_file()   # her terminal is hers: nothing she did not say reaches it from here on
    # `--once` and the session each catch this; the three subcommands never did, and `setup` is the
    # one a stranger runs first — a Ctrl+C there printed forty lines of asyncio internals.
    try:
        if args.command == "setup":
            return asyncio.run(_setup(args))
        if args.command == "doctor":
            from kotoba.cli import doctor

            return doctor.run(plain=args.plain, ascii_only=args.ascii)
        if args.command == "serve":
            from kotoba.cli import serve

            return serve.run(port=args.port, web_port=args.web_port, open_browser=args.open)
        if args.command == "discord":
            from kotoba.discord import run as discord_run

            return discord_run.run(args)
    except KeyboardInterrupt:
        return 130
    if args.once:
        try:
            return asyncio.run(_once(args.once, args))
        except KeyboardInterrupt:
            return 130
    return _interactive(args)


def _interactive(args: argparse.Namespace) -> int:
    """Imported here and nowhere else: rich and prompt_toolkit are the `cli` extra, and `--once` has to
    keep working on an install that skipped it.

    `--sessions` and `--settings` open on a menu rather than on her greeting; both together is one
    screen, so the conversations win — they are the one you cannot reach with a slash command.

    Two ways this cannot open, and they get two different sentences. No POSIX terminal layer means a
    working path is one line away and is taken, so nothing apologises in front of something that works.
    An import that failed where the terminal COULD have run is the `cli` extra genuinely missing — one
    message for both told Windows to install an extra it already had."""
    if not host.terminal_ui_available():
        return _web_instead(args)
    try:
        from kotoba.cli import app
    except ImportError as missing:
        from kotoba import DIST_NAME

        print(f'{missing}.  pip install "{DIST_NAME}[cli]"  — or use --once', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return stopped_starting()
    launch = "sessions" if args.sessions else "settings" if args.settings else ""
    try:
        return asyncio.run(app.run(plain=args.plain, ascii_only=args.ascii, calm=args.calm,
                                   no_face=args.no_face, launch=launch))
    except KeyboardInterrupt:
        return 130


def _web_instead(args: argparse.Namespace) -> int:
    """Bare `kotoba` where her terminal cannot run: the backend and the web UI, and her face in a browser.

    Somebody typed the name of the program, not the name of a server, so what they get is her. The
    line before it is not decoration: a browser opening on its own is a surprise, and this is the one
    entry point whose behaviour differs by platform, so it says which way it went and how to stop it.

    Every EXPLICIT command keeps its meaning — `serve` stays quiet unless given `--open`, `--once`
    still answers in one shot. Only the argumentless form, which had nothing to fall back on, changes.
    `--sessions` and `--settings` are named rather than swallowed: they select a panel of a terminal
    that is not opening. The exit code stays the server's, because that is what is now running."""
    from kotoba.cli import serve

    missing = ", ".join(host.missing_terminal_modules())
    if not serve.has_server():
        from kotoba import DIST_NAME

        print(f"kotoba: her interactive terminal needs the POSIX terminal layer ({missing}), which "
              f"{platform.system() or 'this system'} does not have, and the backend that takes its "
              f'place here needs the server extra:  pip install "{DIST_NAME}[server]"',
              file=sys.stderr)
        return 1
    print(f"kotoba: her interactive terminal needs the POSIX terminal layer ({missing}), which "
          f"{platform.system() or 'this system'} does not have. Starting the backend and the web UI "
          "instead — your browser opens when it answers, and Ctrl+C stops both.", file=sys.stderr)
    if args.sessions or args.settings:
        asked = "--sessions" if args.sessions else "--settings"
        print(f"kotoba: {asked} opens a panel of that terminal, so it does nothing here — the web UI "
              "has its own.", file=sys.stderr)
    return serve.run(open_browser=True)


if __name__ == "__main__":
    raise SystemExit(main())
