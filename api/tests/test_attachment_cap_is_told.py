"""Hitting the attachment cap is an answer, not a silence.

The cap is 4 and `add()` returned nothing, so the fifth file was dropped while the endpoint answered
`{"ok": true}`. The PDF case was the bad one: an image at least reached `_keep_shared` somewhere, but
a PDF left no trace anywhere, and the person told the document attached got an answer written as if
it had never been sent.

The cap itself is fine — four files of up to 14 MB each is already plenty of turn context. What was
not fine is the silence: nothing written now means a non-2xx carrying a `detail`, never a 200 with a
falsy field, and a refused part is kept OUT of `_shared` so it cannot still reach `remember_image`."""
from __future__ import annotations

import pytest

from kotoba.core import attachments

_AUTH = {"Authorization": "Bearer k"}
_PDF = "data:application/pdf;base64,JVBERi0xLjQK"
_IMG = "data:image/png;base64,AAAA"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "a.db"))
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "")
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    from fastapi.testclient import TestClient
    import kotoba.server as server

    attachments._pending.clear()
    attachments._shared.clear()
    with TestClient(server.app) as c:
        yield c
    attachments._pending.clear()
    attachments._shared.clear()


def test_add_reports_whether_it_kept_the_part():
    """The verdict the caller never had: True while there is room, False the moment the cap refuses."""
    attachments._pending.clear()
    kept = [attachments.add("s-verdict", {"type": "input_file", "filename": f"{i}.pdf"})
            for i in range(attachments.MAX_PER_SESSION + 2)]
    assert kept[:attachments.MAX_PER_SESSION] == [True] * attachments.MAX_PER_SESSION
    assert kept[attachments.MAX_PER_SESSION:] == [False, False], \
        "add() answered nothing, so no caller could tell a stashed file from a dropped one"
    assert len(attachments.take("s-verdict")) == attachments.MAX_PER_SESSION


def test_a_refused_image_is_not_kept_as_shared_either():
    """The overflow image used to reach `_keep_shared` anyway: refused from the turn, yet still the thing
    `resolve_image("attachment")` hands back. Told "it didn't attach", she could have kept it."""
    attachments._pending.clear()
    attachments._shared.clear()
    for i in range(attachments.MAX_PER_SESSION):
        attachments.add("s-shared", {"type": "input_image", "image_url": f"{_IMG}{i}"}, name=f"ok{i}.png")
    assert attachments.add("s-shared", {"type": "input_image", "image_url": _IMG + "X"},
                           name="refused.png") is False
    names = [e["name"] for e in attachments.shared_images("s-shared")]
    assert "refused.png" not in names, f"a file the person was told did not attach is still reachable: {names}"


def test_the_fifth_file_is_refused_and_says_why(client):
    """The reproduction, at the front door: five POSTs, five `{"ok": true}`, four parts, and a PDF gone
    without a trace."""
    for i in range(attachments.MAX_PER_SESSION):
        r = client.post("/api/session/cap/attachment",
                        json={"kind": "pdf", "data_url": _PDF, "name": f"doc{i}.pdf"}, headers=_AUTH)
        assert r.status_code == 200, r.text

    r = client.post("/api/session/cap/attachment",
                    json={"kind": "pdf", "data_url": _PDF, "name": "doc5.pdf"}, headers=_AUTH)
    assert r.status_code >= 400, f"a dropped document answered success: {r.status_code} {r.text}"
    detail = str(r.json().get("detail", ""))
    assert str(attachments.MAX_PER_SESSION) in detail, f"the answer must name the cap it hit: {detail!r}"
    assert detail.strip(), "a refusal with no sentence is the same silence one status code up"

    parts = attachments.take("cap")
    assert len(parts) == attachments.MAX_PER_SESSION
    assert [p.get("filename") for p in parts] == [f"doc{i}.pdf" for i in range(attachments.MAX_PER_SESSION)], \
        "the files that DID land must be the ones already reported as landed"


def test_the_pile_reopens_once_a_turn_has_taken_it(client):
    """The cap bounds one message, not the session: what `take()` drains makes room again."""
    for i in range(attachments.MAX_PER_SESSION):
        assert client.post("/api/session/again/attachment",
                           json={"kind": "image", "data_url": _IMG, "name": f"p{i}.png"},
                           headers=_AUTH).status_code == 200
    assert client.post("/api/session/again/attachment",
                       json={"kind": "image", "data_url": _IMG, "name": "over.png"},
                       headers=_AUTH).status_code >= 400
    attachments.take("again")
    assert client.post("/api/session/again/attachment",
                       json={"kind": "image", "data_url": _IMG, "name": "next.png"},
                       headers=_AUTH).status_code == 200
