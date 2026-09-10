"""Resolve the jailed working directory for a Task.

  default (no KOTOBA_WORKSPACE_DIR) → the file LIBRARY (~/.kotoba/files): Kotoba works directly in the
        single place the Files panel shows, so creating folders, moving and deleting files reflect there
        immediately (no per-session copy/sync, no duplicates). This is what makes "move index.html and its
        assets into web/" Just Work and stay browsable.
  KOTOBA_WORKSPACE_DIR set → that single host project folder instead (advanced: point Kotoba at a real repo).

Whatever this returns becomes the root that path_security jails every file/shell/code op to.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from kotoba.paths import home_dir


def resolve_workdir(session_id: str | None) -> Path:
    ws = os.getenv("KOTOBA_WORKSPACE_DIR", "").strip()
    if ws:
        return Path(ws).expanduser().resolve()
    # Default: work IN the library so the panel mirrors exactly what's on disk.
    from kotoba.core import file_library

    d = file_library.library_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


log = logging.getLogger("kotoba.workspace")

_SCRATCH_MARK = ".kotoba-scratch"
_refused_sweep = False


def scratch_dir() -> Path:
    """A dedicated SCRATCH dir for throwaway/temporary work (downloads in progress, intermediate files,
    `mktemp`), the way a normal shell uses /tmp — but kept under ~/.kotoba so it's tidy, OUTSIDE the Files
    library (it never clutters the user's panel) and not the global system /tmp. Exposed to shell/code as
    TMPDIR (see the local sandbox). Override with KOTOBA_TMP_DIR."""
    d = Path(os.getenv("KOTOBA_TMP_DIR", str(home_dir() / "tmp"))).expanduser()
    had_things = d.is_dir() and any(x.name != _SCRATCH_MARK for x in d.iterdir())
    d.mkdir(parents=True, exist_ok=True)
    # The mark is what earns the sweeper its licence, and it is only ever laid on a directory we made
    # or were handed empty. Pointed at somewhere that already had things in it — a downloads folder,
    # /tmp — the mark is withheld and the sweep declines rather than eating somebody's work.
    if not had_things:
        try:
            (d / _SCRATCH_MARK).touch(exist_ok=True)
        except OSError:
            pass
    return d.resolve()


def clean_scratch(max_age_hours: float = 24.0) -> int:
    """Remove scratch entries older than max_age_hours. Best-effort; returns how many were removed."""
    import time

    root = scratch_dir()
    if not (root / _SCRATCH_MARK).exists():
        global _refused_sweep
        if not _refused_sweep:
            _refused_sweep = True
            log.warning("%s already held files when it was handed over, so nothing there is swept — "
                        "point KOTOBA_TMP_DIR at an empty directory to get the cleanup back", root)
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    try:
        for p in root.iterdir():
            try:
                if p.name == _SCRATCH_MARK:
                    continue
                if p.stat().st_mtime < cutoff:
                    if p.is_dir():
                        import shutil

                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink()
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed
