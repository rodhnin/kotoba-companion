"""The writer for the models directory: an archive from outside, unpacked into a folder served on the
app's own origin. `model_library` reads it; everything that puts something there is here.

Two doors, one unpacker: Live2D's free sample fetched by the person's own machine (their licence
forbids us redistributing it, which is why nothing ships), and a `.zip` they supply. Both are
attacker-controlled input, so every entry is judged before a byte is written, and the whole thing is
staged and swapped into place — a half-written model directory is an install nobody can diagnose.

What may LAND is not what may be SERVED. Serving refuses what a browser would run; installing refuses
that too, and keeps the licence that has to stay beside it and the motion audio Cubism references."""
from __future__ import annotations

import asyncio
import errno
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path

import httpx

from kotoba.core import model_library
from kotoba.core.atomic_file import exclusive, publish
from kotoba.core.path_security import PathSecurityError, validate_within_dir
from kotoba.core.ssrf import check_redirect, url_block_reason

DEFAULT_MODEL = {
    "name": "niziiro-mao",
    "url": "https://cubism.live2d.com/sample-data/bin/mao/mao_en.zip",
    "licence": "https://www.live2d.com/en/learn/sample/model-terms/",
}

_MAX_ARCHIVE_BYTES = 200_000_000    # the sample is ~74 MB; this is headroom, not a target
_MAX_UNPACKED_BYTES = 300_000_000
_MAX_ENTRIES = 4000
_MAX_NAME_CHARS = 200
_MAX_REDIRECTS = 5
_MAX_DIR_CHARS = 60
_CHUNK = 1 << 16
_ENTRY_SUFFIX = ".model3.json"
_REDIRECTS = (301, 302, 303, 307, 308)
_DRIVE = re.compile(r"^[A-Za-z]:")
_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9._-]+")

# The serving list, plus what a model arrives WITH and nothing serves: the licence, and motion audio.
_INSTALLABLE = {
    ".json", ".moc3", ".moc", ".png", ".jpg", ".jpeg", ".webp",
    ".txt", ".md", ".wav", ".mp3",
}


class InstallError(Exception):
    """A refusal in words a person can act on. `status` is what the HTTP door should answer."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _disk_error(e: OSError, kept: str = "") -> InstallError:
    """`kept` names where the previous model was left when even putting it back was refused — the one
    case where the person has to be told a folder name, because nothing sweeps those."""
    where = f" Your previous one is safe in `{kept}`; rename it back when nothing is holding it." if kept else ""
    if e.errno == errno.ENOSPC:
        return InstallError("there is not enough free disk space to install this model" + where, status=507)
    return InstallError(f"the model could not be written to disk ({e.strerror or e}).{where}", status=500)


def _safe_parts(name: str) -> list[str] | None:
    """The entry's path split into parts, or None if the name is not a plain relative path.

    Judged by the rules of the platform the archive was BUILT for, not this one: a backslash is a
    separator and a drive letter is an absolute path, so `..\\..\\x` and `C:\\x` are escapes that land
    on POSIX as innocent-looking filenames."""
    if not name or len(name) > _MAX_NAME_CHARS or "\x00" in name:
        return None
    if name[0] in "/\\" or _DRIVE.match(name):
        return None
    parts = [p for p in re.split(r"[/\\]", name) if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return parts


def _is_plain_file(info: zipfile.ZipInfo) -> bool:
    """A symlink inside a directory served over HTTP is a file read, and a device node is worse.

    The test is on the FILE-TYPE bits alone: plenty of ordinary entries carry permissions and no type
    at all (a zip built on Windows, and everything `writestr` produces), and reading that absence as
    "not a regular file" refuses every honest archive."""
    kind = (info.external_attr >> 16) & 0o170000
    return kind in (0, stat.S_IFREG)


def _write_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, room: int) -> int:
    """Copy one entry, counting what ACTUALLY arrives rather than what the header promised."""
    written = 0
    try:
        with zf.open(info) as src, open(target, "wb") as out:
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > room:
                    raise InstallError("that archive unpacks to more than this installer will write")
                out.write(chunk)
    except InstallError:
        raise
    except (zipfile.BadZipFile, EOFError, ValueError) as e:
        raise InstallError(f"that archive is damaged and did not unpack ({e})") from e
    except OSError as e:
        raise _disk_error(e) from e
    return written


def _unpack(archive: Path, into: Path) -> list[str]:
    """Every entry validated before it is written; returns the extensions (or bare names) left out."""
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as e:
        raise InstallError("that file is not a zip archive") from e
    skipped: list[str] = []
    total = 0
    with zf:
        infos = zf.infolist()
        if len(infos) > _MAX_ENTRIES:
            raise InstallError(f"that archive holds more than {_MAX_ENTRIES} files")
        for info in infos:
            if info.is_dir():
                continue
            parts = _safe_parts(info.filename)
            if parts is None:
                raise InstallError("that archive names a file outside the folder it installs into")
            if not _is_plain_file(info):
                raise InstallError("that archive carries a link or a device, not just files")
            suffix = Path(parts[-1]).suffix.lower()
            if suffix not in _INSTALLABLE or any(p.startswith(".") or p == "__MACOSX" for p in parts):
                skipped.append(suffix or parts[-1])
                continue
            if total + info.file_size > _MAX_UNPACKED_BYTES:
                raise InstallError("that archive unpacks to more than this installer will write")
            try:
                target = validate_within_dir("/".join(parts), into)
            except PathSecurityError as e:
                raise InstallError("that archive names a file outside the folder it installs into") from e
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                # A crafted archive can name `a` as a file and then `a/b`. That is a bad archive, not
                # a failing disk, and it must not be reported as one.
                if e.errno in (errno.EEXIST, errno.ENOTDIR):
                    raise InstallError("that archive contradicts itself about its own folders") from e
                raise _disk_error(e) from e
            total += _write_entry(zf, info, target, _MAX_UNPACKED_BYTES - total)
    return skipped


def _entry_file(staged: Path) -> Path:
    """The `.model3.json` that makes this a model — shallowest, then alphabetical, so an archive that
    carries two of them installs the same one every time.

    Alphabetical by the path TEXT: tie-broken on the Path object, "every time" stopped at the platform
    boundary, because Windows compares those case-folded. The same download wore a different face."""
    found = sorted(staged.rglob("*" + _ENTRY_SUFFIX),
                   key=lambda p: (len(p.relative_to(staged).parts), p.relative_to(staged).as_posix()))
    for p in found:
        if p.is_file():
            return p
    raise InstallError("that archive holds no .model3.json, so it is not a Live2D model")


def _model_root(staged: Path, entry: Path) -> Path:
    """What becomes the installed folder: the entry's own directory, or its parent when that directory
    is the `runtime/` the official samples ship — which keeps the licence sitting beside it and keeps
    the entry within the depth `model_library` scans. Wrapper folders around it are dropped."""
    root = entry.parent
    if root.name.lower() == "runtime" and root != staged:
        return root.parent
    return root


def _weigh(root: Path) -> tuple[int, int]:
    """What actually landed, measured on the staged tree rather than promised by the archive: the zip's
    own headers are attacker-controlled, and entries are dropped on the way in."""
    files = 0
    total = 0
    try:
        for p in root.rglob("*"):
            if p.is_file():
                files += 1
                total += p.stat().st_size
    except OSError as e:
        raise _disk_error(e) from e
    return files, total


def _folder_name(entry: Path) -> str:
    cleaned = _UNSAFE_IN_NAME.sub("-", entry.name[: -len(_ENTRY_SUFFIX)]).strip("-.")
    return cleaned[:_MAX_DIR_CHARS] or "model"


def _publish(root: Path, name: str) -> bool:
    """Move the validated tree into place under a lock, so two installs cannot interleave and nothing
    ever appears half-written. Reinstalling replaces: one model, one folder, the newer one."""
    models = model_library.models_dir()
    dest = models / name
    aside = None
    try:
        with exclusive(models / ".install"):
            try:
                # Through `publish`: on Windows a rename is refused while anything holds a file in
                # the tree, and a model folder is exactly what a scanner has just finished reading.
                if dest.exists():
                    aside = models / f".replaced-{uuid.uuid4().hex}"
                    publish(str(dest), str(aside))
                publish(str(root), str(dest))
            except OSError as e:
                # The rollback can be refused too — on Windows both renames are the same scanner. Left
                # bare it replaced the original error with an OSError nobody upstream catches, and the
                # person's previous model stayed in a dot-named folder that nothing ever sweeps.
                if aside is not None and not dest.exists():
                    try:
                        publish(str(aside), str(dest))
                    except OSError:
                        raise _disk_error(e, kept=aside.name) from e
                raise _disk_error(e) from e
            if aside is not None:
                shutil.rmtree(aside, ignore_errors=True)
            return aside is not None
    except TimeoutError as e:
        raise InstallError("another model is being installed right now — try again in a moment",
                           status=409) from e


def _box() -> Path:
    """A staging directory beside the destination: the same filesystem, so the final move is a rename
    and not a copy. Dot-named, because `model_library` skips those and must never offer one."""
    models = model_library.models_dir()
    try:
        models.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=".install-", dir=models))
    except OSError as e:
        raise _disk_error(e) from e


def install_zip(archive: str | Path) -> dict:
    """Install one archive and report what happened. Blocking — call it off the event loop."""
    box = _box()
    try:
        staged = box / "unpacked"
        staged.mkdir()
        skipped = _unpack(Path(archive), staged)
        entry = _entry_file(staged)
        root = _model_root(staged, entry)
        name = _folder_name(entry)
        rel = entry.relative_to(root).as_posix()
        files, size = _weigh(root)
        replaced = _publish(root, name)
        return {"dir": name, "entry": rel, "replaced": replaced,
                "skipped": len(skipped), "skipped_kinds": sorted(set(skipped)),
                "files": files, "bytes": size,
                "path": str(model_library.models_dir() / name)}
    finally:
        shutil.rmtree(box, ignore_errors=True)


async def _write_stream(chunks, target: Path) -> Path:
    total = 0
    try:
        with open(target, "wb") as out:
            async for chunk in chunks:
                total += len(chunk)
                if total > _MAX_ARCHIVE_BYTES:
                    raise InstallError(
                        f"that archive is larger than {_MAX_ARCHIVE_BYTES // 1_000_000} MB", status=413)
                out.write(chunk)
    except OSError as e:
        raise _disk_error(e) from e
    if not total:
        raise InstallError("nothing arrived — the archive was empty")
    return target


async def _download(url: str, into: Path) -> Path:
    """Fetch to disk, never to memory, with the guard the rest of the app's outbound calls use. Every
    redirect hop is re-checked: the first URL being safe says nothing about where it sends us."""
    reason = url_block_reason(url)
    if reason:
        raise InstallError(f"that download address is not allowed ({reason})")
    target = into / "model.zip"
    current = url
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS):
                async with client.stream("GET", current, headers={"User-Agent": "Kotoba"}) as r:
                    if r.status_code in _REDIRECTS and "location" in r.headers:
                        nxt, why = check_redirect(current, r.headers["location"])
                        if why:
                            raise InstallError(f"that download redirected somewhere it may not go ({why})")
                        current = nxt
                        continue
                    if r.status_code >= 400:
                        raise InstallError(f"the download answered {r.status_code}")
                    declared = r.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > _MAX_ARCHIVE_BYTES:
                        raise InstallError("that download is larger than this installer will accept",
                                           status=413)
                    return await _write_stream(r.aiter_bytes(), target)
    except httpx.HTTPError as e:
        raise InstallError(f"the download did not finish ({e})") from e
    raise InstallError("that download redirected too many times")


#: Which folder the sample unpacked into, remembered beside the models rather than assumed. The name
#: comes out of the archive, so nothing can know it before the first download — and a screen that
#: cannot tell offers the 74 MB again to somebody already wearing it.
_DEFAULT_MARK = ".default"


def default_installed_dir() -> str:
    from kotoba.core.model_library import models_dir

    try:
        name = (models_dir() / _DEFAULT_MARK).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return name if name and (models_dir() / name).is_dir() else ""


async def install_default() -> dict:
    """Fetch Live2D's free sample from Live2D, on this machine, and install it. The address has a default;
    an operator can point it at their own mirror, which is checked exactly like the original."""
    from kotoba.core.model_library import models_dir

    box = _box()
    try:
        archive = await _download(os.getenv("KOTOBA_DEFAULT_MODEL_URL") or DEFAULT_MODEL["url"], box)
        got = await asyncio.to_thread(install_zip, archive)
        try:
            (models_dir() / _DEFAULT_MARK).write_text(str(got.get("dir") or ""), encoding="utf-8")
        except OSError:
            pass
        return got
    finally:
        shutil.rmtree(box, ignore_errors=True)


async def install_stream(chunks, declared: str | None = None) -> dict:
    """Install an archive arriving as a byte stream. It goes to disk as it arrives — tens of megabytes
    never belong in memory — and a declared length over the cap is refused before any of it is read."""
    if declared and declared.isdigit() and int(declared) > _MAX_ARCHIVE_BYTES:
        raise InstallError(f"that archive is larger than {_MAX_ARCHIVE_BYTES // 1_000_000} MB", status=413)
    box = _box()
    try:
        archive = await _write_stream(chunks, box / "upload.zip")
        return await asyncio.to_thread(install_zip, archive)
    finally:
        shutil.rmtree(box, ignore_errors=True)
