"""The exact grant on every surface a person actually touches.

The rule lives in `core/approval`; this file proves the wiring
around it — the answer the WEB card posts, the row the agentic loop writes and reads back, the key the
CLI draws, and the Settings listing that has to tell a one-line grant from a whole-family one before
somebody decides which to take back.

The withheld-reason half is here too, because a card that hides an option without saying why is the
defect that started this: it read as broken, to the person it was built for, on the surface he uses.
"""
from __future__ import annotations

import asyncio
import io
import re
from types import SimpleNamespace

from conftest import needs_posix_terminal
from kotoba.cli import approvals, settings_view, slash
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render import cards
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console
from kotoba.core import events, interaction
from kotoba.core.approval import ApprovalGate
from kotoba.core.loop import _grant_note

WRAPPED = "sh -c \"sleep 15 && echo 'terminado a los quince'\""


class _DB:
    """`save_approved_command` / `list_approved_commands` with the scope column that tells them apart."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    async def save_approved_command(self, pattern, scope="command"):
        self.rows = [r for r in self.rows if r["pattern"] != pattern]
        self.rows.append({"pattern": pattern, "scope": scope})

    async def list_approved_commands(self):
        return list(self.rows)

    async def delete_approved_command(self, pattern):
        self.rows = [r for r in self.rows if r["pattern"] != pattern]


def _caps(width: int = 90) -> Caps:
    return Caps(color="none", background="dark", unicode=True, interactive=True, width=width,
                g=dict(GLYPHS_UNICODE))


def _painted(card: cards.Approval, width: int = 76) -> str:
    caps = _caps(width)
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    for row in cards.approval_rows(caps, card, width - 4):
        screen.row(row)
    return buf.getvalue()


def _flat(out: str) -> str:
    """The card's words as one line: the widow-balancing wrap may break a sentence anywhere, and these
    asserts are about what the panel SAYS, not where its rows break — nor how it paints them. The key
    glyph carries its own style span, so `t always allow ...` exists as a phrase and never as a raw
    substring; asserting on the painted bytes made these tests pass or fail by terminal capability."""
    return re.sub(r"\s+", " ", re.sub(r"\x1b\[[0-9;]*m", "", out).replace("█", " "))


# --- the answer the web card posts ------------------------------------------------------------------

def test_the_body_the_web_card_posts_lands_as_an_exact_grant_in_the_database():
    """`{approved, always, always_exact}` is the whole contract across that seam. It is asserted here and
    not in the browser because the panel has no seam a unit test can reach — but the KEY NAME does."""
    db = _DB()
    sid = "web-exact"
    gate: dict = {}

    async def go():
        async def ask(action, risk, fam):
            card: dict = {}
            answered = asyncio.create_task(_answer_the_card(sid, card))
            approved, always = await interaction.request_approval(
                sid, action, channel="text", family=fam, card=card, timeout=2.0)
            await answered
            return (approved, always, bool(card.get("always_exact")))

        async def persist_exact(cmd):
            await db.save_approved_command(cmd, "exact")

        gate["g"] = ApprovalGate(host_exec=True, workspace_root=None, ask=ask,
                                 on_persist_exact=persist_exact,
                                 on_persist=lambda fam: db.save_approved_command(fam, "command"))
        q = events.register(sid)
        try:
            return await gate["g"].confirm(WRAPPED, "exec"), q
        finally:
            events.unregister(sid)

    async def _answer_the_card(session_id, card):
        for _ in range(200):
            if card.get("request_id"):
                break
            await asyncio.sleep(0.005)
        interaction.resolve(session_id,
                            {"approved": True, "always": False, "always_exact": True},
                            card.get("request_id"))

    ok, _q = asyncio.run(go())
    assert ok is True
    assert db.rows == [{"pattern": WRAPPED, "scope": "exact"}]
    assert gate["g"].would_auto_allow(WRAPPED, "exec") is True
    assert gate["g"].is_saved("sh") is False


def test_a_web_answer_that_claims_both_grants_gets_the_one_it_was_offered():
    """Only one key can be pressed. A body claiming both is either a stale client or a forged one, and
    the family grant it names is the one the card actually showed — the narrower flag is dropped rather
    than saved as a second row nobody asked for."""
    db = _DB()

    async def go():
        async def ask(action, risk, fam):
            return (True, True, True)

        gate = ApprovalGate(
            host_exec=True, ask=ask,
            on_persist=lambda fam: db.save_approved_command(fam, "command"),
            on_persist_exact=lambda cmd: db.save_approved_command(cmd, "exact"))
        await gate.confirm("npm run build", "exec")
        return gate

    gate = asyncio.run(go())
    assert db.rows == [{"pattern": "npm", "scope": "command"}]
    assert gate.is_saved_exact("npm run build") is False


# --- what the loop stores and reads back ------------------------------------------------------------

def test_the_two_scopes_seed_two_different_sets_and_do_not_leak_into_each_other():
    db = _DB([{"pattern": "git", "scope": "command"},
              {"pattern": WRAPPED, "scope": "exact"}])
    rows = asyncio.run(db.list_approved_commands())
    families = {r["pattern"] for r in rows if r.get("scope") != "exact"}
    exact = {r["pattern"] for r in rows if r.get("scope") == "exact"}
    gate = ApprovalGate(host_exec=True, saved_commands=families, saved_exact=exact)

    assert gate.would_auto_allow("git status", "exec") is True
    assert gate.would_auto_allow(WRAPPED, "exec") is True
    assert gate.would_auto_allow("sh -c 'cat /etc/shadow'", "exec") is False
    assert gate.would_auto_allow(WRAPPED + " ", "exec") is False


def test_the_landed_row_never_reports_a_family_grant_the_person_never_gave():
    """`sh` is a family the gate refuses to save at all, so a row reading "you always allow sh" would be
    describing a permission that cannot exist. The width of the grant is part of the receipt."""
    ctx = SimpleNamespace(_grants={"c1": ("saved", "git"), "c2": ("saved-exact", "sh"), "c3": "npm"})
    assert _grant_note(ctx, "c1") == "you always allow git"
    assert _grant_note(ctx, "c2") == "you always allow this exact command"
    assert _grant_note(ctx, "c3") == "you always allow npm"
    assert _grant_note(ctx, "nope") == ""


# --- the terminal card ------------------------------------------------------------------------------

def test_the_terminal_card_offers_t_where_it_cannot_offer_a_and_says_why():
    note = "“sh” only names what runs it, not what runs — saving that would allow anything."
    out = _painted(cards.Approval(WRAPPED, "", "sh", can_always=False, can_always_exact=True,
                                  always_note=note, show_why=True))
    assert "t always allow just this line" in _flat(out)
    assert "always allow sh" not in _flat(out)
    assert "only names what runs it" in _flat(out)
    assert "t means yes to this exact line from now on" in _flat(out)


def test_a_card_that_can_offer_both_offers_both_and_names_each_reach():
    out = _painted(cards.Approval("npm run build", "", "npm", can_always=True, can_always_exact=True,
                                  show_why=True), width=100)
    assert "a always allow npm" in _flat(out) and "t always allow just this line" in _flat(out)
    assert "a means yes to every npm from now on" in _flat(out)
    assert "t means yes to this exact line from now on" in _flat(out)


def test_a_card_that_can_offer_neither_draws_neither_key_and_says_it_once():
    """The `?` panel already names the danger in `why`; repeating the backend's sentence under it would
    tell the same person the same thing twice on the one card that must stay readable."""
    note = "It's a recursive delete, so it asks every time — this one can't be saved."
    out = _painted(cards.Approval("rm -rf /tmp/build", "recursive-delete", "rm", can_always=False,
                                  always_note=note, show_why=True))
    keys_row = next(line for line in out.split("\n") if "go ahead" in line)
    assert "always" not in keys_row and "just this line" not in keys_row
    assert "recursive delete" in out and "asks every time" not in out


def test_the_flash_names_only_the_keys_this_card_actually_drew():
    import time

    for can_a, can_t, expect in ((False, False, "y, n or ?"), (True, False, "y, a, n or ?"),
                                 (False, True, "y, t, n or ?"), (True, True, "y, a, t, n or ?")):
        card = cards.Approval("npm run build", "", "npm", can_always=can_a, can_always_exact=can_t)
        card.flash_until = time.monotonic() + 5
        assert expect in _painted(card, width=100)


@needs_posix_terminal
def test_a_t_pressed_on_a_card_that_cannot_persist_it_is_discarded_with_the_flash(monkeypatch):
    """Same rule as `a`: a key the rail never drew is junk typed at a safety gate, never a decision."""
    from kotoba.cli.input import keys as keymod

    caps = _caps(76)
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    drawn = cards.Approval("rm -rf /tmp/build", "recursive-delete", "rm",
                           can_always=False, can_always_exact=False)
    app.approval = drawn
    reads = iter(["t", "a", "y"])
    monkeypatch.setattr(keymod, "read_input", lambda timeout=0.25: next(reads, ""))
    assert app._read_approval(drawn) == "y"
    assert drawn.flash_until > 0.0


def test_the_answer_is_clamped_to_what_the_card_was_allowed_to_offer():
    quiet = approvals.Approvals.__new__(approvals.Approvals)
    offered = approvals.Card(request_id="r", mode="approval", label=WRAPPED, family="sh",
                             can_always=False, can_always_exact=True)
    withheld = approvals.Card(request_id="r", mode="approval", label="rm -rf /tmp/x", family="rm",
                              can_always=False, can_always_exact=False)
    assert quiet._value(offered, (True, False, True)) == {
        "approved": True, "always": False, "always_exact": True}
    assert quiet._value(offered, (True, True, False)) == {
        "approved": True, "always": False, "always_exact": False}
    assert quiet._value(withheld, (True, True, True)) == {
        "approved": True, "always": False, "always_exact": False}
    assert quiet._value(offered, (True, False)) == {
        "approved": True, "always": False, "always_exact": False}


def test_the_plain_terminal_prompt_offers_t_and_prints_the_missing_reason(monkeypatch, capsys):
    """The question goes to stderr, never stdout: `--once` puts her answer there, and a card opened
    into it arrived glued to her reply on the same line."""
    card = approvals.Card(request_id="r", mode="approval", label=WRAPPED, family="sh",
                          can_always=False, can_always_exact=True,
                          always_note="“sh” only names what runs it, not what runs.")
    monkeypatch.setattr("builtins.input", lambda: "t")
    assert approvals.ask_at_terminal(card) == (True, False, True)
    out, err = capsys.readouterr()
    assert "[y/N/t]" in err and "only names what runs it" in err
    assert out == "", f"the card landed where her answer goes: {out!r}"


# --- Settings ---------------------------------------------------------------------------------------

def test_settings_tells_a_one_line_grant_from_a_whole_family():
    assert settings_view.grant_permits("git", "command") == "runs git without asking"
    assert settings_view.grant_permits(WRAPPED, "exact") == (
        "runs this exact line without asking — nothing else")
    rows = settings_view.section_info("SECURITY", {
        "security": {"trust": "workspace",
                     "approvals": [{"pattern": "git", "scope": "command"},
                                   {"pattern": WRAPPED, "scope": "exact"}]},
        "browser_cdp": ""})
    assert (WRAPPED, "runs this exact line without asking — nothing else", "") in rows


def _cli(rows) -> tuple[App, io.StringIO, _DB]:
    caps = _caps(90)
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    db = _DB(rows)
    app.session = SimpleNamespace(session_id="s1", engine=SimpleNamespace(db=db))
    return app, buf, db


def _said(app, arg: str = "") -> str:
    asyncio.run(slash.run(app, commands.Command("/approvals", arg)))
    return app.screen.console.file.getvalue()


@needs_posix_terminal
def test_the_terminal_listing_shows_an_exact_grant_as_the_one_line_it_is():
    app, _, _ = _cli([{"pattern": "git", "scope": "command"},
                      {"pattern": WRAPPED, "scope": "exact"}])
    out = _said(app)
    assert "runs git without asking" in out
    assert "runs this exact line without asking" in out


@needs_posix_terminal
def test_an_exact_grant_can_be_revoked_by_its_number_because_retyping_it_is_not_an_option():
    app, _, db = _cli([{"pattern": "git", "scope": "command"},
                       {"pattern": WRAPPED, "scope": "exact"}])
    out = _said(app, "rm 2")
    assert "revoked" in out
    assert asyncio.run(db.list_approved_commands()) == [{"pattern": "git", "scope": "command"}]


@needs_posix_terminal
def test_revoking_by_name_still_works_and_a_number_nobody_listed_does_not():
    app, _, db = _cli([{"pattern": "git", "scope": "command"}])
    _said(app, "rm 9")
    assert asyncio.run(db.list_approved_commands()) == [{"pattern": "git", "scope": "command"}]
    _said(app, "rm git")
    assert asyncio.run(db.list_approved_commands()) == []
