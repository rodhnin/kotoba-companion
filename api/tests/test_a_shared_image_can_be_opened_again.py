"""An image the user hands her stays reachable for the rest of the conversation — and no further.

`attachments` shows the model base64 it can never quote back, and drops the part after one
turn — "what was in that picture?" three turns later had nowhere to go except `remember_image`,
which files it in her DURABLE visual memory. Looking again and keeping forever were the same
gesture, which would have turned every working screenshot into a keepsake.

The fix is a transient half: the bytes go into the Files library and the share is logged as a
session capture, so `view_capture` re-opens it later. The durable half does not move —
`remember_image` stays hers to call for the few that are really keepsakes."""
from __future__ import annotations

import asyncio
import base64
import io
from types import SimpleNamespace

import pytest

from kotoba.cli import slash
from kotoba.core import attachments, file_library, session_captures, visual_memory
from kotoba.tools.builtin import view_capture


PNG = b"\x89PNG\r\n\x1a\n" + b"kotoba-pixels" * 40


@pytest.fixture
def app(tmp_path):
    said: list[str] = []
    yield SimpleNamespace(screen=SimpleNamespace(chrome=said.append), said=said,
                          session=SimpleNamespace(session_id="s-share"), shared_n=0,
                          gifts=[], pending_send=[], prompt=SimpleNamespace(pending=""))
    attachments._pending.pop("s-share", None)
    attachments._shared.pop("s-share", None)
    session_captures.clear("s-share")


def _shot(tmp_path, name="clip-1.png"):
    p = tmp_path / name
    p.write_bytes(PNG)
    return str(p)


def test_the_shared_image_lands_in_files_and_in_this_sessions_captures(app, tmp_path):
    note = slash._carry(app, _shot(tmp_path), "clip-1.png", label="[Image #1]")

    assert note and "open it again later" in note
    stored = file_library.resolve(f"{slash.SHARED_DIR}/clip-1.png")
    assert stored is not None and stored.read_bytes() == PNG
    logged = session_captures.entries("s-share")
    assert [e["file"] for e in logged] == [f"{slash.SHARED_DIR}/clip-1.png"]
    assert "[Image #1]" in logged[0]["caption"] and "shared" in logged[0]["caption"]


def test_the_block_names_it_and_view_capture_gives_the_same_bytes_back(app, tmp_path):
    """The whole chain a later turn walks: the developer block names a file, and the tool opens it."""
    slash._carry(app, _shot(tmp_path), "clip-1.png", label="[Image #1]")
    attachments.take("s-share")   # the turn it rode in on has consumed the part

    block = session_captures.prompt_block("s-share")
    assert f"{slash.SHARED_DIR}/clip-1.png" in block and "view_capture" in block
    assert "the user shared" in block, "she must not be told she took this one herself"

    out = asyncio.run(view_capture.execute({"file": f"{slash.SHARED_DIR}/clip-1.png"}, None))
    assert out is not None and out.images
    assert base64.b64decode(out.images[0].split(",", 1)[1]) == PNG


def test_sharing_an_image_never_writes_to_her_durable_visual_memory(app, tmp_path):
    """A paste is not a keepsake. `remember_image` is the only road into visual memory and it stays a
    call SHE makes — every screenshot flowing in by default would poison recall_image within a week."""
    slash._carry(app, _shot(tmp_path), "clip-1.png", label="[Image #1]")

    assert visual_memory.all_entries() == []
    assert visual_memory.prompt_block() == ""
    assert not list(visual_memory.memory_dir().rglob("*.png"))


def test_a_library_that_refuses_the_copy_still_lets_her_see_it_now(app, tmp_path, monkeypatch):
    """Best-effort on purpose: a share she can see THIS turn is worth more than one she could also see
    later, so a refused save costs the second half and never the first."""
    monkeypatch.setattr(file_library, "save_image", lambda *a, **k: None)
    note = slash._carry(app, _shot(tmp_path), "clip-1.png", label="[Image #1]")

    assert note == "she can see this one"
    assert session_captures.entries("s-share") == []
    assert attachments.has("s-share")


def test_attach_seeds_a_marker_for_a_picture_and_keeps_a_verb_for_a_text_file(app, tmp_path):
    """`/attach` shares ctrl-v's road for anything she can SEE. A file only copied into the workdir keeps
    its verb — there the sentence is the whole instruction, nothing else tells her to open it.

    Both land in the BOX and wait for enter, never on `pending_send`, which bypasses the editor."""
    from kotoba.cli.render.caps import Caps
    from kotoba.cli.render.theme import GLYPHS_UNICODE

    app.screen = SimpleNamespace(chrome=app.said.append, separate=lambda: None, blank=lambda: None,
                                 row=lambda *a: None, rw=80)
    app.caps = Caps(color="none", background="dark", unicode=True, interactive=False, width=80,
                    g=dict(GLYPHS_UNICODE))
    slash._attach(app, _shot(tmp_path, "trace.png"))
    assert app.prompt.pending == "[Image #1]" and app.shared_n == 1

    app.prompt.pending = ""
    notes = tmp_path / "notes.txt"
    notes.write_text("hola")
    slash._attach(app, str(notes))
    assert app.prompt.pending == "read notes.txt" and app.pending_send == []
    assert app.shared_n == 1, "a file she cannot see does not take an image's number"


def test_the_gift_row_never_widens_past_the_terminal(app, tmp_path):
    """The note goes into a row `gift_rows` wraps; a longer one must not push it off the glass."""
    from kotoba.cli.render.caps import Caps
    from kotoba.cli.render.rows import gift_rows
    from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console
    from kotoba.cli import state

    note = slash._carry(app, _shot(tmp_path), "clip-1.png", label="[Image #1]")
    caps = Caps(color="none", background="dark", unicode=True, interactive=False, width=60,
                g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    console = build_console(caps, file=buf)
    for row in gift_rows(caps, state.Gift("sent", "clip-1.png", f"4 kB - {note}"), 1, 56):
        console.print(row)
    assert max((len(line) for line in buf.getvalue().splitlines()), default=0) <= 60


def test_what_the_cli_sends_is_what_context_reads_as_a_share_with_no_caption(app, tmp_path):
    """The two halves meeting: `slash.said` picks a token, `context` recognises it. Pinned
    together because a marker that stopped matching that branch would look identical on screen and quietly
    put her back to describing pictures in English."""
    import kotoba.core.context as context
    from kotoba.models.schemas import ChatRequest

    class _DB:
        async def fetch_recent_turns(self, *a, **k):
            return []

        async def fetch_soul_config(self):
            return {"name": "Kotoba", "language": "auto"}

        async def fetch_user_profile_as_markdown(self):
            return ""

    slash._carry(app, _shot(tmp_path), "clip-1.png", label="[Image #1]")
    sent = slash.said("[Image #1]", ["[Image #1]"])
    req = ChatRequest(messages=[{"role": "user", "content": sent}])
    items = asyncio.run(context.load_context(req, _DB(), "s-share"))

    assert all(sent not in str(m.get("content")) for m in items), "the sentinel is never shown to her"
    told = " ".join(m["content"] for m in items if m["role"] == "developer").lower()
    assert "no caption" in told and "language" in told
    last_user = [m for m in items if m["role"] == "user"][-1]
    assert [p["type"] for p in last_user["content"]] == ["input_image"]


def test_a_replayed_share_reads_as_a_marker_and_not_as_the_sentinel():
    """`/sessions` reads the row straight out of `turns`, and a share with no caption is stored as the
    token the attachment branch keys on. Printed raw it showed `__image_only__` to someone who had typed
    nothing at all — true of a web turn since long before the CLI ever sent one."""
    assert slash.shown("__image_only__") == "[Image]"
    assert slash.shown("__file_only__") == "[File]"
    assert slash.shown("que ves aqui?") == "que ves aqui?"
