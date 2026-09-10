"""An archive from outside, written into a directory this app serves over HTTP.

Every refusal below was watched SUCCEEDING first, against a hand-rolled unpacker with no guards — the
one anybody writes: open the zip, join the name onto the destination, write. It put a file in the
user's home, wrote through a symlink, and installed an `.html` into a folder served on the app's own
origin. So these are not claims about code that always refused; they are the attacks, and the archives
that carry them are built here rather than downloaded.

The other half is shape: a model is a directory holding a `.model3.json`, packed three different
ways, and the installed name has to be predictable enough that `avatar_model` can point at it.
"""
from __future__ import annotations

import asyncio
import io
import stat
import zipfile
from pathlib import Path

import httpx
import pytest
from conftest import make_symlink

from kotoba.core import model_install, model_library

MODEL = [
    ("mao_pro/runtime/mao_pro.model3.json", b'{"Version": 3}'),
    ("mao_pro/runtime/mao_pro.moc3", b"MOC3"),
    ("mao_pro/runtime/texture_00.png", b"\x89PNG\r\n"),
]


def _zip(path: Path, entries, extra: list[zipfile.ZipInfo] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
        for info in extra or []:
            z.writestr(info, b"")
    return path


def _bytes(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def models(tmp_path, monkeypatch):
    root = tmp_path / "models"
    root.mkdir()
    monkeypatch.setenv("KOTOBA_MODELS_DIR", str(root))
    (tmp_path / "victim").mkdir()
    return root


def _outside(tmp_path) -> Path:
    return tmp_path / "victim" / "pwned"


@pytest.mark.parametrize("escape", [
    "../victim/pwned",
    "mao_pro/../../victim/pwned",
    "mao_pro/runtime/../../../victim/pwned",
    "./../victim/pwned",
])
def test_an_entry_that_traverses_is_refused_and_writes_nothing_outside(models, tmp_path, escape):
    """Watched succeeding: the unguarded unpacker wrote tmp_path/victim/pwned from inside the archive."""
    arc = _zip(tmp_path / "evil.zip", [*MODEL, (escape, b"owned")])
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(arc)
    assert not _outside(tmp_path).exists()


def test_an_absolute_entry_name_never_lands_at_that_absolute_path(models, tmp_path):
    target = _outside(tmp_path)
    arc = _zip(tmp_path / "abs.zip", [*MODEL, (str(target), b"owned")])
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(arc)
    assert not target.exists()


@pytest.mark.parametrize("name", ["C:\\victim\\pwned", "..\\..\\victim\\pwned", "mao_pro\\..\\..\\pwned"])
def test_a_windows_path_is_not_treated_as_an_innocent_filename(models, tmp_path, name):
    """A backslash is a separator on the platform the archive was built for, and a drive letter is an
    absolute path there. Judging them by POSIX rules installs an attack under a puzzling filename."""
    arc = _zip(tmp_path / "win.zip", [*MODEL, (name, b"owned")])
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(arc)


def test_a_symlink_entry_is_refused_and_never_materialises(models, tmp_path):
    """A symlink inside a directory served over HTTP is a file read: `GET /api/models/raw/<dir>/link`
    would answer with /etc/passwd. Refused whole — an archive carrying one is not a mispacked model."""
    link = zipfile.ZipInfo("mao_pro/runtime/passwd.png")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    arc = tmp_path / "link.zip"
    with zipfile.ZipFile(arc, "w") as z:
        for n, d in MODEL:
            z.writestr(n, d)
        z.writestr(link, "/etc/passwd")
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(arc)
    assert list(models.iterdir()) == []


def test_an_entry_that_declares_a_bomb_is_refused_before_a_byte_is_written(models, tmp_path, monkeypatch):
    monkeypatch.setattr(model_install, "_MAX_UNPACKED_BYTES", 8192)
    arc = _zip(tmp_path / "bomb.zip", [*MODEL, ("mao_pro/runtime/big.png", b"\0" * 200_000)])
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(arc)
    assert list(models.iterdir()) == []


def test_entries_that_are_each_small_but_together_are_not(models, tmp_path, monkeypatch):
    """The cap is on the TOTAL: a bomb split across a hundred honest-looking entries is the same bomb."""
    monkeypatch.setattr(model_install, "_MAX_UNPACKED_BYTES", 8192)
    many = [(f"mao_pro/runtime/t{i:03d}.png", b"\0" * 1000) for i in range(40)]
    arc = _zip(tmp_path / "many.zip", [*MODEL, *many])
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(arc)


def test_a_header_that_lies_about_its_size_installs_nothing(models, tmp_path, monkeypatch):
    """The declared size is the attacker's word. When it lies small the entry fails its own CRC on the
    way out, and that has to end as a refusal with an empty models directory — never a 500 and never a
    half-written model the app then serves."""
    arc = _zip(tmp_path / "liar.zip", [*MODEL, ("mao_pro/runtime/t.png", b"\0" * 100_000)])
    real = zipfile.ZipFile.infolist

    def _lying(self):
        infos = real(self)
        for i in infos:
            i.file_size = 1
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", _lying)
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(arc)
    assert list(models.iterdir()) == []


def test_an_archive_with_too_many_entries_is_refused(models, tmp_path, monkeypatch):
    monkeypatch.setattr(model_install, "_MAX_ENTRIES", 10)
    arc = _zip(tmp_path / "swarm.zip", [*MODEL] + [(f"mao_pro/runtime/e{i}.png", b"x") for i in range(20)])
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(arc)


@pytest.mark.parametrize("name", ["evil.html", "evil.js", "evil.svg", "run.sh", "payload.exe", "inner.zip"])
def test_nothing_a_browser_would_run_is_ever_written_to_disk(models, tmp_path, name):
    """The serving filter refuses these on the way OUT. That is not a reason to keep them: the folder
    is the person's own machine, and one day something else reads it."""
    arc = _zip(tmp_path / "mixed.zip", [*MODEL, (f"mao_pro/runtime/{name}", b"<script>1</script>")])
    got = model_install.install_zip(arc)
    assert not (models / got["dir"] / "runtime" / name).exists()
    assert got["skipped"] == 1


def test_everything_the_app_serves_is_something_the_installer_writes():
    """The two lists may differ in ONE direction. A type the app serves but the installer drops is a
    model that installs and then 404s partway through loading, with nothing to see."""
    assert set(model_library._SERVABLE) <= model_install._INSTALLABLE


def test_an_archive_of_nothing_but_refused_files_is_not_a_model(models, tmp_path):
    arc = _zip(tmp_path / "junk.zip", [("a/index.html", b"<b>"), ("a/app.js", b"1")])
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(arc)
    assert list(models.iterdir()) == []


def test_an_archive_with_no_entry_file_is_not_a_model(models, tmp_path):
    arc = _zip(tmp_path / "textures.zip", [("a/texture_00.png", b"\x89PNG"), ("a/notes.txt", b"hi")])
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(arc)
    assert list(models.iterdir()) == []


def test_an_archive_that_contradicts_its_own_folders_is_a_bad_archive_not_a_bad_disk(models, tmp_path):
    arc = _zip(tmp_path / "clash.zip", [*MODEL, ("mao_pro/runtime/x.png", b"1"),
                                        ("mao_pro/runtime/x.png/y.png", b"2")])
    with pytest.raises(model_install.InstallError) as e:
        model_install.install_zip(arc)
    assert e.value.status == 400


def test_something_that_is_not_a_zip_at_all_is_refused(models, tmp_path):
    junk = tmp_path / "not.zip"
    junk.write_bytes(b"just some bytes")
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(junk)


def test_a_refused_archive_leaves_nothing_half_written(models, tmp_path):
    """Atomicity is the difference between a failed install and an install nobody can diagnose."""
    arc = _zip(tmp_path / "evil.zip", [*MODEL, ("../victim/pwned", b"owned")])
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(arc)
    assert list(models.iterdir()) == []


def test_the_installed_directory_is_named_after_the_entry_file(models, tmp_path):
    got = model_install.install_zip(_zip(tmp_path / "m.zip", MODEL))
    assert got == {"dir": "mao_pro", "entry": "runtime/mao_pro.model3.json",
                   "replaced": False, "skipped": 0, "skipped_kinds": [],
                   "files": 3, "bytes": sum(len(d) for _, d in MODEL),
                   "path": str(models / "mao_pro")}
    assert (models / "mao_pro" / "runtime" / "mao_pro.moc3").is_file()


def test_the_receipt_counts_what_landed_and_not_what_the_archive_offered(models, tmp_path):
    """The size and the count are a receipt somebody reads on their first run, so they have to be
    measured on the tree that is about to be published: dropped entries are not installed bytes, and
    a wrapper folder the installer discards is not an installed file."""
    junk = [("mao_en/notes.html", b"<b>dropped</b>" * 40), ("mao_en/thumb.svg", b"<svg/>" * 40)]
    arc = _zip(tmp_path / "r.zip", [(f"mao_en/{n}", d) for n, d in MODEL] + junk)
    got = model_install.install_zip(arc)
    assert got["files"] == 3
    assert got["bytes"] == sum(len(d) for _, d in MODEL)
    assert got["path"] == str(models / "mao_pro")
    assert sum(p.stat().st_size for p in (models / "mao_pro").rglob("*") if p.is_file()) == got["bytes"]


def test_the_wrapper_folders_an_archive_happens_to_carry_are_dropped(models, tmp_path):
    """A folder named for the download, wrapped around the model's own folder, may not change where
    the model lands, or `avatar_model` cannot point at it."""
    wrapped = [(f"mao_en/{n}", d) for n, d in MODEL]
    got = model_install.install_zip(_zip(tmp_path / "w.zip", wrapped))
    assert got["dir"] == "mao_pro"
    assert got["entry"] == "runtime/mao_pro.model3.json"


def test_a_flat_archive_installs_with_its_entry_at_the_top(models, tmp_path):
    flat = [("free1.model3.json", b"{}"), ("free1.moc3", b"MOC3"), ("texture_00.png", b"\x89PNG")]
    got = model_install.install_zip(_zip(tmp_path / "f.zip", flat))
    assert got["dir"] == "free1"
    assert got["entry"] == "free1.model3.json"
    assert (models / "free1" / "texture_00.png").is_file()


def test_a_model_whose_name_would_be_a_path_gets_a_plain_folder_name(models, tmp_path):
    odd = [("pack/../weird name!.model3.json", b"{}")]
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(_zip(tmp_path / "odd.zip", odd))
    ok = [("weird name!/weird name!.model3.json", b"{}")]
    got = model_install.install_zip(_zip(tmp_path / "ok.zip", ok))
    assert got["dir"] == "weird-name"
    assert (models / "weird-name").is_dir()


def test_installing_the_same_model_twice_leaves_exactly_one_copy(models, tmp_path):
    model_install.install_zip(_zip(tmp_path / "a.zip", MODEL))
    second = [*MODEL, ("mao_pro/runtime/texture_01.png", b"\x89PNG")]
    got = model_install.install_zip(_zip(tmp_path / "b.zip", second))
    assert got["replaced"] is True
    assert [p.name for p in models.iterdir() if p.is_dir()] == ["mao_pro"]
    assert (models / "mao_pro" / "runtime" / "texture_01.png").is_file()


def test_a_failed_reinstall_leaves_the_model_that_was_already_there(models, tmp_path):
    model_install.install_zip(_zip(tmp_path / "a.zip", MODEL))
    with pytest.raises(model_install.InstallError):
        model_install.install_zip(_zip(tmp_path / "evil.zip", [*MODEL, ("../victim/pwned", b"x")]))
    assert (models / "mao_pro" / "runtime" / "mao_pro.model3.json").is_file()


def test_an_install_that_cannot_get_the_lock_says_so_instead_of_crashing(models, tmp_path, monkeypatch):
    """Two people, or two clicks: the swap is serialized, and waiting forever is not an answer."""
    def _busy(_path):
        raise TimeoutError("held")

    monkeypatch.setattr(model_install, "exclusive", _busy)
    with pytest.raises(model_install.InstallError) as e:
        model_install.install_zip(_zip(tmp_path / "m.zip", MODEL))
    assert e.value.status == 409
    assert list(models.iterdir()) == []


def test_what_was_installed_is_what_the_library_then_offers(models, tmp_path):
    """The install is only finished when the thing that draws her face can see it."""
    got = model_install.install_zip(_zip(tmp_path / "m.zip", MODEL))
    assert model_library.installed() == [{"dir": got["dir"], "entry": got["entry"]}]
    assert model_library.resolve(f"{got['dir']}/{got['entry']}") is not None


def test_a_directory_being_staged_is_never_offered_as_an_installed_model(models):
    staged = models / ".install-abc" / "mao_pro"
    staged.mkdir(parents=True)
    (staged / "mao_pro.model3.json").write_text("{}", encoding="utf-8")
    assert model_library.installed() == []


def test_the_licence_that_travels_with_a_model_is_kept(models, tmp_path):
    arc = _zip(tmp_path / "lic.zip", [*MODEL, ("mao_pro/LICENSE.txt", b"free material licence")])
    got = model_install.install_zip(arc)
    assert (models / got["dir"] / "LICENSE.txt").is_file()


class _Resp:
    def __init__(self, status=200, headers=None, chunks=(b"",)):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self, chunk_size=None):
        for c in self._chunks:
            yield c


def _client(responses):
    seen = []

    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, **k):
            seen.append(url)
            return responses[len(seen) - 1]

    return _C, seen


@pytest.fixture
def offline(monkeypatch):
    """A literal public address, so the guard under test resolves nothing: with a hostname here the
    suite's verdict would depend on the machine having DNS."""
    url = "https://93.184.216.34/mao_en.zip"
    monkeypatch.setenv("KOTOBA_DEFAULT_MODEL_URL", url)
    return url


def test_the_address_we_ship_is_live2ds_own_over_https():
    assert model_install.DEFAULT_MODEL["url"].startswith("https://cubism.live2d.com/")
    assert model_install.DEFAULT_MODEL["licence"].startswith("https://www.live2d.com/")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9777/api/settings",
    "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd",
])
def test_the_download_refuses_an_address_pointing_into_the_network(models, monkeypatch, url):
    """The address is fixed, but an operator can repoint it at a mirror — and a mirror on the loopback
    or the metadata address is the SSRF the rest of the app already refuses."""
    monkeypatch.setenv("KOTOBA_DEFAULT_MODEL_URL", url)
    with pytest.raises(model_install.InstallError):
        asyncio.run(model_install.install_default())


def test_a_redirect_into_the_network_is_not_followed(models, monkeypatch, offline):
    """The first address being safe says nothing about where it sends us."""
    fake, seen = _client([_Resp(302, {"location": "http://127.0.0.1:9777/api/settings"})])
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    with pytest.raises(model_install.InstallError):
        asyncio.run(model_install.install_default())
    assert seen == [offline]


def test_a_download_stops_at_the_size_cap(models, monkeypatch, offline):
    monkeypatch.setattr(model_install, "_MAX_ARCHIVE_BYTES", 4096)
    fake, _ = _client([_Resp(200, {}, chunks=[b"\0" * 2048] * 100)])
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    with pytest.raises(model_install.InstallError):
        asyncio.run(model_install.install_default())
    assert list(models.iterdir()) == []


def test_a_download_that_announces_a_huge_body_is_refused_before_it_is_read(models, monkeypatch, offline):
    monkeypatch.setattr(model_install, "_MAX_ARCHIVE_BYTES", 4096)
    fake, _ = _client([_Resp(200, {"content-length": "99999999"}, chunks=[b"x"])])
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    with pytest.raises(model_install.InstallError):
        asyncio.run(model_install.install_default())


def test_the_default_download_installs_what_it_fetched(models, monkeypatch, offline):
    payload = _bytes(MODEL)
    fake, _ = _client([_Resp(200, {}, chunks=[payload[:20], payload[20:]])])
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    got = asyncio.run(model_install.install_default())
    assert got["dir"] == "mao_pro"
    assert model_library.installed() == [{"dir": "mao_pro", "entry": "runtime/mao_pro.model3.json"}]


def test_a_download_that_answers_with_an_error_is_not_installed(models, monkeypatch, offline):
    fake, _ = _client([_Resp(404, {}, chunks=[b"nope"])])
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    with pytest.raises(model_install.InstallError):
        asyncio.run(model_install.install_default())


def test_an_upload_stops_at_the_size_cap(models, monkeypatch):
    monkeypatch.setattr(model_install, "_MAX_ARCHIVE_BYTES", 4096)

    async def _flood():
        for _ in range(100):
            yield b"\0" * 2048

    with pytest.raises(model_install.InstallError):
        asyncio.run(model_install.install_stream(_flood()))
    assert list(models.iterdir()) == []


def test_an_uploaded_archive_is_unpacked_by_the_same_installer(models):
    payload = _bytes(MODEL)

    async def _chunks():
        yield payload[:10]
        yield payload[10:]

    got = asyncio.run(model_install.install_stream(_chunks()))
    assert got["dir"] == "mao_pro"


def test_an_uploaded_archive_gets_the_same_refusals_as_a_downloaded_one(models):
    payload = _bytes([*MODEL, ("../victim/pwned", b"owned")])

    async def _chunks():
        yield payload

    with pytest.raises(model_install.InstallError):
        asyncio.run(model_install.install_stream(_chunks()))


def test_an_upload_that_announces_a_huge_body_is_refused_before_it_is_read(models, monkeypatch):
    monkeypatch.setattr(model_install, "_MAX_ARCHIVE_BYTES", 4096)
    read = False

    async def _chunks():
        nonlocal read
        read = True
        yield b"x"

    with pytest.raises(model_install.InstallError):
        asyncio.run(model_install.install_stream(_chunks(), declared="99999999"))
    assert read is False


@pytest.fixture
def client(models):
    from fastapi.testclient import TestClient

    import kotoba.server as main

    main._FAILED_AUTH.clear()
    with TestClient(main.app) as c:
        yield c


def test_installing_from_the_web_ends_with_her_wearing_it(client, models):
    """An installed model nobody selected looks to the person like nothing happened."""
    r = client.post("/api/models/install/upload", content=_bytes(MODEL))
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "mao_pro/runtime/mao_pro.model3.json"
    assert client.get("/api/avatar").json()["selected"]["dir"] == "mao_pro"


def test_a_hostile_upload_is_refused_at_the_door_in_words(client, models, tmp_path):
    r = client.post("/api/models/install/upload", content=_bytes([*MODEL, ("../victim/pwned", b"x")]))
    assert r.status_code == 400
    assert "outside" in r.json()["detail"]
    assert not _outside(tmp_path).exists()
    assert client.get("/api/avatar").json()["installed"] == []


def test_an_upload_over_the_cap_is_told_so(client, models, monkeypatch):
    monkeypatch.setattr(model_install, "_MAX_ARCHIVE_BYTES", 4096)
    r = client.post("/api/models/install/upload", content=b"\0" * 8192)
    assert r.status_code == 413


def test_the_default_is_not_fetched_until_its_licence_is_accepted(client, models, monkeypatch):
    """The whole reason no model ships is that we may not redistribute one, so the person accepts
    Live2D's terms on their own machine before their machine goes and gets it."""
    called = False

    class _Never:
        def __init__(self, *a, **k):
            nonlocal called
            called = True

    monkeypatch.setattr(httpx, "AsyncClient", _Never)
    r = client.post("/api/models/install/default", json={})
    assert r.status_code == 400
    assert model_install.DEFAULT_MODEL["licence"] in r.json()["detail"]
    assert called is False


def test_the_offer_names_the_model_and_its_licence(client):
    d = client.get("/api/models/default").json()
    assert d["url"] == model_install.DEFAULT_MODEL["url"]
    assert d["licence"] and d["name"]


def test_the_credential_that_draws_a_face_cannot_install_one(models, monkeypatch):
    """`km` is minted for reading a model's files. The installer lives under the same first segment
    and must not inherit it — that cookie travels with every texture fetch."""
    import hashlib
    import hmac

    from fastapi.testclient import TestClient

    import kotoba.server as main

    pw = "pw-for-tests"
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", pw)
    main._FAILED_AUTH.clear()
    with TestClient(main.app) as c:
        c.cookies.set("km", hmac.new(pw.encode(), b"kotoba-models-viewer", hashlib.sha256).hexdigest())
        assert c.post("/api/models/install/upload", content=_bytes(MODEL)).status_code == 401
        assert c.post("/api/models/install/default", json={"accept_license": True}).status_code == 401
    assert list(models.iterdir()) == []


def test_the_worn_model_is_measured_on_disk_so_the_card_can_state_it(client, models):
    """The first-run card names a file count and a size. Those came only from the install that had
    just run, so a reload or a reconfigure — every visit but one — showed a card with the row missing.
    The disk is the answer that survives, and it is measured for the WORN model alone: nothing states
    a size for the others, and this walk is per request."""
    client.post("/api/models/install/upload", content=_bytes(MODEL))
    worn = client.get("/api/avatar").json()["selected"]
    assert worn["dir"] == "mao_pro"
    assert worn["files"] == len(MODEL)
    assert worn["bytes"] == sum(len(data) for _, data in MODEL)


def test_measuring_a_model_that_is_not_there_answers_zero_rather_than_raising(models):
    """`/api/avatar` runs on every app start; a folder deleted from under it is a Tuesday."""
    assert model_library.measure("gone") == {"files": 0, "bytes": 0}
    assert model_library.measure("") == {"files": 0, "bytes": 0}


def test_a_symlinked_directory_is_not_walked_into_when_measuring(models, tmp_path):
    """The same rule `_entry_of` follows: somebody else's unpacked folder is not ours to trust, and a
    loop through one would hang the request that draws the card."""
    (models / "m").mkdir()
    (models / "m" / "m.model3.json").write_bytes(b'{"Version": 3}')
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "big.png").write_bytes(b"x" * 4096)
    try:
        make_symlink(models / "m" / "link", tmp_path / "elsewhere", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform does not make symlinks here")
    assert model_library.measure("m") == {"files": 1, "bytes": 14}
