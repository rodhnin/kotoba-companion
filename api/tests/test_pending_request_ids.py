"""interaction.pending_request_ids — card IDENTITY as a public door.

The voice session's microphone yield covers a SNAPSHOT of the cards his voice stepped over, so it
needs which cards are pending, not `has_pending`'s truth: a card that opens later must still shut
the mic while an interrupted card stands. `core.voice.session._pending_cards` read the private
`interaction._pending` for that, and its docstring named the accessor as interaction's to grow.
This file pins the grown accessor: same shape, same filter, and it must keep agreeing with the
private read for as long as that read exists.
"""
from __future__ import annotations

import asyncio

from kotoba.core import interaction


def _open(sid: str, label: str) -> str:
    rid, _fut = interaction._open_request(sid, label)
    return rid


def _drop_all(sid: str) -> None:
    for rid in list((interaction._pending.get(sid) or {}).keys()):
        interaction._abandon_request(sid, rid)


def test_identity_follows_the_cards_not_the_dict():
    """Two cards open -> both ids. One resolved -> its id leaves the view at once, done Future and
    all, even before _await_response pops the entry — the same semantics the mic yield snapshots."""

    async def main():
        sid = "prid-identity"
        try:
            r1, r2 = _open(sid, "approve rm?"), _open(sid, "approve pip?")
            assert interaction.pending_request_ids(sid) == frozenset({r1, r2})
            assert interaction.resolve(sid, {"approved": True}, request_id=r1)
            assert interaction.pending_request_ids(sid) == frozenset({r2})
        finally:
            _drop_all(sid)

    asyncio.run(main())


def test_empty_for_none_and_unknown_sessions():
    async def main():
        assert interaction.pending_request_ids(None) == frozenset()
        assert interaction.pending_request_ids("prid-never-opened") == frozenset()

    asyncio.run(main())


def test_agrees_with_the_private_read_it_replaces():
    """core.voice.session._pending_cards is this call now. The parity check stays: it is what says the
    door and the private dict answer the same question, so a change to `_pending`'s shape cannot quietly
    move only one of them."""

    async def main():
        sid = "prid-parity"
        try:
            _open(sid, "a"), _open(sid, "b")
            interaction.resolve(sid, "typed")  # newest-first: resolves "b"
            private = frozenset(
                rid for rid, fut in (interaction._pending.get(sid) or {}).items() if not fut.done()
            )
            assert interaction.pending_request_ids(sid) == private
            assert len(private) == 1
        finally:
            _drop_all(sid)

    asyncio.run(main())
