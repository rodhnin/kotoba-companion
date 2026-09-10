"""A snap browser sees its own /tmp and is refused every dotted directory, and her home is one.

It exits 0 having written nothing, so only asking beforehand tells anybody why. Ubuntu ships chromium
only as a snap, which makes this the ordinary case there rather than an oddity.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kotoba.cli import doctor
from kotoba.core import pdf


@pytest.fixture
def snap(monkeypatch, tmp_path):
    """A snap browser whose own writable area exists, with a home it does not reach."""
    monkeypatch.setattr("kotoba.core.pdf.chromium_path", lambda: "/snap/bin/brave")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_the_render_goes_to_the_one_place_a_snap_may_write(snap):
    area = snap / "snap" / "brave" / "current"
    area.mkdir(parents=True)
    assert pdf.snap_scratch("/snap/bin/brave") == area
    assert pdf._scratch_root("/snap/bin/brave") == area
    assert pdf.pdf_available() is True
    assert doctor._browser().status == "ok"


def test_a_snap_that_never_ran_has_nowhere_to_write(snap):
    """The instrument, shown firing: no area, no PDF, and doctor says so instead of promising one."""
    assert pdf.snap_scratch("/snap/bin/brave") is None
    assert pdf.pdf_available() is False
    check = doctor._browser()
    assert check.status == "warn" and "snap" in check.detail


def test_an_ordinary_browser_keeps_the_system_default(monkeypatch):
    monkeypatch.setattr("kotoba.core.pdf.chromium_path", lambda: "/usr/bin/brave")
    assert pdf._scratch_root("/usr/bin/brave") is None
    assert pdf.pdf_available() is True
    assert doctor._browser().status == "ok"
