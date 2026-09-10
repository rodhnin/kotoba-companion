"""A no-sandbox flag should apply only where Chromium truly cannot build a sandbox.

It was passed unconditionally, so a normal install ran both the report renderer and her headless
browser with the renderer sandbox off — the browser being the sharp end, since it visits unvetted
pages. The first fix gated it on root, which was wrong exactly where it mattered most: the project's
own container runs as an unprivileged user, and without user namespaces that uid cannot build a
sandbox either, so Chromium exits with no usable sandbox and reports came out as nothing at all. One
deployment target escaped only because it happens to run as root. The fix is now an explicit env var
the image sets, not a guess from the environment; the CDP path never carries the flag, since that
browser is one the user already launched."""
from __future__ import annotations

import os

import pytest
from conftest import posix_only

from kotoba.core import pdf
from kotoba.core.mcp import known


@pytest.fixture(autouse=True)
def _fallback_browser(monkeypatch, tmp_path):
    """No CDP configured — the headless-playwright path an OSS clone gets out of the box."""
    monkeypatch.delenv("KOTOBA_BROWSER_CDP", raising=False)
    monkeypatch.delenv("KOTOBA_BROWSER_EXECUTABLE", raising=False)
    monkeypatch.delenv("KOTOBA_CHROMIUM_NO_SANDBOX", raising=False)
    monkeypatch.setenv("KOTOBA_BROWSER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)


def _browser_args():
    return known.build_cfg("browser")[1]["args"]


def test_a_normal_account_keeps_the_renderer_sandbox():
    assert not pdf.no_sandbox_needed()
    assert "--no-sandbox" not in _browser_args()
    assert pdf._sandbox_flags() == ()


@posix_only("os.geteuid")
def test_root_still_gets_it():
    """Kept because Chromium refuses outright as root, which is how Railway runs."""
    os.environ.pop("KOTOBA_CHROMIUM_NO_SANDBOX", None)
    real = os.geteuid
    os.geteuid = lambda: 0
    try:
        assert pdf.no_sandbox_needed()
        assert "--no-sandbox" in _browser_args()
    finally:
        os.geteuid = real


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE"])
def test_the_container_env_var_turns_it_on_for_a_non_root_uid(monkeypatch, value):
    """The case the root-only gate broke: our image, uid 10001, no PDF at all and a silent 503."""
    monkeypatch.setenv("KOTOBA_CHROMIUM_NO_SANDBOX", value)
    assert pdf.no_sandbox_needed()
    assert pdf._sandbox_flags() == ("--no-sandbox",)
    assert "--no-sandbox" in _browser_args()


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_an_unset_or_false_var_leaves_the_sandbox_on(monkeypatch, value):
    monkeypatch.setenv("KOTOBA_CHROMIUM_NO_SANDBOX", value)
    assert not pdf.no_sandbox_needed()


def test_the_cdp_path_never_carries_it(monkeypatch):
    monkeypatch.setenv("KOTOBA_CHROMIUM_NO_SANDBOX", "1")
    monkeypatch.setenv("KOTOBA_BROWSER_CDP", "http://127.0.0.1:9222")
    args = _browser_args()
    assert "--no-sandbox" not in args and "--cdp-endpoint" in args


def test_the_image_sets_the_var_so_reports_still_render():
    """A test against the Dockerfile because the code cannot know it: if the ENV line is ever dropped,
    the container silently stops producing reports and only this fails."""
    from kotoba.paths import REPO_ROOT

    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER kotoba" in dockerfile, "if the image stopped dropping privileges, revisit this whole file"
    assert "ENV KOTOBA_CHROMIUM_NO_SANDBOX=1" in dockerfile
