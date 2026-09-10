"""The wheel must carry the data files a running install reads from disk.

Building the wheel found 150 files, all `.py` — no soul, no report template, no skills, no face art.
Package discovery cannot see files outside the package by construction, so the runtime data now lives
in TWO places: the repo-root originals a clone edits, and byte-identical copies the package-data config
ships. These tests are the drift lock: the copies must match the originals, the package-data patterns
must cover every copy with glob semantics rather than fnmatch (fnmatch lets `*` cross a slash and would
approve a pattern the build ignores), and each resolver must fall back to the packaged copy when the
repo files are gone, exactly what a wheel install looks like.
"""
from __future__ import annotations

import glob
import tomllib
from pathlib import Path

import pytest

from kotoba import paths
from kotoba.cli.render import art
from kotoba.cli.render.kaomoji import EMOTIONS
from kotoba.core import skill_docs
from kotoba.paths import DATA_DIR, PACKAGE_DIR, REPO_ROOT
from kotoba.soul import loader
from kotoba.tools.action import make_report

API_ROOT = PACKAGE_DIR.parents[1]


def _data_files() -> list[Path]:
    return sorted(p for p in DATA_DIR.rglob("*") if p.is_file())


def _original_of(copy: Path) -> Path:
    rel = copy.relative_to(DATA_DIR)
    if rel.parts[0] == "cli":
        return REPO_ROOT / "assets" / Path(*rel.parts)
    return REPO_ROOT / Path(*rel.parts)


def test_the_data_dir_holds_everything_a_wheel_needs():
    names = {p.relative_to(DATA_DIR).as_posix() for p in _data_files()}
    assert "soul/default.md" in names, "she has no personality without it — nothing starts"
    assert "soul/report_template.html" in names and "soul/report-chibi.webp" in names
    assert any(n.startswith("soul/skills/") and n.endswith(".md") for n in names)
    for emotion in EMOTIONS:
        assert f"cli/faces/{emotion}.png" in names, f"the {emotion} face would fall to the kaomoji"
        assert f"cli/faces/{emotion}-outlined.png" in names, f"no light-terminal twin for {emotion}"


@pytest.mark.skipif(paths._CLONE is None, reason="only a clone has the originals to compare against")
def test_every_packaged_copy_is_byte_identical_to_its_repo_original():
    for copy in _data_files():
        original = _original_of(copy)
        assert original.is_file(), f"{copy} has no repo original at {original}"
        assert copy.read_bytes() == original.read_bytes(), (
            f"{copy} drifted from {original} — edit the repo file and re-copy it into the package"
        )


def test_package_data_patterns_cover_every_data_file():
    cfg = tomllib.loads((API_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = cfg["tool"]["setuptools"]["package-data"]["kotoba"]
    shipped: set[str] = set()
    for pattern in patterns:
        # Normalised: the pattern is written with `/` and glob expands with the platform separator,
        # so on Windows every entry came back half one and half the other.
        shipped.update(m.replace("\\", "/") for m in glob.glob(pattern, root_dir=PACKAGE_DIR))
    for f in _data_files():
        rel = f.relative_to(PACKAGE_DIR).as_posix()
        assert rel in shipped, f"{rel} would silently miss the wheel — add a pattern to pyproject.toml"


def test_the_soul_resolver_falls_back_to_the_packaged_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(loader, "API_DIR", tmp_path / "api")
    monkeypatch.setattr(loader, "REPO_ROOT", tmp_path / "repo")
    found = loader.resolve_soul_path(loader.DEFAULT_SOUL_PATH)
    assert found == (DATA_DIR / "soul" / "default.md").resolve()
    assert loader.load_soul_from_file(loader.DEFAULT_SOUL_PATH)["personality"]


def test_a_user_soul_file_still_wins_over_the_packaged_copy(monkeypatch, tmp_path):
    (tmp_path / "repo" / "soul").mkdir(parents=True)
    (tmp_path / "repo" / "soul" / "default.md").write_text("name: Mine\n---\n", encoding="utf-8")
    monkeypatch.setattr(loader, "API_DIR", tmp_path / "api")
    monkeypatch.setattr(loader, "REPO_ROOT", tmp_path / "repo")
    expected = (tmp_path / "repo" / "soul" / "default.md").resolve()
    assert loader.resolve_soul_path(loader.DEFAULT_SOUL_PATH) == expected


def test_a_missing_soul_names_each_candidate_once(monkeypatch, tmp_path):
    """On a wheel the api_dir and repo_root candidates collapse to the same ~/.kotoba path; the audit
    saw the error print it twice."""
    monkeypatch.setattr(loader, "API_DIR", tmp_path)
    monkeypatch.setattr(loader, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path / "no-data")
    with pytest.raises(FileNotFoundError) as e:
        loader.resolve_soul_path(loader.DEFAULT_SOUL_PATH)
    tried = str(e.value).split("tried: ")[1].split(", ")
    assert len(tried) == len(set(tried)), f"a candidate is listed twice: {tried}"


def test_skills_fall_back_to_the_packaged_copies(monkeypatch, tmp_path):
    monkeypatch.delenv("KOTOBA_SKILLS_DIR", raising=False)
    monkeypatch.setattr(skill_docs, "REPO_ROOT", tmp_path)
    assert skill_docs.skills_dir() == DATA_DIR / "soul" / "skills"
    skills = skill_docs.list_skills()
    assert skills, "a wheel install lost every skill"
    assert skill_docs.view_skill(skills[0]["name"])


def test_the_report_template_and_chibi_fall_back_to_the_packaged_copies(monkeypatch, tmp_path):
    monkeypatch.setattr(make_report, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(make_report, "_chibi_uri_cache", None)
    template = make_report._template()
    assert template and "{{TITLE}}" in template
    assert make_report._chibi_data_uri().startswith("data:image/webp;base64,")


def test_the_face_art_ships_inside_the_package(monkeypatch, tmp_path):
    monkeypatch.delenv("KOTOBA_CLI_FACES", raising=False)
    monkeypatch.setattr(art, "REPO_ROOT", tmp_path)
    assert art.faces_dir() == DATA_DIR / "cli" / "faces"
    assert art.path("neutral").is_file() and art.path("thinking", outlined=True).is_file()


def test_the_licence_copy_never_drifts_from_the_original():
    """MIT asks that the notice travel with the software, and the manifest cannot reach outside its own
    directory. The copy beside it is not kept in the repository, so it is only here after a build."""
    from kotoba.paths import PACKAGE_DIR

    api = PACKAGE_DIR.parents[1]
    repo = api.parent
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        beside, original = api / name, repo / name
        if not original.is_file():
            pytest.skip(f"no {name} to compare against — this is not a checkout")
        if not beside.is_file():
            pytest.skip(f"{name} has not been mirrored — nothing has built this tree yet")
        assert beside.read_bytes() == original.read_bytes(), f"{name} drifted from the original"


def test_a_checkout_without_the_copies_still_builds_a_licensed_wheel(tmp_path):
    """A fresh clone carries the originals and none of the copies, which is the state the build has to
    survive: without this the wheel goes out with no licence in it at all."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import _web_check

    repo, api = tmp_path, tmp_path / "api"
    api.mkdir()
    (repo / "LICENSE").write_text("MIT", encoding="utf-8")
    (repo / "THIRD_PARTY_NOTICES.md").write_text("notices", encoding="utf-8")

    _web_check.mirror_licences(api, repo)
    assert (api / "LICENSE").read_text(encoding="utf-8") == "MIT"
    assert (api / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8") == "notices"


def test_an_unpacked_sdist_has_the_copies_and_no_original(tmp_path):
    """There the copies ARE the originals, one directory up is somebody else's, and a build from an
    sdist must not read it or refuse for its absence."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import _web_check

    api = tmp_path / "kotoba-companion-1.0"
    api.mkdir()
    (api / "LICENSE").write_text("MIT", encoding="utf-8")
    (api / "THIRD_PARTY_NOTICES.md").write_text("notices", encoding="utf-8")

    _web_check.mirror_licences(api, tmp_path)
    assert (api / "LICENSE").read_text(encoding="utf-8") == "MIT"


def test_a_build_with_no_licence_anywhere_refuses(tmp_path):
    import sys

    import pytest

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import _web_check

    api = tmp_path / "api"
    api.mkdir()
    with pytest.raises(SystemExit, match="unlicensed"):
        _web_check.mirror_licences(api, tmp_path)


def test_the_frontend_and_the_package_name_the_same_version():
    """Two version strings for one release, and nothing compared them: `package.json` is written by
    hand, so the interface could name a version the wheel had never heard of."""
    import json

    from kotoba import __version__

    root = PACKAGE_DIR.parents[2]
    manifest = root / "package.json"
    if not manifest.is_file():
        pytest.skip("no package.json here — this is an installed wheel, not a checkout")
    assert json.loads(manifest.read_text(encoding="utf-8"))["version"] == __version__


def test_the_distribution_has_a_page_of_its_own():
    """Undeclared, the project page on PyPI is blank but for the one-line summary — the first thing
    anybody sees of an install they have not made yet."""
    import tomllib

    api = PACKAGE_DIR.parents[1]
    cfg = tomllib.loads((api / "pyproject.toml").read_text(encoding="utf-8"))
    named = cfg["project"].get("readme")
    assert named, "pyproject declares no readme"
    page = (api / named).read_text(encoding="utf-8")
    assert len(page.strip()) > 200, "a page that says nothing is the same blank page"
    assert "pip install" in page, "the one thing a stranger came here to find"
