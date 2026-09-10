"""Build the web UI and place it inside the Python package, so a wheel needs no Node.

Run from a clone with Node installed. The wheel build refuses to package a `web/` this did not write.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "api" / "src" / "kotoba" / "web"
EXPORT = REPO / ".next-export"

INPUTS = ("app", "components", "lib", "public", "next.config.ts", "tsconfig.json",
          "global.d.ts", "package.json", "package-lock.json")
SKIP_DIR_NAMES = {"__tests__"}
SKIP_PATHS = ("public/models",)
PAGES = ("app.html", "login.html", "setup.html", "404.html")
STAMP = "kotoba-build.json"

#: A build must not carry the machine that made it. `next build` inlines any `NEXT_PUBLIC_*` it finds
#: in the environment or a `.env.local`, and minified into a chunk one address looks like any other.
#: Two nets, judging different things: an allowlist for hosts that legitimately appear, and — checked
#: FIRST, so no allowlist entry can ever disable them — the shapes that can only be somebody's own
#: machine. Every entry below was seen in a real build and looked at once.
KNOWN_HOSTS = {
    "www.w3.org",              # XML namespaces in the icon and in React's SVG handling
    "nextjs.org",              # Next's runtime errors link to its own docs
    "react.dev",              # React's minified error decoder builds a link from an error number
    "github.com",              # licence and source headers of bundled libraries
    "aomediacodec.github.io",              # an RTP header-extension identifier, not a link
    "www.pixijs.com",              # PixiJS prints a banner to the console
    "www.live2d.com",              # the Cubism Core licence header
    "api.openai.com",              # the placeholder base URL the Settings panel shows
    "cdn.jsdelivr.net",              # where the voice SDK fetches its resampler worklet at runtime
    "gate.invalid",              # a base that can never resolve, for parsing a relative redirect
}
KNOWN_SUFFIXES = (".elevenlabs.io",)

#: Refused as the host of a URL, tolerated as a bare word: `ws://localhost:9777` is one machine's
#: configuration, while the same word inside an error message or an SDP template identifies nobody.
LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
UNSPECIFIC_IPS = {"0.0.0.0", "127.0.0.1", "255.255.255.255"}
PRIVATE_SUFFIXES = ("local", "lan", "internal", "home", "corp", "intranet", "localdomain",
                    "home.arpa", "localhost")
TUNNELS = (
    "ngrok.io", "ngrok.app", "ngrok-free.app", "ngrok.dev", "ngrok-free.dev",
    "trycloudflare.com", "cfargotunnel.com", "loca.lt", "localtunnel.me", "serveo.net",
    "localhost.run", "lhr.life", "ts.net", "pinggy.io", "pinggy.link", "zrok.io", "bore.pub",
    "tunnelto.dev", "expose.dev", "localxpose.io", "loclx.io", "pagekite.me", "telebit.io",
)
HOST_TLDS = {
    "com", "net", "org", "io", "dev", "app", "ai", "me", "co", "cc", "xyz", "info", "biz",
    "eu", "uk", "es", "de", "fr", "it", "nl", "be", "ch", "at", "pt", "se", "no", "fi", "dk",
    "ie", "cz", "ru", "ua", "cn", "jp", "kr", "tw", "sg", "au", "nz", "br", "mx", "ar", "cl",
    "ve", "pe", "ca", "us", "tv", "fm", "gg", "im", "is", "to", "ly", "st", "la", "nu", "ws",
    "tech", "cloud", "site", "online", "page", "run", "zone", "host", "network", "systems",
    "digital", "studio", "live", "chat", "stream", "link", "click", "top", "club", "space",
    "world", "today", "email", "one", "pro", "name", "mobi", "asia", "moe", "icu", "pw",
    "tk", "ml", "ga", "cf", "gq", "wtf", "lol", "ninja", "cx", "ac",
}
GENERIC_NAMES = {
    "user", "users", "admin", "root", "node", "runner", "build", "builder", "ci", "dev",
    "test", "ubuntu", "debian", "docker", "default", "localhost", "host", "pc", "desktop",
    "laptop", "home", "www", "web", "app", "kotoba", "server", "client", "vagrant",
    "github", "actions", "worker", "jenkins", "travis", "circleci", "azure", "gitlab",
}

_HOST = r"(?:\[[0-9a-f:.]+\]|[a-z0-9._~-]+)"
_USERINFO = r"(?:[a-z0-9._~%!$&'()*+,;=:-]*@)?"
_PORT = r"(?::(\d{1,5}))?"
_PRIVATE_TAILS = tuple("." + s for s in PRIVATE_SUFFIXES)
R_URL = re.compile(r"(?:https?|wss?|ftp|file)://" + _USERINFO + "(" + _HOST + ")" + _PORT, re.I)
R_SCHEME_REL = re.compile(
    r"(?<=[\"'`(=\s,])//(\[[0-9a-f:.]+\]|[a-z0-9-]+(?:\.[a-z0-9-]+)+)" + _PORT
    + r"(?=[\"'`/:?#\s)<]|$)", re.I)
R_IPV4 = re.compile(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3})(?::(\d{1,5}))?(?![\w.])")
R_IPV6 = re.compile(r"\[(?=[0-9a-f:.]*:)([0-9a-f:.]{2,45})\](?::(\d{1,5}))?", re.I)
R_IPV6_BARE = re.compile(r"(?<![\w:.])::1(?![\w:])")
R_PRIVATE = re.compile(
    r"(?<![\w.-])([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:"
    + "|".join(s.replace(".", r"\.") for s in PRIVATE_SUFFIXES)
    + r"))(?::(\d{1,5}))?(?=[\"'`/:?#\s<)]|$)", re.I)
R_TUNNEL = re.compile(
    r"(?<![\w-])((?:[a-z0-9-]+\.)*(?:" + "|".join(t.replace(".", r"\.") for t in TUNNELS)
    + r"))(?![\w-])", re.I)
R_BARE_HOST = re.compile(r"[\"'`]([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?::(\d{1,5}))?[\"'`]", re.I)
R_BARE_PORT = re.compile(r"[\"'`]([a-z][a-z0-9-]{1,62}):(\d{2,5})[\"'`]", re.I)
R_HOME_PATH = re.compile(
    r"(?<![\w])(/(?:home|Users|root)/[A-Za-z0-9._-]+|[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]+)")
R_SOURCEMAP = re.compile(r"[#@]\s*sourceMappingURL\s*=")
R_AGENT_ID = re.compile(r"(?<![A-Za-z0-9_])agent_[0-9a-z]{10,40}(?![A-Za-z0-9_])", re.I)
R_B64 = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{24,}={0,2}(?![A-Za-z0-9+/_-])")
R_JS_SLASH = re.compile(r"\\/|\\u002f|\\x2f", re.I)
R_JS_COLON = re.compile(r"\\u003a|\\x3a", re.I)
R_PCT = re.compile(r"%(3a|2f|2e|5b|5d|40)", re.I)
_PCT = {"3a": ":", "2f": "/", "2e": ".", "5b": "[", "5d": "]", "40": "@"}

PNG_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"sBIT",
              b"bKGD", b"pHYs", b"hIST", b"sPLT", b"iCCP", b"acTL", b"fcTL", b"fdAT"}
WEBP_CHUNKS = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF", b"ICCP"}
SVG_MARKS = ("<metadata", "inkscape:", "sodipodi:", "xmlns:dc", "xmlns:cc", "xmlns:rdf",
             "<!-- Generator", "Adobe Illustrator")


def refuse(why: str) -> int:
    print(f"refusing to build the web UI: {why}", file=sys.stderr)
    return 2


def _is_input(rel: Path) -> bool:
    """Is this file part of what the frontend is built FROM. A test directory never is, wherever it
    sits; the model directory is skipped by its whole PATH, so a future route at `app/models/` is not
    invisible to this the way a name match made it."""
    if any(part in SKIP_DIR_NAMES for part in rel.parts[:-1]):
        return False
    posix = rel.as_posix()
    return not any(posix == s or posix.startswith(s + "/") for s in SKIP_PATHS)


def in_order(files, root: Path) -> list[Path]:
    """Sorted by the path TEXT, because sorting Path objects is not the same order on both platforms.

    `PurePath` compares on a case-folded string under Windows and a plain one everywhere else, so an
    upper-case sibling changes place — and a digest that hashes in that order made a build stamped on
    one platform unverifiable on the other, which is the exact shape this project ships in."""
    return sorted(files, key=lambda f: f.relative_to(root).as_posix())


def sources_digest() -> str:
    h = hashlib.sha256()
    for name in INPUTS:
        p = REPO / name
        if p.is_file():
            files = [p]
        else:
            files = in_order((f for f in p.rglob("*") if f.is_file() and _is_input(f.relative_to(REPO))),
                             REPO)
        for f in files:
            h.update(f.relative_to(REPO).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def web_digest(root: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    for f in in_order((p for p in root.rglob("*") if p.is_file() and p.name != STAMP), root):
        h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
        n += 1
    return h.hexdigest(), n


def version() -> str:
    text = (REPO / "api" / "src" / "kotoba" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not found:
        raise SystemExit("the package declares no __version__")
    return found.group(1)


def _is_ipv4(host: str) -> bool:
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() and int(p) < 256 for p in parts)


def builder_marks() -> set[str]:
    names: set[str] = set()
    # Whose machine this is, asked at scan time rather than written down: the net then calibrates
    # itself to whoever runs it, and a name too short or too common to mean anything is left out.
    for get in (socket.gethostname, socket.getfqdn):
        try:
            names.add(get())
        except OSError:
            pass
    for n in list(names):
        names.add(n.split(".")[0])
    try:
        names.add(getpass.getuser())
    except Exception:
        pass
    marks: set[str] = set()
    for n in names:
        n = n.strip().lower()
        if len(n) >= 5 and n not in GENERIC_NAMES and n not in LOOPBACK_NAMES:
            marks.add(n)
    try:
        home = Path.home()
        if len(home.parts) >= 3:
            marks.add(home.as_posix().lower())
    except Exception:
        pass
    return marks


def _normalize(text: str) -> str:
    text = R_JS_SLASH.sub("/", text)
    text = R_JS_COLON.sub(":", text)
    if "%" in text:
        text = R_PCT.sub(lambda m: _PCT[m.group(1).lower()], text)
    return text


def _judge_host(host: str, port: str | None, where: str, marks: set[str]) -> str | None:
    # Order matters: every shape that can only be a private machine is answered before the allowlist
    # is consulted, so putting `something.lan` on the list would not let it through.
    host = host.lower().rstrip(".")
    shown = host + (f":{port}" if port else "")
    if host.startswith("["):
        return f"{shown} (ipv6 literal, {where})"
    if _is_ipv4(host):
        return f"{shown} (ip literal, {where})"
    if "." not in host:
        if host in LOOPBACK_NAMES:
            return f"{shown} (loopback, {where})"
        if host in marks:
            return f"{shown} (builder name, {where})"
        return None
    if host in LOOPBACK_NAMES or host.endswith(".localhost"):
        return f"{shown} (loopback, {where})"
    if host.endswith(_PRIVATE_TAILS):
        return f"{shown} (private name, {where})"
    if R_TUNNEL.fullmatch(host):
        return f"{shown} (tunnel, {where})"
    if host in marks:
        return f"{shown} (builder name, {where})"
    if host in KNOWN_HOSTS or host.endswith(KNOWN_SUFFIXES):
        return None
    return f"{shown} (unknown host, {where})"


def _scan_text(text: str, marks: set[str], literal_shapes: bool) -> set[str]:
    hits: set[str] = set()
    low = text.lower()
    for m in R_URL.finditer(text):
        found = _judge_host(m.group(1), m.group(2), "url", marks)
        if found:
            hits.add(found)
    for m in R_SCHEME_REL.finditer(text):
        found = _judge_host(m.group(1), m.group(2), "scheme-relative", marks)
        if found:
            hits.add(found)
    for m in R_IPV4.finditer(text):
        if m.group(1) not in UNSPECIFIC_IPS and _is_ipv4(m.group(1)):
            hits.add(f"{m.group(0)} (bare ip)")
    for m in R_IPV6.finditer(text):
        hits.add(f"[{m.group(1).lower()}] (ipv6 literal)")
    if R_IPV6_BARE.search(text):
        hits.add("::1 (ipv6 loopback)")
    for m in R_PRIVATE.finditer(text):
        hits.add(f"{m.group(1).lower()}{':' + m.group(2) if m.group(2) else ''} (private name)")
    for m in R_TUNNEL.finditer(text):
        hits.add(f"{m.group(1).lower()} (tunnel)")
    for m in R_HOME_PATH.finditer(text):
        hits.add(f"{m.group(1)} (home path)")
    if R_SOURCEMAP.search(text):
        hits.add("sourceMappingURL (source map)")
    for m in R_AGENT_ID.finditer(text):
        hits.add(f"{m.group(0)} (agent id)")
    for mark in marks:
        if mark.startswith("/"):
            if mark in low:
                hits.add(f"{mark} (builder home)")
        elif re.search(r"(?<![a-z0-9])" + re.escape(mark) + r"(?![a-z0-9])", low):
            hits.add(f"{mark} (builder name)")
    if literal_shapes:
        for m in R_BARE_HOST.finditer(text):
            host = m.group(1).lower()
            if host.rsplit(".", 1)[-1] in HOST_TLDS or host.endswith(KNOWN_SUFFIXES):
                found = _judge_host(host, m.group(2), "bare literal", marks)
                if found:
                    hits.add(found)
        for m in R_BARE_PORT.finditer(text):
            name = m.group(1).lower()
            if name in LOOPBACK_NAMES or name in marks:
                hits.add(f"{name}:{m.group(2)} (bare name:port)")
    return hits


def _decoded_blobs(text: str):
    for m in R_B64.finditer(text):
        raw = m.group(0).rstrip("=")
        for decode in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                yield decode(raw + "=" * (-len(raw) % 4)).decode("latin-1")
                break
            except Exception:
                continue


def strangers(tree: Path) -> list[str]:
    marks = builder_marks()
    out: set[str] = set()
    for f in sorted(p for p in tree.rglob("*") if p.is_file() and p.name != STAMP):
        rel = f.relative_to(tree).as_posix()
        if f.suffix == ".map":
            out.add(f"{rel}: whole file (source map)")
            continue
        for hit in _scan_text(rel, marks, False):
            out.add(f"{rel}: {hit} [file name]")
        text = f.read_bytes().decode("latin-1")
        for hit in _scan_text(_normalize(text), marks, True):
            out.add(f"{rel}: {hit}")
        for blob in _decoded_blobs(text):
            for hit in _scan_text(blob, marks, False):
                out.add(f"{rel}: {hit} [base64]")
    return sorted(out)


def _png_extras(d: bytes) -> list[str]:
    out, i = [], 8
    while i + 8 <= len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        ty = d[i + 4:i + 8]
        if ty not in PNG_CHUNKS:
            out.append(f"{ty.decode('latin-1')} chunk, {ln} bytes")
        i += 12 + ln
    return out


def _webp_extras(d: bytes) -> list[str]:
    out, i = [], 12
    while i + 8 <= len(d):
        ty = d[i:i + 4]
        ln = struct.unpack("<I", d[i + 4:i + 8])[0]
        if ty not in WEBP_CHUNKS:
            out.append(f"{ty.decode('latin-1').strip()} chunk, {ln} bytes")
        i += 8 + ln + (ln & 1)
    return out


def _jpeg_extras(d: bytes) -> list[str]:
    out, i = [], 2
    while i + 4 <= len(d) and d[i] == 0xFF:
        marker = d[i + 1]
        ln = struct.unpack(">H", d[i + 2:i + 4])[0]
        if marker in (0xDA, 0xD9):
            break
        if marker in (0xE1, 0xE2, 0xEB, 0xEC, 0xED, 0xEE, 0xFE):
            out.append(f"segment 0x{marker:02X}, {ln} bytes")
        i += 2 + ln
    return out


def fingerprints(tree: Path) -> list[str]:
    out = []
    for f in sorted(p for p in tree.rglob("*") if p.is_file() and p.name != STAMP):
        rel = f.relative_to(tree).as_posix()
        d = f.read_bytes()
        extras: list[str] = []
        if d[:8] == b"\x89PNG\r\n\x1a\n":
            extras = _png_extras(d)
        elif d[:4] == b"RIFF" and d[8:12] == b"WEBP":
            extras = _webp_extras(d)
        elif d[:2] == b"\xff\xd8":
            extras = _jpeg_extras(d)
        elif f.suffix == ".svg":
            text = d.decode("utf-8", errors="replace")
            extras = [m for m in SVG_MARKS if m in text]
        elif f.suffix == ".map":
            extras = ["source map"]
        out.extend(f"{rel}: {e}" for e in extras)
    return out


def _model_art(root: Path) -> list[Path]:
    """Every Live2D file under `root`, symlinked directories included.

    `Path.glob` will not descend a symlink, so a model linked into `public/` was invisible here while
    the build packaged it anyway — and the whole point of this refusal is that the art is somebody
    else's and cannot be redistributed. Real paths are remembered so a loop of links cannot hang it,
    and a directory it cannot read raises: a licence guard must not fail open."""
    def refuse_to_guess(err: OSError) -> None:
        raise err

    found: list[Path] = []
    seen: set[str] = set()
    for base, dirs, files in os.walk(root, followlinks=True, onerror=refuse_to_guess):
        real = os.path.realpath(base)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        found += [Path(base) / f for f in files if f.endswith((".moc3", ".model3.json"))]
    return found


def main() -> int:
    reads = (".env", ".env.local", ".env.production", ".env.production.local",
             ".env.development", ".env.development.local")
    stray = sorted(n for n in reads if (REPO / n).is_file())
    if stray:
        return refuse(f"{', '.join(stray)} would be read by the build and frozen into the bundle")
    art = sorted(str(p.relative_to(REPO)) for p in _model_art(REPO / "public"))
    if art:
        return refuse(f"a Live2D model sits under public/ and would be redistributed: {art[0]}")
    npm = shutil.which("npm")
    if npm is None:
        return refuse("npm is not on PATH; the packaged web UI is built with Node")

    env = {k: v for k, v in os.environ.items()
           if not k.startswith("NEXT_PUBLIC_") and k not in ("DOCKER_BUILD", "KOTOBA_BACKEND_URL")}
    env["KOTOBA_STATIC_EXPORT"] = "1"
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    shutil.rmtree(EXPORT, ignore_errors=True)
    built = subprocess.run([npm, "run", "build"], cwd=REPO, env=env)
    if built.returncode != 0:
        return refuse(f"`npm run build` exited {built.returncode}")

    missing = [p for p in PAGES if not (EXPORT / p).is_file()]
    if missing:
        return refuse(f"the build produced no {', '.join(missing)}")
    if not any((EXPORT / "_next" / "static").rglob("*.js")):
        return refuse("the build produced no javascript")
    found = strangers(EXPORT)
    if found:
        print("refusing: an address or a name of this machine was frozen into the build", file=sys.stderr)
        for line in found:
            print(f"  {line}", file=sys.stderr)
        return 2
    marked = fingerprints(EXPORT)
    if marked:
        print("refusing: a file carries metadata that would travel with every install", file=sys.stderr)
        for line in marked:
            print(f"  {line}", file=sys.stderr)
        return 2

    shutil.rmtree(WEB, ignore_errors=True)
    shutil.copytree(EXPORT, WEB)
    shutil.rmtree(REPO / "api" / "build", ignore_errors=True)
    digest, count = web_digest(WEB)
    stamp = {"version": version(), "files": count, "web_sha256": digest,
             "sources_sha256": sources_digest()}
    (WEB / STAMP).write_text(json.dumps(stamp, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(stamp, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
