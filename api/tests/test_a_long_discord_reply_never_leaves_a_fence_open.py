"""Discord drops any message over 2000 characters, so a long reply has to be cut somewhere.

The cut that matters is the one inside a fenced code block: an unbalanced ``` swallows the whole
next message into the block, and she writes code often enough that this is the common case, not the
edge one. Every part has to be balanced on its own, and the reopened fence has to keep its language
or the highlighting is lost for the rest of the answer.
"""
from __future__ import annotations

from kotoba.discord.text import LIMIT, split_for_discord


def _fences(part: str) -> int:
    return sum(1 for line in part.splitlines() if line.strip().startswith("```"))


def test_a_short_reply_is_not_split():
    assert split_for_discord("hola") == ["hola"]


def test_no_part_is_over_the_limit():
    text = "\n\n".join(f"Párrafo {i} " + "palabra " * 40 for i in range(60))
    for part in split_for_discord(text):
        assert len(part) <= LIMIT


def test_a_fence_that_spans_the_cut_is_closed_and_reopened():
    body = "\n".join(f"    line_{i} = {i} * 2" for i in range(400))
    text = f"Aquí tienes:\n\n```python\n{body}\n```\n\nY ya está."
    parts = split_for_discord(text)
    assert len(parts) > 1
    for part in parts:
        assert _fences(part) % 2 == 0, part[:120]


def test_the_reopened_fence_keeps_its_language():
    body = "\n".join(f"    line_{i} = {i}" for i in range(400))
    parts = split_for_discord(f"```python\n{body}\n```")
    assert parts[1].lstrip().startswith("```python")


def test_prose_is_cut_at_a_paragraph_break_when_there_is_one():
    text = "\n\n".join("frase corta." * 20 for _ in range(30))
    parts = split_for_discord(text)
    assert len(parts) > 1
    assert not parts[0].endswith("frase cort")


def test_text_survives_the_round_trip():
    """A splitter that loses characters is worse than one that sends a wall."""
    text = "\n\n".join(f"bloque {i} " + "x" * 300 for i in range(20))
    joined = "".join(p for p in split_for_discord(text))
    for i in range(20):
        assert f"bloque {i} " in joined


def test_a_fence_that_opens_late_in_a_part_still_fits_the_limit():
    head = "a" * 1000 + "\n```python\n" + "x" * 986
    parts = split_for_discord(head + "\n\n" + "y" * 500 + "\n```\n")
    assert len(parts) > 1
    for part in parts:
        assert len(part) <= LIMIT, len(part)
        assert _fences(part) % 2 == 0


def test_a_hard_cut_inside_a_fence_still_fits_the_limit():
    for part in split_for_discord("```js\n" + "z" * 2500 + "\n```"):
        assert len(part) <= LIMIT, len(part)


def test_a_one_line_fence_is_already_closed():
    text = "x\n```js console.log(1) ```\n" + "w" * 1990 + "\n\nmore"
    parts = split_for_discord(text)
    assert not parts[0].endswith("```")
    assert not parts[1].startswith("```")


def test_the_cut_notice_never_pushes_the_last_part_over_the_limit():
    import asyncio

    from kotoba.discord.text import StreamingReply

    class _Msg:
        def __init__(self, content):
            self.content = content

    class _Channel:
        def __init__(self):
            self.sent = []

        async def send(self, body):
            self.sent.append(body)
            return _Msg(body)

    channel = _Channel()
    reply = StreamingReply(channel)
    reply.feed("\n\n".join("p" * 1999 for _ in range(8)))
    asyncio.run(reply.flush(final=True))
    assert len(channel.sent) == 5
    assert "cut here" in channel.sent[-1]
    for body in channel.sent:
        assert len(body) <= LIMIT, len(body)


def test_code_keeps_its_empty_brackets():
    from kotoba.discord.text import tidy_links

    code = "Call `main()` first.\n```python\ndef main():\n    xs = []\n```"
    assert tidy_links(code) == code
    assert tidy_links("no propaganda barata. ()") == "no propaganda barata."


def test_a_tracking_parameter_first_in_the_query_leaves_a_valid_url():
    from kotoba.discord.text import tidy_links

    assert tidy_links("https://example.com/p?ref=abc&id=5") == "https://example.com/p?id=5"
    assert tidy_links("https://example.com/p?id=5&ref=abc") == "https://example.com/p?id=5"
    assert tidy_links("https://example.com/p?a=1&ref=x&b=2") == "https://example.com/p?a=1&b=2"
    assert tidy_links("https://example.com/p?ref=x") == "https://example.com/p"
