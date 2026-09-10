"""HTML → PDF via a headless Chromium (Brave/Chromium/Chrome) — colors guaranteed.

The interactive browser print dialog drops background colors unless the user ticks "Background graphics".
Rendering server-side with `--print-to-pdf` honors `print-color-adjust:exact` with NO checkbox, so the
report comes out exactly as designed. Best-effort: returns None if no Chromium binary is available (the
frontend then falls back to window.print()).

(For a cloud deploy, the image needs a chromium binary, or use the Playwright MCP.)
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

log = logging.getLogger("kotoba.pdf")

_CANDIDATES = [
    "brave", "brave-browser", "chromium", "chromium-browser",
    "google-chrome-stable", "google-chrome",
]


def chromium_path() -> str | None:
    for c in _CANDIDATES:
        p = shutil.which(c)
        if p:
            return p
    return None


def browser_is_snap(path: str) -> bool:
    """A snap browser drives a page perfectly and cannot write the PDF anywhere she keeps files: it
    sees a private /tmp and is refused every dotted directory, and her home is one. It exits 0 having
    written nothing, so only asking beforehand tells anybody why. Ubuntu ships chromium ONLY as a
    snap, which makes this the ordinary case there."""
    return path.startswith("/snap/") or Path(path).resolve().name == "snap"


def snap_scratch(chrome: str) -> Path | None:
    """The one place a snap browser can always both read and write: its own `~/snap/<name>/current`.

    It is not hidden, so the confinement allows it, and it is an ordinary directory to everyone else,
    so we can collect the PDF afterwards. Absent until the snap has been run once."""
    area = Path.home() / "snap" / Path(chrome).name / "current"
    return area if area.is_dir() else None


def _scratch_root(chrome: str) -> Path | None:
    """Where to put the pair of files the render needs. None means the system default."""
    return snap_scratch(chrome) if browser_is_snap(chrome) else None


def pdf_available() -> bool:
    """The one origin: `doctor` reports from it and the chromium-dependent tests skip on it."""
    found = chromium_path()
    if found is None:
        return False
    return not browser_is_snap(found) or snap_scratch(found) is not None


def no_sandbox_needed() -> bool:
    """Whether Chromium must be told to drop its own sandbox before it will start at all.

    It used to be told unconditionally, which turned the renderer sandbox off for every report — while
    rendering HTML assembled from pages she read on the web. A normal account does not need it, so it
    is now the exception.

    Root is one exception and NOT the only one: our own Dockerfile runs an unprivileged uid, and
    without user namespaces that cannot build a sandbox either — Chromium answers "No usable sandbox!"
    and exits, producing no PDF. The same applies to hosts that restrict unprivileged userns, so the
    escape hatch is an explicit env var the image sets, not a guess about the environment."""
    if os.getenv("KOTOBA_CHROMIUM_NO_SANDBOX", "").strip().lower() in ("1", "true", "yes"):
        return True
    return getattr(os, "geteuid", lambda: 1)() == 0


def _sandbox_flags() -> tuple[str, ...]:
    return ("--no-sandbox",) if no_sandbox_needed() else ()


async def html_to_pdf(html: str) -> bytes | None:
    """Render `html` to PDF bytes. Returns None if no chromium is available or rendering fails.

    A failure is LOGGED with Chromium's own stderr. It used to go to DEVNULL, so a container whose
    Chromium refused to start returned a bare 503 and the one line that said why ("No usable sandbox!")
    was thrown away — the same shape of silence that let a GUI launch report success for months."""
    chrome = chromium_path()
    if not chrome:
        return None

    with tempfile.TemporaryDirectory(dir=_scratch_root(chrome)) as d:
        html_file = Path(d) / "report.html"
        pdf_file = Path(d) / "report.pdf"
        html_file.write_text(html, encoding="utf-8")
        # No `--user-data-dir`: the render then uses the person's own browser profile, which is not
        # what anyone would choose — but Brave never finishes a print into a fresh one, and a report
        # that never renders is the worse of the two.
        proc = await asyncio.create_subprocess_exec(
            chrome, "--headless=new", *_sandbox_flags(), "--disable-gpu",
            "--no-pdf-header-footer", f"--print-to-pdf={pdf_file}", f"file://{html_file}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=45)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            log.warning("PDF render timed out after 45s")
            return None
        except asyncio.CancelledError:
            # Cancelling a coroutine never kills the process it spawned; only the timeout branch used to
            # clean up, so an abandoned turn left a headless Chromium behind.
            proc.kill()
            raise
        if pdf_file.exists():
            return pdf_file.read_bytes()
        log.warning("PDF render produced nothing (exit %s): %s", proc.returncode,
                    (err or b"").decode("utf-8", "replace").strip()[:500])
        return None
