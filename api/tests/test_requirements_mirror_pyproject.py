"""One source of truth for dependencies, and a mirror that cannot drift away from it.

`requirements.txt` and `pyproject.toml` both listed version floors, and they had already disagreed: the
flat file carried a floor the project file never pinned. Two files answering the same question
differently is not a redundancy, it is a coin toss — whichever one a reader opens is the answer they
get, and only one is what an editable install actually honours. So `pyproject.toml` is authoritative and
`requirements.txt` mirrors the backend set: core dependencies plus the server, voice, mcp and web
extras; cli and dev stay outside it, since the flat file is for a container or scanner, not a
contributor's install. This compares the two as SETS OF REQUIREMENTS, never as text — pinning the file
byte-for-byte would fail on a reflowed comment while still passing on a floor that moved."""
from __future__ import annotations

import tomllib

import pytest

from kotoba import paths

pytestmark = pytest.mark.skipif(
    paths._CLONE is None, reason="both manifests live in the repository, not the wheel")

API = paths.REPO_ROOT / "api"
MIRRORED_EXTRAS = ("server", "voice", "mcp", "web")


def _pyproject() -> dict:
    return tomllib.loads((API / "pyproject.toml").read_text(encoding="utf-8"))


def _norm(spec: str) -> str:
    """A requirement without its whitespace. `uvicorn[standard]>=0.32` keeps its extra: an extra is
    part of what gets installed, so dropping it here would let the two files disagree about it."""
    return "".join(spec.split()).lower()


def _flat() -> set[str]:
    out = set()
    for line in (API / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.add(_norm(line))
    return out


def _declared() -> set[str]:
    proj = _pyproject()["project"]
    out = {_norm(d) for d in proj["dependencies"]}
    for extra in MIRRORED_EXTRAS:
        out |= {_norm(d) for d in proj["optional-dependencies"][extra]}
    return out


def test_the_mirror_lists_exactly_what_the_project_file_declares():
    """Every floor in one file is in the other, with the same operator and the same number. A missing
    entry is a dependency the flat install silently lacks; an extra one is a floor nobody installing
    the package would ever get."""
    flat, declared = _flat(), _declared()
    assert flat == declared, (
        "requirements.txt has drifted from pyproject.toml — pyproject is authoritative:\n"
        f"  only in requirements.txt: {sorted(flat - declared)}\n"
        f"  only in pyproject.toml:   {sorted(declared - flat)}")


def test_the_mirror_says_which_file_wins():
    """A reader who opens the mirror first has to be told, in the file, that it is not the answer —
    otherwise the drift comes back the next time someone bumps a floor in the file they happened to
    open."""
    head = (API / "requirements.txt").read_text(encoding="utf-8")[:600].lower()
    assert "pyproject.toml" in head, "the mirror never names the file that outranks it"
    assert "not the source of truth" in head or "authoritative" in head


def test_the_mirror_is_the_backend_and_not_the_whole_package():
    """`cli`, `discord` and `dev` stay out on purpose: the mirror is what the backend image installs,
    and the bot is its own process rather than part of that server. If a future extra is added to
    pyproject and quietly joins the mirrored set, this is the line that has to be changed
    deliberately rather than the mirror growing by accident."""
    extras = set(_pyproject()["project"]["optional-dependencies"])
    assert extras == set(MIRRORED_EXTRAS) | {"cli", "discord", "dev"}, (
        f"the extras changed; decide whether the new one belongs in the mirror: {sorted(extras)}")
