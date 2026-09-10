"""ask_user detail field — the two-field card contract.

ask_user now accepts an optional `detail` string that is forwarded to the emitted `need_input`
frame so the frontend can show it behind a "See description" expander. A call without `detail`
must still work exactly as before. Approval frames are unaffected.
"""
from __future__ import annotations

import asyncio
import types

import kotoba.core.interaction as interaction
import kotoba.tools.builtin.ask_user as ask_user


def _ctx(mode: str = "companion"):
    return types.SimpleNamespace(session_id="det-sess", mode=mode)


def _patch_emits(monkeypatch):
    """Capture all emit_task calls; suppress emit_emotion."""
    emitted: list[tuple[str, dict]] = []

    async def fake_emit_task(session_id, kind, **data):
        emitted.append((kind, data))

    async def fake_emit_emotion(session_id, emotion):
        pass

    monkeypatch.setattr(interaction, "emit_task", fake_emit_task)
    monkeypatch.setattr(interaction, "emit_emotion", fake_emit_emotion)
    return emitted


def test_detail_flows_to_need_input_frame_companion(monkeypatch):
    """When ask_user is called with `detail`, the emitted need_input frame carries it."""
    emitted = _patch_emits(monkeypatch)
    ctx = _ctx("companion")
    asyncio.run(ask_user.execute(
        {"prompt": "Paste the repo URL", "detail": "The full https:// address of your GitHub repo."},
        ctx,
    ))
    need_input_frames = [d for kind, d in emitted if kind == "need_input" and d.get("mode") == "input"]
    assert need_input_frames, "need_input frame must be emitted"
    frame = need_input_frames[0]
    assert frame.get("label") == "Paste the repo URL"
    assert frame.get("detail") == "The full https:// address of your GitHub repo."


def test_no_detail_still_works_companion(monkeypatch):
    """A call without `detail` emits a need_input frame with no detail key (not null — omitted)."""
    emitted = _patch_emits(monkeypatch)
    ctx = _ctx("companion")
    out = asyncio.run(ask_user.execute({"prompt": "What's your name?"}, ctx))
    assert out  # still returns guidance
    need_input_frames = [d for kind, d in emitted if kind == "need_input" and d.get("mode") == "input"]
    assert need_input_frames, "need_input frame must be emitted"
    frame = need_input_frames[0]
    assert frame.get("label") == "What's your name?"
    assert "detail" not in frame  # omitted, not null


def test_detail_flows_to_need_input_frame_work_mode(monkeypatch):
    """In work mode, request_input is called and detail reaches it as a kwarg."""
    _patch_emits(monkeypatch)
    seen: dict = {}

    async def fake_request_input(sid, label, kind, *, timeout=None, detail=None, card=None):
        seen.update(sid=sid, label=label, kind=kind, detail=detail, card=card)
        return "typed-value"

    monkeypatch.setattr(interaction, "request_input", fake_request_input)

    ctx = _ctx("work")
    out = asyncio.run(ask_user.execute(
        {"prompt": "Enter your email", "detail": "The address tied to your account."},
        ctx,
    ))
    assert seen.get("label") == "Enter your email"
    assert seen.get("detail") == "The address tied to your account."
    assert "typed-value" in out, "the typed value must be handed back to the model"


def test_approval_frame_unaffected(monkeypatch):
    """request_approval emits a need_input{mode:approval} frame — it must not carry a detail field."""
    emitted = _patch_emits(monkeypatch)
    monkeypatch.setattr(interaction, "_await_response", lambda *a, **k: asyncio.sleep(0))

    async def go():
        # request_approval refuses to open a card into a session with no listener.
        from kotoba.core import events
        events.register("det-sess")
        try:
            await interaction.request_approval("det-sess", "rm -rf build", timeout=0.01)
        finally:
            events.event_queues.pop("det-sess", None)

    asyncio.run(go())
    approval_frames = [d for kind, d in emitted if kind == "need_input" and d.get("mode") == "approval"]
    assert approval_frames, "approval frame must be emitted"
    assert "detail" not in approval_frames[0]


# --- the title is a FIELD LABEL, enforced in code -------------------------------------------------

from kotoba.tools.builtin.ask_user import _TITLE_MAX, _as_field_label


def test_an_explanatory_prompt_is_reduced_to_its_first_sentence():
    """The schema asks for a label and the model still writes a paragraph; the card gets a label anyway,
    and the reasoning survives as expandable detail rather than being dropped."""
    title, detail = _as_field_label(
        "Escribe tu código postal aquí, por favor. Lo usaré solo para adaptar la información o el "
        "servicio a tu zona, como mostrarte opciones, horarios o disponibilidad más precisos.",
        None,
    )
    assert title == "Escribe tu código postal aquí, por favor."
    assert detail and detail.startswith("Lo usaré solo para adaptar")
    assert "horarios o disponibilidad más precisos." in detail, "nothing the model wrote is lost"


def test_a_short_label_is_left_exactly_as_written():
    assert _as_field_label("Your postal code", None) == ("Your postal code", None)


def test_an_unpunctuated_run_on_is_clipped_and_the_tail_kept():
    long = ("Necesito que me escribas ahora mismo la dirección postal completa incluyendo calle numero "
            "piso y ciudad sin abreviar nada")
    title, detail = _as_field_label(long, None)
    assert len(title) <= _TITLE_MAX + 1     # +1 for the ellipsis
    assert title.endswith("…")
    assert detail and detail.endswith("sin abreviar nada")
    assert not title.rstrip("…").endswith(" "), "clip on a word boundary, no trailing space"


def test_an_explicit_detail_is_kept_after_the_overflow():
    title, detail = _as_field_label("Paste the link here. It must be the public one.", "Goes to research/.")
    assert title == "Paste the link here."
    assert detail == "It must be the public one. Goes to research/."


def test_empty_and_whitespace_prompts_do_not_crash():
    assert _as_field_label("", None) == ("", None)
    assert _as_field_label("   \n  ", "ctx") == ("", "ctx")
