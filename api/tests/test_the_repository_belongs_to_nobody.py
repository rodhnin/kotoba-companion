"""An open-source tree must not carry one person's identity: a repository naming its author's home
directory, mailbox or machine layout is one person's working copy with a licence attached, not a
product someone else can install. This refuses the two classes that leak in through fixtures, where a
real path gets typed once and copied forever.

It is an allowlist of what IS generic, not a blocklist of who to avoid: a blocklist would have to spell
the name it removes, and go stale the day anybody else works on the tree. Adding a placeholder here is
deliberate; adding a real home directory cannot be. The one directory this could not clean — captured
live-QA terminal output, where rewriting it would falsify the evidence — was left out of the published
tree instead. Nothing below is exempt."""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Everything that ships. `soul/` is the personality file every install reads, `assets/` carries the
# art and the note explaining how it was made, and the example env file is the one a stranger copies
# — all three were outside this guard while it was passing, which is how two real names survived in
# `assets/cli/README.md` after a sweep that reported the tree clean.
TREE = ("api/src", "api/tests", "lib", "components", "app", "scripts",
        "public/worklets", "docs", "soul", "assets", "tests")
TOP = ("README.md", "SECURITY.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md",
       "ROADMAP.md", "THIRD_PARTY_NOTICES.md", "LICENSE", "Dockerfile", "Dockerfile.web",
       "docker-compose.yml", "api/.env.example", ".env.local.example", ".gitignore",
       ".gitattributes", ".editorconfig", "api/pyproject.toml", "package.json",
       # Config that ships and was outside this guard: a build file is exactly where a real path gets
       # typed once, and the dockerignore, the proxy and the setup sandbox all name directories.
       ".dockerignore", ".nvmrc", "pytest.ini", "tsconfig.json", "next.config.ts", "proxy.ts",
       "global.d.ts", "api/requirements.txt", "try-setup.sh",
       "api/kotoba_build.py", "api/_web_check.py", "api/MANIFEST.in",
       ".github/dependabot.yml", ".github/pull_request_template.md",
       ".github/workflows/ci.yml", ".github/workflows/release.yml", ".github/workflows/codeql.yml",
       ".github/ISSUE_TEMPLATE/bug_report.yml", ".github/ISSUE_TEMPLATE/feature_request.yml",
       ".github/ISSUE_TEMPLATE/config.yml")
# Markup and config travel too, and one of them ships inside the wheel: a home path in an HTML
# template was invisible to a sweep that only read code.
SUFFIXES = {".py", ".ts", ".tsx", ".mjs", ".js", ".md", ".txt", ".sh", ".yaml", ".yml", ".json",
            ".html", ".css", ".svg", ".toml", ".cfg", ".ini"}
SKIP = ("node_modules", ".venv", "site-packages", "__pycache__", ".next", "dist")

# The packaged frontend, excluded from the SNOWFLAKE check alone: its minified constants read as ids.
# Excluded from everything it made five other guards blind over 57 files that ship inside the wheel,
# and a build-injected home path is exactly what those five exist to catch.
BUNDLE = "kotoba/web"

# `~` and `$HOME` are how a path should be written. When a literal one is unavoidable — the
# elision tests need a real absolute path to shorten — it uses one of these. `..` is the parent
# hop the trim tests sweep for, not a user at all.
PLACEHOLDER_HOMES = {"user", "you", "me", "someone", "jordan", "u", "..",
                     # the container's own service account, which is a role and not a person
                     "kotoba",
                     # the same names with a suffix: the boundary tests prove `/home/userX` is
                     # NOT inside `/home/user`, so the guard has to allow the near-miss too
                     "userX", "jordanX", "meX", "youX",
                     # the Windows spellings, which the sweep only started reading once it
                     # learned that a path can name its owner after a drive letter too
                     "name", "ada"}

# RFC 2606 reserves example.com for documentation. Anything else is somebody's real mailbox.
MAIL_DOMAINS = {"example.com"}

# Three spellings of the same mistake: Linux, macOS and Windows all name the owner in the path.
HOME = re.compile(r"(?:/home|/Users|[A-Za-z]:\\+Users)[/\\]+([A-Za-z0-9_.-]+)")
# `~/` is the other way a real directory gets typed. Only the first segment is judged, and
# only against the folders any machine has — anything else is somebody's own filing.
TILDE = re.compile(r"~/([A-Za-z0-9_.-]+)")
# Anything beginning with a dot is configuration every machine has, never somebody's filing. The
# rest is the plain-English set a test can reach for; a name outside it is one person's own folder
# until somebody says otherwise here, on purpose.
# `snap` is snapd's own directory, not anybody's filing: it is the one place a confined browser may
# write, so the PDF renderer names it and doctor explains it.
GENERIC_DIRS = {"documents", "downloads", "desktop", "pictures", "music", "videos", "projects",
                "code", "src", "tmp", "bin", "work", "build", "private", "notes", "kotoba", "snap",
                "shot.png", "in-home.txt", "ta", "report.md", "notes.txt"}
MAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")

# A decision credited to a named individual. Structure, not identity: a capitalised word followed by
# a verb of authorship is how attribution reads in English, so this catches a name without ever
# writing one down. The rule it enforces is "the fact survives, the person does not" — a measurement
# stays, the person who asked for it goes, because a reader who cannot ask that person is left with
# an argument from authority they have no way to check.
CREDITS = re.compile(
    r"\b([A-Z][a-z]{2,15})(?:'s)?\s+"
    r"(asked|chose|decided|wanted|reported|said|insisted|preferred|overruled|approved|rejected)\b"
    # `X's call` / `X's decision` — the same attribution with the verb turned into a noun. This shape
    # sat in the frozen prompt file while the rule above passed, which is the honest limit of a
    # structural guard: it catches the shapes it knows. Add one whenever a new shape gets through.
    r"|\b([A-Z][a-z]{2,15})'s\s+(call|decision|choice|idea|ask|request|preference|instruction)\b")

# Products, companies and tools do decide things, and saying so is ordinary technical prose.
NOT_PEOPLE = {
    "Next", "React", "Python", "Node", "Docker", "Live2D", "Cubism", "Pixi", "OpenAI", "Vercel",
    "Railway", "Rich", "Kotoba", "Windows", "Linux", "The", "This", "That", "It", "She", "They",
    "We", "Nobody", "Everyone", "Anyone", "Someone", "Nothing", "Both", "Each", "Neither", "One",
    "Cloudflare", "GitHub", "Chromium", "Pillow", "Sixel", "POSIX", "Whoever", "Whatever",
    # second person, and ordinary nouns that happen to start a sentence before one of the verbs
    "You", "Permission", "Nobody", "Anybody", "Everybody", "Somebody",
    # panels and surfaces of this product: "Settings said xai" is a screen reporting, not a person.
    # None of these is a given name, so allowing them costs the guard nothing.
    "Settings", "Terminal", "Files", "Report", "Chat", "Plan", "Brain", "Security", "Personality",
    "Voice", "Onboarding", "Console",
}


def _files() -> list[Path]:
    out: list[Path] = []
    for rel in TREE:
        base = ROOT / rel
        if not base.exists():
            continue
        out += [f for f in base.rglob("*")
                if f.is_file() and f.suffix in SUFFIXES
                # as_posix, not str: SKIP holds slash-shaped fragments and on Windows str() spells
                # them with backslashes, so nothing matched and the whole tree was scanned.
                and not any(s in f.as_posix() for s in SKIP)]
    out += [ROOT / name for name in TOP if (ROOT / name).exists()]
    return out


pytestmark = pytest.mark.skipif(not (ROOT / "components").exists(),
                                reason="the frontend tree lives in the repository, not the wheel")


def test_no_real_home_directory_is_written_into_the_tree():
    """A literal `/home/<name>` is the reader being told to be somebody in particular."""
    found = []
    for f in _files():
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for name in HOME.findall(line):
                if name not in PLACEHOLDER_HOMES:
                    found.append(f"{f.relative_to(ROOT)}:{i}  /home/{name}")
    assert not found, (
        "a real home directory is in the tree — use `~`, `$HOME`, or a placeholder from "
        f"PLACEHOLDER_HOMES:\n  " + "\n  ".join(found[:20]))


def test_no_real_mailbox_is_written_into_the_tree():
    """Every address in an example belongs to a domain reserved for examples."""
    found = []
    for f in _files():
        # The notices file reproduces other people's copyright lines, and some of them are written
        # with the author's own address. Scrubbing those would be defacing the attribution the file
        # exists to carry. Every other check still reads it.
        if f.name == "THIRD_PARTY_NOTICES.md":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for domain in MAIL.findall(line):
                if domain not in MAIL_DOMAINS:
                    found.append(f"{f.relative_to(ROOT)}:{i}  @{domain}")
    assert not found, (
        "an address outside the reserved documentation domain is in the tree:\n  "
        + "\n  ".join(found[:20]))


def test_no_decision_is_credited_to_a_named_person():
    """A reader who cannot ask that person is left with an argument they cannot check.

    This is the rule that the home-path and mailbox checks miss, and missing it is not theoretical:
    both of them passed on a tree that still credited a named individual twice, in the one file
    explaining how the art was made."""
    found = []
    for f in _files():
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for hit in CREDITS.finditer(line):
                # one alternative or the other matched, never both
                name = hit.group(1) or hit.group(3)
                word = hit.group(2) or hit.group(4)
                if name in NOT_PEOPLE:
                    continue
                found.append(f"{f.relative_to(ROOT)}:{i}  {name} {word}")
    assert not found, (
        "a decision is credited to a person — say what was decided and why, not who asked:\n  "
        + "\n  ".join(found[:20]))


# A Discord id carries its own creation instant in its top bits, so a real one decodes to a moment
# inside the platform's lifetime and an invented one lands decades out. That single property is the
# whole discriminator — no allow-list, and no false positives to argue about.
_SNOWFLAKE = re.compile(r"\b([1-9]\d{16,19})\b")
_EPOCH_MS = 1_420_070_400_000



def test_no_real_account_id_is_written_into_the_tree():
    """A fixture built from a live session carries the account it was taken from."""
    found = []
    for f in _files():
        if BUNDLE in f.as_posix():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for hit in _SNOWFLAKE.finditer(line):
                made = (int(hit.group(1)) >> 22) + _EPOCH_MS
                # "not in the future" is the discriminator that does not expire.
                if _EPOCH_MS < made < time.time() * 1000:
                    found.append(f"{f.relative_to(ROOT)}:{i}  {hit.group(1)}")
    assert not found, (
        "an id that decodes to a real account, server or role is in the tree — invent one above "
        "9000000000000000000 instead:\n  " + "\n  ".join(found[:20]))


# The author credit is deliberate in a few places and an accident everywhere else. The metadata is the
# reference — whatever is written there must not appear outside this set — which is a guard that never
# has to spell the name and never goes stale when somebody else takes it over. The README is on the
# list because a project may say who built it; that is a credit, not a leak.
CREDIT_ALLOWED = {"api/pyproject.toml", "LICENSE", "package.json", "README.md"}
_AUTHOR = re.compile(r'authors\s*=\s*\[\s*\{[^}]*name\s*=\s*"([^"]+)"', re.S)


def _credited_names() -> set[str]:
    """The author's name parts, from the metadata itself. Initials and short particles are dropped:
    they are ordinary words and would flag half the tree."""
    text = (ROOT / "api" / "pyproject.toml").read_text(encoding="utf-8")
    hit = _AUTHOR.search(text)
    if not hit:
        return set()
    return {w.lower() for w in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", hit.group(1)) if len(w) >= 4}


def test_the_author_is_named_in_the_credit_and_nowhere_else():
    """Three copies of a first name survived every earlier check here: two in Spanish fixture data
    (`"<name> vive en madrid"`) and one in the metadata field itself. The home, mailbox and credit
    patterns all missed them — a bare lowercase given name in a string matches none of the three.
    """
    names = _credited_names()
    assert names, "the package metadata names no author, so this guard measures nothing"
    bad = []
    for path in _files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in CREDIT_ALLOWED:
            continue
        low = path.read_text(encoding="utf-8", errors="replace").lower()
        for name in names:
            if re.search(rf"\b{re.escape(name)}\b", low):
                bad.append(f"{rel}: {name}")
    assert not bad, "the author's name is written outside the credit:\n" + "\n".join(sorted(bad))


def test_no_personal_directory_is_written_as_a_tilde_path():
    """`/home/<name>` is not the only shape a real folder takes. One personal directory sat in a
    fixture as a `~/` path and matched none of the patterns above, because they all look for a
    name and that one was a place. Naming it here would put it back."""
    bad = []
    for path in _files():
        rel = path.relative_to(ROOT).as_posix()
        for hit in TILDE.findall(path.read_text(encoding="utf-8", errors="replace")):
            if not hit.startswith(".") and hit.lower() not in GENERIC_DIRS:
                bad.append(f"{rel}: ~/{hit}")
    assert not bad, "a personal directory is written into the tree:\n" + "\n".join(sorted(set(bad)))


_TOOLING = re.compile(r"\b(claude|anthropic)\b", re.I)


def test_the_tool_that_wrote_the_code_is_not_named_in_it():
    """Whoever reads this repository is told what Kotoba does, never what typed it.

    A provider offered as a product option would be one thing; neither of these is offered, so every
    occurrence is either an attribution or a stray fixture, and both leave the reader an argument they
    cannot check."""
    found = []
    for f in _files():
        if f.name == Path(__file__).name:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in _TOOLING.finditer(text):
            found.append(f"{f.relative_to(ROOT)}: {m.group(0)}")
    assert not found, "the tooling that wrote this must not be named in it: " + "; ".join(found[:10])
