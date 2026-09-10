from __future__ import annotations

from kotoba.discord import actions

CHANNEL = 9000000000000000321
FORUM = 9000000000000000322


class _Text:
    def __init__(self, cid, name) -> None:
        self.id = cid
        self.name = name

    async def send(self, *a, **k):
        return None


class _Forum:
    def __init__(self, cid, name) -> None:
        self.id = cid
        self.name = name


class _Guild:
    owner_id = 9000000000000000999
    roles = []
    members = []

    def __init__(self) -> None:
        self.channels = [_Text(CHANNEL, "general"), _Forum(FORUM, "dudas")]
        self.text_channels = [self.channels[0]]
        self.me = None

    def get_channel(self, cid):
        return next((c for c in self.channels if c.id == cid), None)


def test_a_channel_mention_token_resolves_to_the_channel():
    guild = _Guild()
    assert actions.find_channel(guild, f"<#{CHANNEL}>").id == CHANNEL
    assert actions.find_channel(guild, str(CHANNEL)).id == CHANNEL
    assert actions.find_channel(guild, "#general").id == CHANNEL


def test_the_history_reader_takes_the_same_token():
    from kotoba.tools.action.discord_read_history import _resolve

    assert _resolve(_Guild(), f"<#{CHANNEL}>").id == CHANNEL


def test_a_message_into_a_forum_is_refused_before_the_card_not_after_it():
    said = actions.refusals(_Guild(), [{"op": "post_message", "target": f"<#{FORUM}>",
                                       "value": "hola"}])
    assert len(said) == 1
    assert "forum_post" in said[0]


def test_a_forum_post_without_a_title_is_refused():
    said = actions.refusals(_Guild(), [{"op": "forum_post", "target": "#dudas", "value": "hola"}])
    assert len(said) == 1 and "title" in said[0]
    assert actions.refusals(_Guild(), [{"op": "forum_post", "target": "#dudas", "to": "Fuentes",
                                        "value": "hola"}]) == []


def test_a_message_into_a_text_channel_is_still_fine():
    assert actions.refusals(_Guild(), [{"op": "post_message", "target": "#general",
                                        "value": "hola"}]) == []
