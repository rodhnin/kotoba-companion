"""The packaged web UI has to be there, current, and free of the builder's own address.

A wheel that ships an EMPTY or STALE `web/` is worse than one that ships none: the static layer denies
by default, so the person gets a blank page and no reason for it. And a build picks up `NEXT_PUBLIC_*`
from whatever environment or `.env.local` it finds, freezing one machine's address into chunks that
then reach everyone — invisible once minified, because it looks like any other string.

Skipped where no build exists, so a contributor's clone stays usable. The release job sets
KOTOBA_REQUIRE_WEB=1, and there the skip becomes a failure: "nobody built it" is the whole point.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tomllib
import types
from pathlib import Path

import pytest
from conftest import make_symlink

from kotoba import __version__
from kotoba.core import frontend
from kotoba.paths import PACKAGE_DIR, _CLONE

WEB = PACKAGE_DIR / "web"
STAMP = WEB / "kotoba-build.json"
REQUIRED = ("app.html", "login.html", "setup.html", "404.html")
REPO = Path(__file__).resolve().parents[2]

#: Only the tests that READ a packaged build wait for one. The checker's own tests need none, and
#: skipping them by module is how they stopped running on every clone — where the checker is edited.
_required = os.getenv("KOTOBA_REQUIRE_WEB") == "1"
needs_build = pytest.mark.skipif(
    not _required and not WEB.is_dir(),
    reason="no packaged web build here; the release job sets KOTOBA_REQUIRE_WEB=1")
in_a_clone = pytest.mark.skipif(_CLONE is None, reason="the sources are not shipped")


def _checker():
    """The verification alone, which is why it does not live inside the setuptools wrapper."""
    spec = importlib.util.spec_from_file_location("_web_check", REPO / "api" / "_web_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest() -> tuple[str, int]:
    """The checker's own, never a second one: a nested `kotoba-build.json` made two of them disagree
    about the file count, and the disagreement is invisible until a release."""
    return _checker().digest(WEB)


@needs_build
def test_the_pages_and_a_chunk_are_there():
    assert WEB.is_dir(), "nothing built the web UI — run scripts/build_web.py"
    for name in REQUIRED:
        assert (WEB / name).is_file(), f"{name} is missing from the packaged build"
    assert any((WEB / "_next" / "static").rglob("*.js")), "the build carries no javascript"


@needs_build
def test_the_stamp_matches_what_is_on_disk():
    """A file changed by hand after the build is a build nobody can reproduce."""
    stamp = json.loads(STAMP.read_text(encoding="utf-8"))
    digest, count = _digest()
    assert stamp["version"] == __version__, "the build was stamped for another version of kotoba"
    assert (stamp["web_sha256"], stamp["files"]) == (digest, count)


@in_a_clone
def test_both_digests_order_by_the_path_text_not_by_the_platform(tmp_path):
    """A wheel is stamped on one machine and verified on another, so the order both digests hash in
    must not be the platform's.

    `sorted()` over Path objects compares LISTS OF PARTS, and under Windows each part is case-folded.
    Two names are enough to show both halves without a Windows box: `a.txt` beside `a/b.txt` swap
    places by parts alone, right here, and `UIOverlay.tsx` beside `icons.tsx` swap under the fold.
    Written first against `UIOverlay/icons` only, this test passed against the bug on every Linux run —
    those two happen to agree by parts, and the half that could see it ended in `or os.name != "nt"`."""
    spec = importlib.util.spec_from_file_location("build_web", REPO / "scripts" / "build_web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    names = ["a.txt", "a/b.txt", "UIOverlay.tsx", "icons.tsx"]
    for name in names:
        made = tmp_path / name
        made.parent.mkdir(parents=True, exist_ok=True)
        made.write_text(name, encoding="utf-8")
    files = [tmp_path / n for n in names]
    wanted = sorted(names)

    assert [f.relative_to(tmp_path).as_posix() for f in module.in_order(files, tmp_path)] == wanted
    assert [f.relative_to(tmp_path).as_posix() for f in sorted(files)] != wanted, (
        "the names chosen no longer tell the two orders apart, so this test proves nothing")


@in_a_clone
def test_the_stamp_and_the_checker_hash_in_the_same_order(tmp_path):
    """Two digests, written apart, and only one of them was covered. The stamp is produced by
    `build_web` and verified by `_web_check` inside the wheel: if they ever order differently, every
    install reports itself hand-edited and nothing here would have said so."""
    spec = importlib.util.spec_from_file_location("build_web", REPO / "scripts" / "build_web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for name in ("a.txt", "a/b.txt", "UIOverlay.tsx", "icons.tsx", "kotoba-build.json"):
        made = tmp_path / name
        made.parent.mkdir(parents=True, exist_ok=True)
        made.write_text(name, encoding="utf-8")

    assert module.web_digest(tmp_path) == _checker().digest(tmp_path)


@needs_build
@in_a_clone
def test_the_build_is_not_older_than_the_sources():
    spec = importlib.util.spec_from_file_location("build_web", REPO / "scripts" / "build_web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stamp = json.loads(STAMP.read_text(encoding="utf-8"))
    assert stamp["sources_sha256"] == module.sources_digest(), \
        "the frontend changed since this build — run scripts/build_web.py again"


@needs_build
@in_a_clone
def test_nothing_of_the_builder_travels_in_what_shipped():
    """The nets, run against what actually shipped rather than the export it came from — and over the
    WHOLE tree: scanning only the chunks left 48 of the 70 files unread, and metadata is text too."""
    spec = importlib.util.spec_from_file_location("build_web", REPO / "scripts" / "build_web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not module.strangers(WEB), "an address that can only be one machine's shipped"
    assert not module.fingerprints(WEB), "something identifying the build itself shipped"


@needs_build
def test_no_third_party_model_travels():
    """`next build` copies the whole of public/, and a Live2D model there belongs to somebody else."""
    assert not (WEB / "models").exists(), "a model directory is inside the wheel"


@needs_build
def test_setuptools_can_actually_see_every_file():
    """`glob` skips dotfiles, so one leading dot silently drops a file from the wheel."""
    hidden = [str(p.relative_to(WEB)) for p in WEB.rglob("*") if p.name.startswith(".")]
    assert not hidden, f"these would be dropped by the package-data glob: {hidden}"


@in_a_clone
def test_the_package_declares_it():
    data = tomllib.loads((REPO / "api" / "pyproject.toml").read_text(encoding="utf-8"))
    globs = data["tool"]["setuptools"]["package-data"]["kotoba"]
    assert "web/**/*" in globs, "the build is on disk and the wheel would leave it behind"
    assert data["build-system"]["build-backend"] == "kotoba_build", \
        "without the wrapper nothing stops an empty or stale build from being packaged"


def test_a_directory_with_no_app_html_is_not_a_root(tmp_path, monkeypatch):
    """The blank-page case: an empty directory used to become the root and then deny every path."""
    monkeypatch.setattr(frontend, "_ROOT", frontend._UNSET)
    monkeypatch.setenv("KOTOBA_FRONTEND_DIR", str(tmp_path))
    assert frontend.root() is None
    monkeypatch.setattr(frontend, "_ROOT", frontend._UNSET)
    (tmp_path / "app.html").write_text("<!doctype html>", encoding="utf-8")
    assert frontend.root() == tmp_path


def _plant(web: Path, module, *, version="9.9.9") -> None:
    (web / "_next" / "static" / "chunks").mkdir(parents=True, exist_ok=True)
    for name in REQUIRED:
        (web / name).write_text("<!doctype html>", encoding="utf-8")
    (web / "_next" / "static" / "chunks" / "x.js").write_text("var a=1;", encoding="utf-8")
    (web / "kotoba-build.json").write_text("{}", encoding="utf-8")
    made, count = module.digest(web)
    (web / "kotoba-build.json").write_text(
        json.dumps({"version": version, "files": count, "web_sha256": made}), encoding="utf-8")


@in_a_clone
def test_a_wheel_with_no_web_is_refused(tmp_path):
    module = _checker()
    with pytest.raises(SystemExit) as stop:
        module.require("wheel", tmp_path / "web", "9.9.9")
    assert "build it first" in str(stop.value)


@in_a_clone
def test_a_wheel_whose_build_was_touched_is_refused(tmp_path):
    """One byte edited after the build, which is how a build nobody can reproduce ships."""
    module = _checker()
    web = tmp_path / "web"
    _plant(web, module)
    module.require("wheel", web, "9.9.9")
    (web / "app.html").write_text("<!doctype html> ", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        module.require("wheel", web, "9.9.9")
    assert "no longer matches its stamp" in str(stop.value)


@in_a_clone
def test_a_build_stamped_for_another_version_is_refused(tmp_path):
    module = _checker()
    web = tmp_path / "web"
    _plant(web, module, version="0.0.1")
    with pytest.raises(SystemExit) as stop:
        module.require("wheel", web, "9.9.9")
    assert "stamped for kotoba" in str(stop.value)


@in_a_clone
def test_a_page_that_went_missing_is_refused(tmp_path):
    module = _checker()
    web = tmp_path / "web"
    _plant(web, module)
    (web / "login.html").unlink()
    assert any("login.html is missing" in p for p in module.problems(web, "9.9.9"))


@in_a_clone
def test_a_build_with_no_javascript_is_refused(tmp_path):
    """Four pages and nothing to run them is a blank screen, not a build."""
    module = _checker()
    web = tmp_path / "web"
    _plant(web, module)
    next(iter((web / "_next" / "static" / "chunks").glob("*.js"))).unlink()
    assert any("no javascript" in p for p in module.problems(web, "9.9.9"))


@in_a_clone
def test_a_file_that_arrived_after_the_build_is_refused(tmp_path):
    """The digest catches an edit; the count is what catches an addition of exactly the same size."""
    module = _checker()
    web = tmp_path / "web"
    _plant(web, module)
    (web / "extra.html").write_text("<!doctype html>", encoding="utf-8")
    assert any("no longer matches its stamp" in p for p in module.problems(web, "9.9.9"))


@in_a_clone
def test_the_digest_reads_names_and_not_only_bytes(tmp_path):
    """Two files swapped for each other leave every byte in place. A digest over content alone would
    call that the same build, and the page a browser asks for by name would be the other one."""
    module = _checker()
    web = tmp_path / "web"
    _plant(web, module)
    before = module.digest(web)
    (web / "login.html").write_text("<!doctype html>login", encoding="utf-8")
    (web / "setup.html").write_text("<!doctype html>setup", encoding="utf-8")
    swapped = module.digest(web)
    (web / "login.html").write_text("<!doctype html>setup", encoding="utf-8")
    (web / "setup.html").write_text("<!doctype html>login", encoding="utf-8")
    assert module.digest(web) != swapped and module.digest(web) != before


@in_a_clone
def test_a_build_older_than_the_sources_is_refused(tmp_path, monkeypatch):
    """The half of stale that actually ships: the build is intact, and the frontend moved on."""
    module = _checker()
    web = tmp_path / "web"
    _plant(web, module)
    stamp = json.loads((web / "kotoba-build.json").read_text(encoding="utf-8"))
    stamp["sources_sha256"] = "0" * 64
    (web / "kotoba-build.json").write_text(json.dumps(stamp), encoding="utf-8")
    monkeypatch.setattr(module, "sources_digest", lambda repo: "f" * 64)
    assert any("has changed since this build" in p
               for p in module.problems(web, "9.9.9", tmp_path))


@in_a_clone
def test_a_model_reached_through_a_symlink_is_still_refused(tmp_path):
    """The refusal exists because the art belongs to somebody else and cannot be redistributed. A
    symlinked directory is not descended by a glob, so the one shape that hides a model from the
    check is the one shape a person uses to keep a heavy model outside the tree."""
    spec = importlib.util.spec_from_file_location("build_web_probe", REPO / "scripts" / "build_web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    outside = tmp_path / "elsewhere" / "free1"
    outside.mkdir(parents=True)
    (outside / "free1.moc3").write_bytes(b"")
    public = tmp_path / "public"
    public.mkdir()
    make_symlink(public / "models", outside, target_is_directory=True)

    assert [p.name for p in module._model_art(public)] == ["free1.moc3"]
    assert module._model_art(REPO / "public") == [], "the repository itself carries a model"


def _setuptools_stand_in() -> types.ModuleType:
    """setuptools is absent from a plain venv, and that skipped these probes everywhere they ran. Its
    hooks raise here, so a wrapper that delegates before checking is caught rather than delegated to."""
    def refuse(*_a, **_k):
        raise AssertionError("delegated to setuptools without verifying the web build first")

    meta = types.ModuleType("setuptools.build_meta")
    for hook in ("build_wheel", "build_sdist", "build_editable",
                 "get_requires_for_build_wheel", "get_requires_for_build_sdist",
                 "prepare_metadata_for_build_wheel"):
        setattr(meta, hook, refuse)
    stub = types.ModuleType("setuptools")
    stub.build_meta = meta
    return stub


@in_a_clone
def test_the_wrapper_is_the_backend_that_runs(tmp_path, monkeypatch):
    """Declaring it in the pyproject is not the same as it being reached: the hooks have to be the
    ones that verify, or every refusal in this file guards nothing.

    setuptools stands in for itself, because it is absent from a plain venv and this skipped
    everywhere it was ever run. What the wrapper delegates to is not the question here; whether it
    delegates before checking is, so the stand-in fails the test if it is ever reached."""
    stub = _setuptools_stand_in()
    monkeypatch.setitem(sys.modules, "setuptools", stub)
    monkeypatch.setitem(sys.modules, "setuptools.build_meta", stub.build_meta)

    # What `backend-path` gives it during a real build, and the only reason it can see its checker.
    monkeypatch.syspath_prepend(str(REPO / "api"))
    spec = importlib.util.spec_from_file_location("kotoba_build_probe", REPO / "api" / "kotoba_build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.WEB = tmp_path / "empty"
    with pytest.raises(SystemExit):
        module.build_wheel(str(tmp_path))
    with pytest.raises(SystemExit):
        module.build_sdist(str(tmp_path))


@in_a_clone
def test_the_whole_staging_directory_is_cleared_and_not_only_its_web(tmp_path, monkeypatch):
    """setuptools copies into the staging directory and never cleans it, so anything deleted from the
    sources keeps shipping. Clearing only the web subtree left every other stale file in the wheel."""
    monkeypatch.syspath_prepend(str(REPO / "api"))
    spec = importlib.util.spec_from_file_location("kotoba_build_staging", REPO / "api" / "kotoba_build.py")
    module = importlib.util.module_from_spec(spec)
    stub = _setuptools_stand_in()
    monkeypatch.setitem(sys.modules, "setuptools", stub)
    monkeypatch.setitem(sys.modules, "setuptools.build_meta", stub.build_meta)
    spec.loader.exec_module(module)

    assert module.STAGED == module.HERE / "build" / "lib", "part of the staging survives a build"

    module.STAGED = tmp_path / "lib"
    stale = module.STAGED / "kotoba" / "gone_from_the_sources.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("x", encoding="utf-8")
    module._clear_staged()
    assert not stale.exists()


@pytest.mark.parametrize("branch", ["packaged", "clone"])
def test_every_branch_wants_a_page_and_not_a_directory(tmp_path, monkeypatch, branch):
    """An empty directory used to become the root, and then the static layer denied every path."""
    from kotoba import paths

    monkeypatch.delenv("KOTOBA_FRONTEND_DIR", raising=False)
    empty = tmp_path / ("web" if branch == "packaged" else ".next-export")
    empty.mkdir()
    if branch == "packaged":
        monkeypatch.setattr(paths, "PACKAGE_DIR", tmp_path)
        monkeypatch.setattr(paths, "_CLONE", None)
    else:
        monkeypatch.setattr(paths, "PACKAGE_DIR", tmp_path / "nowhere")
        monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(paths, "_CLONE", tmp_path)
    monkeypatch.setattr(frontend, "_ROOT", frontend._UNSET)
    assert frontend._find_root() is None
    (empty / "app.html").write_text("<!doctype html>", encoding="utf-8")
    assert frontend._find_root() == empty


@needs_build
def test_no_image_carries_a_label_of_its_own():
    """A generator can sew a signed provenance block into a picture — what made it, when, and a unique
    id — and it travels with the file for ever. One shipped 29 KB of it. Every image in the build is
    checked, because a second one would be just as invisible."""
    import struct

    carriers = {b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"caBX", b"caBP", b"iCCP"}
    labelled = []
    for image in sorted(WEB.rglob("*.png")):
        raw = image.read_bytes()
        i = 8
        while i + 8 <= len(raw):
            size = struct.unpack(">I", raw[i:i + 4])[0]
            kind = raw[i + 4:i + 8]
            if kind in carriers:
                labelled.append(f"{image.relative_to(WEB)}: {kind.decode()} ({size} bytes)")
            i += 12 + size
            if kind == b"IEND":
                break
    for image in sorted(WEB.rglob("*.webp")):
        raw = image.read_bytes()
        i = 12
        while i + 8 <= len(raw):
            kind = raw[i:i + 4]
            size = struct.unpack("<I", raw[i + 4:i + 8])[0]
            if kind in (b"EXIF", b"XMP ", b"ICCP"):
                labelled.append(f"{image.relative_to(WEB)}: {kind.decode().strip()} ({size} bytes)")
            i += 8 + size + (size & 1)
    assert not labelled, "an image ships with something written inside it: " + str(labelled)
