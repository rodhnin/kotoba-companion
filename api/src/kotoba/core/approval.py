"""Human-in-the-loop approval. `detect_dangerous(command)` labels destructive shell patterns;
`ApprovalGate` decides whether an action may run — allowlist → auto-safe → ask. With no asker: DENY.

Keep every pattern added here LINEAR. Nothing backtracks exponentially today, but `fork-bomb`'s two
unbounded runs are quadratic — 20 KB 0.10 s, 160 KB 5.6 s — and the gate calls detect_dangerous
several times per decision, with nothing capping a command's length on the way in.

WINDOWS: the gate stops opining about shell commands. Every automatic route (allowlist, both saved
grants, auto-safe) is closed and the card is always shown, because each reads the command line in
POSIX while PowerShell runs it; both "always" keys are withheld rather than saved into a dead rule."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional, Union

from kotoba.core import command_line

log = logging.getLogger("kotoba.approval")


def _windows_shell() -> bool:
    """Whether a shell command decided here would be interpreted by Windows. One origin, in the module
    that launches it, so the shell she is taught, the shell that runs and the shell this file parses for
    can never be three different answers again."""
    from kotoba.core.sandbox.local import shell_is_windows

    return shell_is_windows()

# Metachars that make `_first_token` stop describing what actually runs — never auto-run on the HOST.
_METACHAR_RE = re.compile(r"[;&|`\n<>${}]")


def _has_shell_metachars(command: str) -> bool:
    """Characters that make the gate's reading of a command differ from the shell's.

    Chaining/redirect/substitution metachars mean the first token no longer describes what runs
    ("echo x && curl … @~/.aws/credentials" starts with a "safe" echo).

    Braces and `$` are here because that divergence was a live jail escape. `shlex` does not
    brace-expand or expand variables; `/bin/sh -c` (bash on most non-Debian hosts) does — so
    `cat {/etc/passwd,notes.txt}` and `cat $HOME/.ssh/id_rsa` both arrived as one relative-looking
    token, resolved inside the workspace and auto-ran with no card. The rule this encodes is the
    general one: whatever the gate cannot resolve the way the shell will, it must ask instead."""
    return bool(_METACHAR_RE.search(command or ""))


def _looks_like_path(arg: str) -> bool:
    """A token that references a filesystem location (so we must check it stays in the workspace).
    Bare words like `pattern`/`hello` (grep pattern, echo hello) are NOT paths → not checked."""
    return ("/" in arg) or arg.startswith("~") or arg in (".", "..")


def _option_values(arg: str) -> list[str]:
    """The filename a `-`-prefixed argument carries INSIDE it, if any.

    Every dash-prefixed token used to be skipped before the jail was checked, so a path glued to a flag
    was invisible while the same path as a plain operand asked: `grep --file=/etc/passwd`,
    `wc --files0-from=/etc/passwd` and `cat --file=/etc/passwd` all auto-ran on the host. The shell
    opens that file exactly like an operand.

    Two shapes carry a value: `--long=VALUE` (and `-o=VALUE`), and the attached short form `-oVALUE`.
    A long option with no `=` carries nothing — its value is the NEXT argument, an operand the loop
    already checks. A short cluster (`-la`) lands here too and costs nothing: its letters are not paths."""
    if "=" in arg:
        return [arg.split("=", 1)[1]]
    if arg.startswith("--") or len(arg) <= 2:
        return []
    return [arg[2:]]


def _within_jail(root: Path, arg: str) -> bool:
    """Whether this one argument leaves the workspace. Not a path at all → nothing to check; a bare word
    naming a file that DOES exist is a read (and a symlink there can resolve outside, so `cat link` must
    not auto-run where `cat ./link` asks). Anything unresolvable is refused, never assumed."""
    if not _looks_like_path(arg):
        try:
            if not (root / arg).exists():
                return True
        except OSError:
            return False
    try:
        p = Path(arg).expanduser()
        p = (p if p.is_absolute() else root / p).resolve()
    except Exception:
        return False
    return p == root or root in p.parents

_DANGEROUS: list[tuple[str, re.Pattern[str]]] = [
    # The lookahead asks only for a recursive flag, in any spelling or bundle. Demanding -f as well
    # read `rm -r ~/` as ordinary: -f silences prompts, -r is the one that empties the tree, so the
    # pair let the whole home through and its card then offered to remember it.
    ("recursive-delete", re.compile(
        r"\brm\b(?=[^|;&\n]*\s(-[a-z]*r|--recursive\b))"
        r"|\b(rd|rmdir)\b(?=[^|;&\n]*\s/s)"
        r"|\bRemove-Item\b(?=[^|;&\n]*-Recurse)", re.I)),
    ("delete-root-or-home", re.compile(
        r"\brm\b[^\n]*\s(/|~|/\*|\$HOME)\s*$"
        r"|\b(del|erase)\b[^\n]*\s([a-z]:\\\\?\*?|%USERPROFILE%\\\\?\*?)\s*$", re.I)),
    ("privilege-escalation", re.compile(
        r"\b(sudo|su|doas)\b|\bStart-Process\b[^\n]*-Verb\s+RunAs", re.I)),
    ("pipe-to-shell", re.compile(
        r"\b(curl|wget|fetch)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|d|fi)?sh\b"
        r"|\b(iwr|irm|curl|wget|Invoke-WebRequest|Invoke-RestMethod)\b[^\n|]*\|\s*(iex|Invoke-Expression)\b", re.I)),
    ("raw-disk-write", re.compile(r"\bdd\b[^\n]*\bof=|>\s*/dev/(sd|nvme|disk|hd)", re.I)),
    ("filesystem-format", re.compile(
        r"\bmkfs(\.\w+)?\b|\bfdisk\b|\bmke2fs\b"
        r"|\bformat\s+[a-z]:|\bdiskpart\b|\bbcdedit\b|\bFormat-Volume\b", re.I)),
    ("fork-bomb", re.compile(r":\s*\(\s*\)\s*\{.*\|.*&\s*\}\s*;\s*:")),
    ("recursive-chmod-root", re.compile(r"\bchmod\b[^\n]*\s-R\b[^\n]*\s(/|~)/?(?:\s|$)", re.I)),
    ("power-control", re.compile(
        r"\b(shutdown|reboot|halt|poweroff|init\s+0)\b"
        r"|\b(Stop-Computer|Restart-Computer)\b", re.I)),
    ("history-or-key-wipe", re.compile(r">\s*~?/?\.(bash_history|ssh/|aws/)", re.I)),
    # `find` is on the auto-safe allowlist — without this a bare `find . -delete` would auto-run and wipe the workspace.
    ("destructive-find", re.compile(r"\bfind\b[^\n]*\s-(delete|exec|execdir)\b", re.I)),
    ("file-truncate-shred", re.compile(r"\b(shred|truncate)\b|\btee\b[^\n]*>\s*/", re.I)),
    # Windows has no /etc to protect and no rm to catch: the same harm wears these names there, and
    # the card could not say "destructive" about any of them.
    ("registry-delete", re.compile(r"\breg\s+delete\b|\bRemove-ItemProperty\b", re.I)),
    ("backup-wipe", re.compile(r"\b(vssadmin|wbadmin)\b[^\n]*\bdelete\b|\bcipher\b[^\n]*\s/w", re.I)),

]

# mkdir is here so "organize my files" doesn't prompt; mv/cp/rm are NOT — they can overwrite/delete.
_AUTO_SAFE_COMMANDS = frozenset({
    "ls", "pwd", "echo", "cat", "head", "tail", "wc", "grep", "rg", "find", "stat",
    "whoami", "id", "date", "uname", "true", "printf", "sort", "uniq", "cut", "tr", "mkdir",
    "base64", "xxd", "od", "strings", "hexdump", "tac", "nl",
})

# Several auto-safe commands stop being reads under a flag: `rg --pre CMD` executes, `find -fprintf`
# writes, `tail -f` never returns. The command stays auto-safe; only the flag that breaks the promise asks.
_ESCAPE_FLAGS: dict[str, frozenset[str]] = {
    "find": frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete",
                       "-fprintf", "-fprint", "-fprint0", "-fls"}),
    "rg": frozenset({"--pre", "--pre-glob", "--hostname-bin"}),
    "grep": frozenset({"-Z"}),  # --null: not an escape, but changes framing enough to be worth a look
    "sort": frozenset({"-o", "--output", "--compress-program", "--files0-from"}),
    "tail": frozenset({"-f", "-F", "--follow", "--retry"}),
    "head": frozenset({"--zero-terminated"}),
}


def _has_escape_flag(cmd: str, parts: list[str]) -> bool:
    """True when an allowlisted read command carries a flag that makes it execute, write or hang.
    Matches `--pre CMD`, `--pre=CMD`, the attached short form (`-ofile`) and short BUNDLES (`-qf`) —
    a bundle hides the escape flag anywhere, not just first (`tail -qf` follows forever exactly like
    `tail -f`), and is scanned only for real short clusters: find's flags are single-dash words
    (-exec), not letters. Long options match by PREFIX because getopt_long honours any unambiguous
    abbreviation, so an exact-match check read `--compress-prog=sh` as an ordinary argument and let
    sort run sh on the host with no card."""
    flags = _ESCAPE_FLAGS.get(cmd)
    if not flags:
        return False
    shorts = {f for f in flags if len(f) == 2 and f[0] == "-"}
    for arg in parts[1:]:
        head = arg.split("=", 1)[0]
        if head in flags:
            return True
        if len(head) > 2 and head.startswith("--") and any(f.startswith(head) for f in flags):
            return True
        if len(head) > 2 and not head.startswith("--") and head[:2] in flags:
            return True
        if len(head) > 2 and not head.startswith("--") and head[1:].isalnum():
            if any(f"-{ch}" in shorts for ch in head[1:]):
                return True
    return False


def detect_dangerous(command: str) -> Optional[str]:
    """Return a short reason label if `command` matches a destructive pattern, else None."""
    cmd = (command or "").strip()
    if not cmd:
        return None
    for label, pat in _DANGEROUS:
        if pat.search(cmd):
            return label
    return None


_DANGEROUS_PY: list[tuple[str, re.Pattern[str]]] = [
    ("fs-destroy", re.compile(
        r"\b(shutil\.rmtree|os\.(remove|unlink|rmdir)|\.unlink\(|\.rmdir\("
        r"|from\s+shutil\s+import[^\n]*\b(rmtree|move)\b"
        r"|from\s+os\s+import[^\n]*\b(remove|unlink|rmdir)\b)", re.I)),
    # `os` is matched by NAME, so `import os as o` walked past every spelling below it — and ctypes
    # reaches libc's own system() without naming a module this list knew. Rebinding either one is
    # rare enough that asking about the import itself costs nothing.
    ("shell-spawn", re.compile(
        r"\b(os\.system|os\.popen|subprocess\.|os\.exec[lv]|os\.posix_spawn|pty\.spawn"
        r"|from\s+subprocess\s+import"
        r"|import\s+subprocess\s+as\b"
        r"|import\s+os\s+as\b|\bctypes\b"
        r"|from\s+os\s+import[^\n]*\b(system|popen|exec[lv]?|posix_spawn)\b"
        r"|importlib\b)", re.I)),
    ("raw-socket", re.compile(r"\bsocket\.socket\b|\.connect\(\s*\(", re.I)),
    ("dynamic-eval", re.compile(r"(^|[^.\w])(eval|exec)\s*\(|\b__import__\s*\(", re.I)),
    # The open() mode is matched across BALANCED parens: the path arg is usually a nested call
    # (os.path.join(...)), which a plain `[^)]*` stops short of — missing even an append to ~/.bashrc.
    # Balanced (not `[^\n]*`) so it can't run past `open(`'s close and flag open("x.csv").read().split("w").
    # The mode is any short run of mode letters CONTAINING one that writes: `[wax]b?\+?` read only the
    # first letter plus b and +, so `at` and `r+` — the two that quietly grow a dotfile — were pure
    # computation under a saved grant. The lookahead keeps a filename out of it: "x.csv" is not a mode.
    ("fs-write", re.compile(
        r"\bopen\s*\((?:[^()\n]|\((?:[^()\n]|\([^()\n]*\))*\))*?(?:mode\s*=\s*)?"
        r"['\"](?=[rwaxbt+]{1,4}['\"])[rwaxbt+]*[wax+][rwaxbt+]*['\"]\s*[,)]"
        r"|\bos\.open\s*\([^)\n]*\bO_(WRONLY|RDWR|APPEND|CREAT|TRUNC)\b"
        r"|\.write_text\s*\(|\.write_bytes\s*\("
        r"|\bshutil\.(copy|copy2|copytree|move)\b|\bos\.(rename|replace|chmod|chown)\b", re.I)),
    # Fetching stays unflagged on purpose, but a GET whose URL is BUILT rather than written carries
    # whatever was concatenated into the query string — which is sending, wearing a reader's name.
    ("data-out", re.compile(
        r"\b(requests|httpx|aiohttp)\s*\.\s*(post|put|patch)\b|\bsession\.(post|put|patch)\s*\("
        r"|\b(requests|httpx|aiohttp)\s*\.\s*get\s*\(\s*(f['\"]|[^)\n]*?(\+|%\s|\.format\s*\())"
        r"|\burlopen\s*\([^)]*\bdata\s*=|\b(smtplib|ftplib|paramiko)\b", re.I)),
    # Both separators. The path is written for the machine that will RUN the code, so a POSIX-only
    # spelling read nothing on Windows — and execute_code is the one route the lockdown there leaves
    # automatic, so a saved grant ran code that opened the master key with no card.
    ("secret-read", re.compile(
        r"\.ssh\b|id_rsa|id_ed25519|[\\/]etc[\\/](shadow|passwd)|\.aws[\\/]+credentials|\.netrc"
        r"|\.kotoba[\\/]+\.keystore_key|\.env\b", re.I)),
]


def dangerous_code(code: str) -> Optional[str]:
    """Return a reason label if Python `code` does something worth confirming — prompt-worthy EVEN under
    a saved "always allow execute_code" family. That grant covers COMPUTATION; anything reaching disk or
    the network still asks, because the grant is given mid-conversation on a card showing one line and
    cannot be informed consent for arbitrary I/O. FETCHING stays unflagged (common and legit, and the
    exec env is scrubbed) — SENDING does not: scrubbing the env cannot stop code that reads a key off
    the disk and posts it. Writes are flagged too; file_write is the companion-reachable tool for that."""
    c = code or ""
    if not c.strip():
        return None
    for label, pat in _DANGEROUS_PY:
        if pat.search(c):
            return label
    return None


def _first_token(command: str) -> str:
    parts = command_line.split(command)
    return parts[0] if parts else ""


Asker = Callable[[str, str, Optional[str]], Awaitable[Union[bool, tuple]]]
# (action, risk_kind, approved, approver, detail) — the `detail` contract is documented on record().
Auditor = Callable[[str, str, bool, str, str], Awaitable[None]]
Persist = Callable[[str], Awaitable[None]]
Verdict = Callable[[str, str], None]


def command_family(action: str) -> str:
    """The unit we persist an 'always allow' against: the command's first token (e.g. `npm`, `git`,
    `curl`). Empty when it can't be parsed."""
    return _first_token(action)


_INTERPRETER_FAMILIES = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "fish", "ash", "csh", "tcsh", "busybox",
    "python", "pypy", "perl", "ruby", "node", "nodejs", "deno", "bun",
    "php", "lua", "tclsh", "rscript", "osascript", "awk", "gawk", "mawk", "nawk",
})
_EXEC_WRAPPER_FAMILIES = frozenset({
    "sudo", "doas", "su", "env", "xargs", "nohup", "time", "timeout", "ssh",
    "docker", "podman", "setsid", "stdbuf", "nice", "ionice", "chroot", "unshare",
})
_PAGER_EDITOR_FAMILIES = frozenset({
    "less", "more", "pg", "vi", "vim", "view", "vimdiff", "rvim", "rview",
    "nano", "pico", "emacs", "ed", "ex",
})
# Their whole job is to move bytes off this machine, and the file to send is an ORDINARY ARGUMENT —
# no metacharacter, nothing the jail walk can see, because `-d @/etc/passwd` is not even an absolute
# path. "Always allow curl" cannot mean "curl may read and post anything I can read", and chasing each
# tool's own file syntax is a per-tool arms race. The exact line is still grantable.
_TRANSFER_FAMILIES = frozenset({
    "curl", "wget", "scp", "sftp", "rsync", "ftp", "nc", "ncat", "netcat", "socat",
})
_NEVER_AUTO_FAMILIES = (_INTERPRETER_FAMILIES | _EXEC_WRAPPER_FAMILIES | _PAGER_EDITOR_FAMILIES
                        | _TRANSFER_FAMILIES)


def _command_token(command: str) -> str:
    """The first token reduced to the command it NAMES: path stripped, lowercased. `_base_family` also
    drops a trailing version (`python3.12` → `python`), which is right for the interpreter denylist and
    wrong here — it would turn `base64` into `base`, out of the reader set it belongs to."""
    return Path(_first_token(command)).name.lower()


def _base_family(family: str) -> str:
    """A family token reduced to the form the denylist is keyed on: path stripped, lowercased, trailing
    version dropped — `/usr/bin/python3.12` and `Rscript` become `python` and `rscript`."""
    tok = Path((family or "").strip()).name.lower()
    return tok.rstrip("0123456789.")


def is_auto_safe_family(family: Optional[str]) -> bool:
    """True when a stored family is one of the first-token commands that, on the host, auto-run only
    inside the workspace jail (`ls`, `cat`, `grep`, …). Lets a listing say a saved grant is workdir-scoped
    rather than free.

    Reads the command the token NAMES, the same question `_saved_allows` enforces on. Comparing the raw
    token described a `/bin/cat` grant — which is jailed — as one that runs without asking, i.e. broader
    than it is, to the person deciding whether to take it back."""
    return _command_token(family or "") in _AUTO_SAFE_COMMANDS


def family_never_auto_approves(family: Optional[str]) -> bool:
    """True when a stored 'always allow' for this family must NEVER auto-approve: the token does not name what runs.

    Three shapes qualify. An INTERPRETER runs an arbitrary payload as one argument or a file (`sh -c`,
    `python -c`, `awk 'PROG'`) — the payload carries no metacharacter, which is why `sh -c 'cat id_rsa'`
    auto-ran while `rm -rf` asked. An EXEC-WRAPPER runs another command named in its arguments under a
    changed identity or host (`sudo`, `env`, `xargs`, `ssh`, `docker`). A PAGER or EDITOR that spawns a
    shell (`less` `!cmd`, `vi` `:!cmd`) is the same rule in a reader's clothes: the file arg is a decoy.

    Excluded on purpose: tools whose CLI takes no arbitrary command string, and the plain dump/read
    tools, which are jailed like `cat`. `find`'s `-exec`/`-delete` is caught by detect_dangerous."""
    return _base_family(family or "") in _NEVER_AUTO_FAMILIES


def persistable(action: str, family: Optional[str] = None) -> bool:
    """Whether an 'always allow' for this action may be SAVED — the one rule behind both the gate's
    persistence and the card's `a` key.

    A dangerous command never is. Nor is a DERIVED family (the first token of a shell command) when the
    action carries metacharacters: `npm` does not name what `npm run build && ./deploy.sh` runs, and the
    saved row could never match that compound again while silently granting every simple `npm`. Nor is a
    derived interpreter or exec-wrapper family — saving `sh`/`python`/`env` saves arbitrary execution.
    Nor is a derived family on WINDOWS, where no shell command auto-approves anyway, so the row would be
    a rule the gate is guaranteed never to consult. An EXPLICIT family token (`execute_code`) stays
    persistable: it is unrelated to its action text, and is told apart by comparing it to the first token."""
    if detect_dangerous(action) is not None:
        return False
    fam = family if family is not None else command_family(action)
    if not fam:
        return False
    derived = fam == command_family(action)
    if derived and _has_shell_metachars(action):
        return False
    if derived and family_never_auto_approves(fam):
        return False
    if derived and _windows_shell():
        return False
    return True


def persistable_exact(action: str, family: Optional[str] = None) -> bool:
    """Whether THIS ONE LINE may be saved — the narrow grant, and the second rule the card's keys read.

    `persistable` governs a FAMILY: a predicate over an infinite set of future command lines summarised
    by a first token, and every rule it enforces exists because that summary is LOSSY. An exact grant
    has no summary to lose — the stored key is the command line character for character, so it matches
    exactly the text the person read on the card, and metacharacters inside it are part of that one
    string rather than an escape from a shorter one. The family guard's REASON does not reach here.

    Still vetoed: a dangerous command, always; an EXPLICIT family token (`execute_code`), whose action
    text the model respells between turns; and WINDOWS, where no stored grant is consulted at all."""
    if not (action or "").strip():
        return False
    if detect_dangerous(action) is not None:
        return False
    if _windows_shell():
        return False
    fam = family if family is not None else command_family(action)
    return bool(fam) and fam == command_family(action)


def always_note(action: str, family: Optional[str] = None) -> str:
    """One plain sentence for the card when the FAMILY grant is withheld, or "" when it is on offer.

    An absent option with no reason reads as a broken product. This is the one copy of the reasoning
    that reaches a screen, so the web card and the terminal cannot disagree about why.

    It explains only the BROAD option: when the narrow one is still available its own key says so, and
    a note reading as a dead end over a card that offers something would be its own small lie. WINDOWS
    is the one case where it IS a dead end, because both keys are withheld there. A caller passing
    `family=""` is saying this card is not a command at all, so it gets no sentence about grants."""
    if family == "":
        return ""
    danger = detect_dangerous(action)
    if danger is not None:
        return f"It's a {danger.replace('-', ' ')}, so it asks every time — this one can't be saved."
    fam = family if family is not None else command_family(action)
    if not fam:
        return "I can't read this as a single command, so there's no rule to save."
    if fam != command_family(action):
        return ""
    if _windows_shell():
        return "On Windows I can't read a command line the way the shell will, so every one of them asks."
    if family_never_auto_approves(fam):
        return f"“{fam}” only names what runs it, not what runs — saving that would allow anything."
    if _has_shell_metachars(action):
        return f"This line chains commands, so a rule for “{fam}” would never match it again."
    return ""


class ApprovalGate:
    """Decide whether a sensitive action may proceed.

    Order: allowlist → saved grant (exact line, then family) → auto-safe → ask the user → default.
    Dangerous-pattern commands are NEVER auto-safe and NEVER bypassed by a saved grant of either width.
    `host_exec` is the key safety switch: True means the `local` sandbox runs on the HOST, so every
    non-auto-safe exec is gated through the user — a benign-looking exfil (`curl …$(cat ~/.ssh/id_rsa)`)
    is not in the dangerous set. False (Docker, network-off) asks only for dangerous patterns.

    Two grants, deliberately separate keys, stores and Settings rows: `on_persist` remembers a command
    FAMILY, `on_persist_exact` ONE command line verbatim. A person revoking them must tell them apart."""

    def __init__(
        self,
        *,
        allowlist: Optional[set[str]] = None,
        ask: Optional[Asker] = None,
        default_decision: bool = False,
        audit: Optional[Auditor] = None,
        saved_commands: Optional[set[str]] = None,
        saved_exact: Optional[set[str]] = None,
        on_persist: Optional[Persist] = None,
        on_persist_exact: Optional[Persist] = None,
        on_verdict: Optional[Verdict] = None,
        host_exec: bool = False,
        workspace_root: Optional[Path] = None,
    ) -> None:
        self._allowlist: set[str] = set(allowlist or ())
        self._ask = ask
        self._default = default_decision
        self._audit = audit
        self._saved: set[str] = set(saved_commands or ())
        self._exact: set[str] = set(saved_exact or ())
        self._on_persist = on_persist
        self._on_persist_exact = on_persist_exact
        self._on_verdict = on_verdict
        self._host_exec = host_exec
        self._workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def _no_auto_shell(self, family: Optional[str]) -> bool:
        """True when this action is a SHELL command line on a Windows host — the case where no automatic
        route may be taken at all (see the module docstring).

        `family is None` is what says "shell": a caller that names its own family (`execute_code`) is
        handing over something that was never read as a command line, so none of the POSIX reading that
        breaks here was ever applied to it. `_host_exec` keeps the rule keyed on the gate's own switch,
        so a gate told its commands do not run on the host is never locked by the host's platform."""
        return family is None and self._host_exec and _windows_shell()

    def allowlisted(self, action: str) -> bool:
        return action in self._allowlist

    def is_saved(self, family: str) -> bool:
        return bool(family) and family in self._saved

    def is_saved_exact(self, action: str) -> bool:
        return bool(action) and action in self._exact

    def _exact_allows(self, action: str, family: Optional[str]) -> bool:
        """Whether a stored EXACT grant covers this action: byte-for-byte equality on the command line and
        nothing else — no prefix, no glob, no whitespace or case folding.

        Every normalisation would build an equivalence class, and an equivalence class is a pattern, which
        is the lossy thing the family rules already guard. `echo  a` and `echo a` are two grants here, and
        that is the point: the person granted the line they read. An explicit family token is refused for
        the reason `persistable_exact` gives, so the two sides can never disagree about what was savable —
        and a Windows host is refused for the reason it gives too, so a row saved before the platform
        question existed cannot outlive the answer."""
        if not self._exact or self._no_auto_shell(family):
            return False
        fam = family if family is not None else command_family(action)
        if fam != command_family(action):
            return False
        return self.is_saved_exact(action)

    def _saved_allows(self, action: str, family: Optional[str]) -> str:
        """WHICH stored grant auto-approves THIS action — "saved" (family), "saved-exact" (this line) or
        "". The ONE predicate behind confirm() and would_auto_allow(), so the two can never drift.

        A FAMILY grant is honoured only while it still names what runs. For a DERIVED family the action
        must carry no shell metacharacter, its family must not be an interpreter or exec-wrapper, and a
        read command must still clear the jail — a saved `ls`/`cat` cannot read outside the workspace.
        An EXPLICIT family token is honoured on the grant alone; force_ask stops its risky snippets.

        An EXACT grant faces none of those three, because each re-binds a key that stands for more
        commands than the person saw; an exact key stands for the one they read. Neither survives Windows."""
        if self._no_auto_shell(family):
            return ""
        if self._exact_allows(action, family):
            return "saved-exact"
        fam = family if family is not None else command_family(action)
        if not self.is_saved(fam):
            return ""
        if family is None:
            if _has_shell_metachars(action):
                return ""
            if family_never_auto_approves(fam):
                return ""
            if (self._host_exec and _command_token(action) in _AUTO_SAFE_COMMANDS
                    and not self._safe_host_read(action)):
                return ""
        return "saved"

    def _safe_host_read(self, action: str) -> bool:
        """On the HOST, a first-token-allowlisted command is auto-safe only if it cannot smuggle a
        chained command (no shell metacharacters) AND every path argument stays inside the workspace
        jail; `cat ~/.ssh/id_rsa`, `ls /etc` and `echo x && curl …` fall through. No root, no auto-run.

        An OPTION faces the jail like an operand: the file it names is one the shell will open, and
        skipping it let `grep --file=/etc/passwd` through. Everything a command line puts in front of
        the gate is a flag carrying no file, a path, or a bare word — only the middle one can escape.

        On WINDOWS it answers no to everything: POSIX `shlex` eats the backslashes, so
        `C:\\Users\\me\\.ssh\\id_rsa` arrives as one separator-free token and reads as in-jail."""
        if _windows_shell():
            return False
        if _has_shell_metachars(action):
            return False
        if self._workspace_root is None:
            return False
        parts = command_line.split(action)
        if not parts:
            return False
        if parts and _has_escape_flag(_command_token(action), parts):
            return False
        root = self._workspace_root
        for arg in parts[1:]:
            for candidate in (_option_values(arg) if arg.startswith("-") else [arg]):
                if not _within_jail(root, candidate):
                    return False
        return True

    def auto_safe(self, action: str, risk_kind: str) -> bool:
        if detect_dangerous(action):
            return False
        if risk_kind == "read":
            return True
        if _first_token(action) in _AUTO_SAFE_COMMANDS:
            if not self._host_exec or self._safe_host_read(action):
                return True
        if risk_kind == "exec" and not self._host_exec:
            return True
        return False

    def would_auto_allow(self, action: str, risk_kind: str = "exec", family: Optional[str] = None,
                         force_ask: bool = False) -> bool:
        """True if confirm() would allow this WITHOUT prompting the user (allowlist / a saved grant /
        auto-safe). Lets a caller decide up front whether it must ask — so a live VOICE turn can
        DEFER an ask-needed command instead of blocking on the approval card.
        `force_ask` (caller detected danger the gate can't see, e.g. destructive Python) → never auto-allow.
        A shell command on a Windows host is the same answer arriving from the platform: nothing about it
        is auto-allowed, so a voice turn defers it to a card exactly as it would a dangerous one."""
        if force_ask or detect_dangerous(action) is not None or self._no_auto_shell(family):
            return False
        if self.allowlisted(action):
            return True
        if self._saved_allows(action, family):
            return True
        return self.auto_safe(action, risk_kind)

    async def persist_always(self, action: str, family: Optional[str] = None) -> None:
        """Remember this command family as 'always allow' (used when a DEFERRED approval was granted with
        'always'). Never persists a dangerous command, nor a derived family off a compound whose first
        token does not name what runs (see `persistable`)."""
        fam = family if family is not None else command_family(action)
        if persistable(action, family):
            self._saved.add(fam)
            if self._on_persist is not None:
                try:
                    await self._on_persist(fam)
                except Exception:
                    log.warning("persist_always failed for %r", fam, exc_info=True)

    async def persist_exact(self, action: str, family: Optional[str] = None) -> None:
        """Remember THIS ONE LINE as 'always allow' — the deferred path's twin of persist_always, and it
        refuses whatever `persistable_exact` refuses, so a card and a held card grant the same thing."""
        if not persistable_exact(action, family):
            return
        self._exact.add(action)
        if self._on_persist_exact is not None:
            try:
                await self._on_persist_exact(action)
            except Exception:
                log.warning("persist_exact failed for %r", action, exc_info=True)

    async def confirm(self, action: str, risk_kind: str = "exec", family: Optional[str] = None,
                     force_ask: bool = False) -> bool:
        """Return True if the action may run, auditing the decision and who made it. `family` overrides
        the persisted key; `force_ask` skips allowlist, saved grants and auto-safe and always prompts.

        The asker's answer may be a bare yes, a yes + "always allow this family", or a yes + "always
        allow this exact line" — never both; a reply claiming both is honoured as the family one. Each
        is persisted only if its own rule allows it, so an asker cannot talk the gate into a grant no
        card could offer. When the asker RAISES, the action is denied but NOT audited as the user's.

        A card that EXPIRES is the same lie from the other side — a denial and a timeout both arrive as
        False, so an unanswered `df -h` was written down as `approved=0, approver="user"`."""
        fam = family if family is not None else command_family(action)
        dangerous = detect_dangerous(action) is not None
        always = always_exact = False
        # One flag for "no automatic route may be taken" — every branch below is closed by it.
        no_auto = force_ask or self._no_auto_shell(family)
        saved_by = "" if (no_auto or dangerous) else self._saved_allows(action, family)
        if not no_auto and self.allowlisted(action):
            ok, who = True, "allowlist"
        elif saved_by:
            ok, who = True, saved_by
        elif not no_auto and self.auto_safe(action, risk_kind):
            ok, who = True, "auto-safe"
        elif self._ask is not None:
            who = "user"
            try:
                res = await self._ask(action, risk_kind, fam)
            except Exception:
                log.warning("approval asker failed for %r — denying", action, exc_info=True)
                res, who = False, "error"
            if isinstance(res, tuple):
                ok, always = bool(res[0]), bool(res[1])
                always_exact = bool(res[2]) if len(res) > 2 else False
                if len(res) > 3:
                    from kotoba.core.interaction import approver_for

                    who = approver_for(str(res[3]))
            else:
                ok = bool(res)
        else:
            ok, who = self._default, "default"

        if ok and always and self._on_persist is not None and persistable(action, family):
            self._saved.add(fam)
            try:
                await self._on_persist(fam)
            except Exception:
                log.warning("persisting approval for %r failed", fam, exc_info=True)
        elif ok and always_exact:
            await self.persist_exact(action, family)

        if self._on_verdict is not None:
            try:
                self._on_verdict(fam, who)
            except Exception:
                log.warning("verdict hook failed for %r", fam, exc_info=True)
        await self.record(action, risk_kind, ok, who, "decision")
        return ok

    async def record(self, action: str, risk_kind: str, approved: bool, approver: str,
                     detail: str) -> None:
        """Write one audit row. `detail` says WHICH event this is — "decision" (someone said yes/no) or
        "executed"/"executed:failed" (it actually ran). Public so the DEFERRED path, which asks the user
        directly instead of through confirm(), still records the human's answer and the run it authorised.
        `detail` is required: omitting it would silently create a legacy-looking NULL row."""
        if self._audit is None:
            return
        try:
            await self._audit(action, risk_kind, approved, approver, detail)
        except Exception:
            log.warning("approval audit failed for %r", action, exc_info=True)
