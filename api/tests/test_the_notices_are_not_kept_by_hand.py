"""A licence inventory maintained by hand is an inventory that is quietly wrong.

The hand-written half of the file — the reproduced licence texts — is deliberate and stays. The list
of packages is generated from the frontend's runtime dependency tree, and this is what stops the two
from drifting: a dependency added and never listed is the shape of the mistake.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "licenses.py"


def _generator():
    if not (ROOT / "node_modules").is_dir():
        pytest.skip("no node_modules here — nothing to inventory")
    spec = importlib.util.spec_from_file_location("licenses_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generated_block_is_current():
    _generator()
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr.strip() or done.stdout.strip()


def test_no_copyright_line_is_invented():
    """It reports what a package states about itself, or nothing. A placeholder is not a statement."""
    module = _generator()
    for name, _node in module.closure().items():
        line = module.notice(name)
        assert "[yyyy]" not in line and "[name" not in line, f"{name} got a template, not a notice"


def test_every_package_in_the_runtime_tree_is_listed():
    module = _generator()
    listed = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    missing = [n for n in module.closure() if f"**{n}**" not in listed]
    assert not missing, f"in the bundle's tree and not in the notices: {missing[:8]}"


# --- what a package-level inventory cannot see -------------------------------------------------------

# Fingerprints of the Live2D Cubism Web Framework: JSON keys its physics and motion parsers read. They
# are string literals, so minification leaves them alone, which is the only reason this is measurable —
# the class names a symbol search would look for are mangled away.
_FRAMEWORK_MARKS = ("PhysicsSettings", "Normalization", "FadeInTime", "FadeOutTime")

WEB = ROOT / "api" / "src" / "kotoba" / "web"


def _built_bundles():
    if not WEB.is_dir():
        pytest.skip("no packaged web UI here — nothing to inspect")
    return list((WEB / "_next" / "static" / "chunks").glob("*.js"))


def test_a_framework_compiled_inside_a_dependency_is_still_named():
    """The generated list is per PACKAGE, and it cannot see inside one.

    `pixi-live2d-display` is MIT and listed as MIT, and its `cubism4` build carries Live2D's own
    Cubism Web Framework compiled in — a different company's code under a different licence, arriving
    with its source headers minified off. Every witness said the bundle was MIT.
    """
    bundles = _built_bundles()
    text = "\n".join(b.read_text(encoding="utf-8", errors="ignore") for b in bundles)
    if not any(mark in text for mark in _FRAMEWORK_MARKS):
        pytest.skip("no Live2D framework in this build")

    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "Cubism Web Framework" in notices, "the framework ships and the notices do not name it"
    assert "Live2D Open Software License" in notices, \
        "named, but under the wrong licence — Core's terms are not the Framework's"


def test_the_runtime_the_framework_needs_is_shipped_beside_it():
    """Live2D permits the Framework to travel only inside a work that also ships its own runtime. That
    runtime is Cubism Core, and it is a file we place by hand — so this is a real thing to lose."""
    bundles = _built_bundles()
    text = "\n".join(b.read_text(encoding="utf-8", errors="ignore") for b in bundles)
    if not any(mark in text for mark in _FRAMEWORK_MARKS):
        pytest.skip("no Live2D framework in this build")
    assert (WEB / "live2dcubismcore.min.js").is_file(), \
        "the framework is packaged without the runtime its licence requires beside it"
