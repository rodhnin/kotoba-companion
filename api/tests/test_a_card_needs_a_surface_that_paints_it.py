"""Discord listens to the event queue and paints no fire-and-forget card, so the announcing tools
reported success into nothing: "I opened a text box on screen" to a room with no screen. Being
LISTENED TO and being DRAWN are two questions and only one was asked.

The line falls between announcing and BLOCKING, not between surfaces. A blocking card is answered
everywhere a queue is registered — Discord posts the prompt and reads the asker's next message — so
widening the refusal to cover those took a working path away. Approvals were never in scope."""
from __future__ import annotations

import asyncio

import pytest

from kotoba.cli.approvals import Approvals
from kotoba.cli.events_bridge import EventBridge
from kotoba.core import events, interaction
from kotoba.discord import authority, cards as discord_cards


@pytest.fixture(autouse=True)
def _clean():
    events.event_queues.clear()
    events._cardless.clear()
    yield
    events.event_queues.clear()
    events._cardless.clear()


def _actor(**kw):
    return authority.Actor(user_id=1, guild_id=2, display="Wren", handle="wrenlow",
                           is_owner=kw.get("owner", False), is_guild_admin=kw.get("admin", False),
                           is_guild_owner=False)


@pytest.mark.parametrize("who", [None, _actor(), _actor(admin=True), _actor(owner=True)],
                         ids=["stranger", "guest", "admin", "owner"])
def test_no_discord_turn_is_offered_a_tool_that_draws_a_card(who):
    assert authority.NO_SURFACE_TOOLS <= authority.excluded_tools(who)


def test_a_cardless_listener_is_heard_but_not_drawn():
    events.register("s", draws_cards=False)
    assert events.has_listener("s")
    assert not events.draws_cards("s")


def test_a_normal_listener_draws():
    events.register("s")
    assert events.draws_cards("s")


def test_unregistering_forgets_that_the_session_was_cardless():
    queue = events.register("s", draws_cards=False)
    events.unregister("s", queue)
    events.register("s")
    assert events.draws_cards("s")


def test_a_drawing_surface_taking_over_the_id_is_not_left_refusing():
    """Takeover without a teardown: the flag is restated by `register`, never merely added to."""
    events.register("s", draws_cards=False)
    events.register("s")
    assert events.draws_cards("s")


def test_the_losers_teardown_leaves_the_live_surfaces_answer_alone():
    """`unregister` takes the caller's own queue for the queue; the flag rides the same early return,
    or an overlapping reconnect would answer for the connection that replaced it."""
    stale = events.register("s")
    events.register("s", draws_cards=False)
    events.unregister("s", stale)
    assert events.has_listener("s") and not events.draws_cards("s")

    stale_cardless = events.register("s", draws_cards=False)
    events.register("s")
    events.unregister("s", stale_cardless)
    assert events.draws_cards("s")


def test_the_flag_never_outlives_the_queue_it_describes():
    """Nothing pops a queue without dropping the flag, so a reused id cannot inherit a dead refusal."""
    queue = events.register("s", draws_cards=False)
    events.unregister("s", queue)
    assert "s" not in events._cardless
    events.register("s2", draws_cards=False)
    events.unregister("s2")
    assert "s2" not in events._cardless


def test_the_non_blocking_card_reports_that_nothing_was_drawn():
    events.register("s", draws_cards=False)
    assert asyncio.run(interaction.open_input_card("s", "Your postal code")) is False
    assert asyncio.run(interaction.open_link_card("s", "https://example.com")) is False


def test_an_approval_still_reaches_a_surface_that_answers_in_words():
    """Discord approves and refuses; it just paints nothing you can announce."""
    events.register("s", draws_cards=False)
    assert interaction._reachable("s", "approval for 'ls'")


class _FakeMessage:
    def __init__(self, content: str, channel_id: int, author_id: int) -> None:
        self.content = content
        self.channel = type("C", (), {"id": channel_id})()
        self.author = type("A", (), {"id": author_id})()


class _FakeClient:
    async def wait_for(self, event, check=None, timeout=None):
        return _FakeMessage("SW1A 1AA", 42, 7)


class _FakeChannel:
    id = 42
    guild = None

    def __init__(self) -> None:
        self.sent: list[str] = []
        self._state = type("S", (), {"_get_client": staticmethod(lambda: _FakeClient())})()

    async def send(self, *args, **kwargs):
        self.sent.append(args[0] if args else "")


def test_a_cardless_surface_still_answers_a_card_that_blocks():
    """The regression this file exists to stop twice over: Discord has no box to draw, and answers a
    blocking one anyway by posting the prompt and reading the reply. Refusing it on the surface's
    behalf loses `clarify`, the OAuth token and every credential prompt on that surface."""
    async def go():
        queue = events.register("s", draws_cards=False)
        channel = _FakeChannel()
        asking = discord_cards.DiscordCards(channel, asker=7, owner=7)
        bridge = EventBridge(queue, approvals=Approvals("s", ask=asking.ask))
        bridge.start()
        card: dict = {}
        value = await interaction.request_input("s", "Your postal code", timeout=5.0, card=card)
        await bridge.aclose()
        return value, card, channel.sent

    value, card, sent = asyncio.run(go())
    assert value == "SW1A 1AA", card.get("verdict")
    assert sent and "Your postal code" in sent[0]


@pytest.mark.parametrize(
    "ending,must_say",
    [(interaction.UNREACHABLE, "no way to put"), (interaction.UNANSWERED, "expired"),
     (interaction.DECLINED, "said NO")],
    ids=["undrawable", "expired", "declined"],
)
def test_a_blocking_ask_user_names_the_ending_it_had(monkeypatch, ending, must_say):
    """A bare None is graded a failure and speaks one canned line over every ending, so a box that was
    never drawn and one left empty on purpose sounded the same — and only the second was a decision."""
    import types

    from kotoba.tools.builtin import ask_user

    async def fake_request_input(sid, label, kind, *, timeout=None, detail=None, card=None):
        card["verdict"] = ending
        return None

    monkeypatch.setattr(interaction, "request_input", fake_request_input)
    ctx = types.SimpleNamespace(session_id="s", mode="work", call_id="c1")
    out = asyncio.run(ask_user.execute({"prompt": "Your postal code"}, ctx))
    assert must_say in out
    assert interaction.no_run_verdict(ctx, "c1") == ending
