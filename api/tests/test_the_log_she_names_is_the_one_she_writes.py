"""Two refusals send a person to read the log, and both named a file that has never existed.

`logs.path()` is the one origin, but its two callers each carry a hardcoded string for the case where
it raises — and a fallback is exactly the branch nobody looks at, so it said `kotoba.log` while the
writer had long since been `cli.log`. Somebody following that sentence finds nothing and concludes
the failure left no trace. The fallback has to name the same file the writer opens."""
from __future__ import annotations

from pathlib import Path

import pytest

from kotoba.cli import wizard
from kotoba.core import logs, voice_key

NAMERS = [wizard._log_path, voice_key.log_path]


@pytest.mark.parametrize("namer", NAMERS, ids=lambda f: f.__module__)
def test_the_name_it_gives_is_the_file_that_gets_written(namer):
    assert Path(namer()).name == logs.path().name


@pytest.mark.parametrize("namer", NAMERS, ids=lambda f: f.__module__)
def test_the_fallback_names_it_too_when_the_real_path_cannot_be_read(namer, monkeypatch):
    monkeypatch.setattr(logs, "path", lambda: (_ for _ in ()).throw(OSError("no home")))
    assert Path(namer()).name == "cli.log"


def test_the_writer_is_still_the_one_this_pins():
    """Guards the guard: pinning a literal would pass happily after the writer moved."""
    assert logs.path().name == "cli.log"
