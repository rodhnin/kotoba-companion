"""Filesystem jail.

Every path a tool receives from the model MUST pass through `validate_within_dir(path, root)` before
it is opened/written. It resolves symlinks and `..` and guarantees the final path stays inside `root`,
so the model can never read/write outside its jailed working dir.
"""
from __future__ import annotations

from pathlib import Path


class PathSecurityError(Exception):
    """Raised when a path would escape its jail (root) — via .., an absolute path, or a symlink."""


def validate_within_dir(path: str | Path, root: str | Path) -> Path:
    """Return the resolved absolute Path for `path`, guaranteed to live within `root`.

    - A relative `path` is taken relative to `root`.
    - An absolute `path` is allowed only if it already lives within `root`.
    - Symlinks and `..` are resolved first (so a symlink inside `root` pointing outside is rejected).
    - The path need not exist yet (write targets are fine); resolution is non-strict.

    Raises PathSecurityError on any escape.
    """
    root_resolved = Path(root).expanduser().resolve()
    p = Path(path).expanduser()
    candidate = p if p.is_absolute() else (root_resolved / p)
    resolved = candidate.resolve()  # follows symlinks, collapses .. — non-strict (ok if missing)

    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        raise PathSecurityError(f"path {str(path)!r} escapes the jail {str(root_resolved)!r}")
    return resolved
