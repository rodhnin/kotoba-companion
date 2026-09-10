"""The 25-second approval window, and the only axis that may widen it.

`VOICE_APPROVAL_TIMEOUT = 25.0` is defended by TOOL_TIMEOUT, but 25 rather than 90 is one AGENT-MODE
fact: ElevenLabs times a silent turn out and re-fires it, binding only a turn that arrived through
/v1 with an EL agent on the other end — not voice_mode, not any turn.

`voice_mode` is a stored intention unrelated to what `/v1` gates on; with the tunnel up an EL turn
would take the relaxed branch and have its call cut instead. The axis is a per-turn TRANSPORT mark
carried on the ToolContext, set by the only code that knows what is on the other end — so the
window fails safe: unknown stays tight, only a turn known NOT to be EL's gets the long one."""
from __future__ import annotations

from kotoba.core import interaction
from kotoba.core.loop import TOOL_TIMEOUT, _tool_budget


class _Ctx:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_an_unmarked_voice_turn_keeps_the_window_it_has_today():
    """Nothing said this turn is ours, so it is treated as EL's: 25 s, under the loop's TOOL_TIMEOUT."""
    assert interaction.approval_timeout("voice") == 25.0
    assert interaction.approval_timeout(None) == interaction.VOICE_APPROVAL_TIMEOUT
    assert interaction.VOICE_APPROVAL_TIMEOUT < TOOL_TIMEOUT


def test_an_elevenlabs_turn_keeps_it_too():
    assert interaction.approval_timeout("voice", el_agent=True) == interaction.VOICE_APPROVAL_TIMEOUT


def test_a_voice_turn_that_is_ours_gets_a_readable_window():
    window = interaction.approval_timeout("voice", el_agent=False)

    assert window == interaction.LOCAL_VOICE_APPROVAL_TIMEOUT == 60.0
    assert window > interaction.VOICE_APPROVAL_TIMEOUT


def test_the_typed_window_is_untouched_on_every_transport():
    for mark in (None, True, False):
        assert interaction.approval_timeout("text", el_agent=mark) == interaction.DEFAULT_TIMEOUT


def test_the_mark_is_read_off_the_turn_and_defaults_to_the_tight_branch():
    assert interaction.el_agent_turn(_Ctx()) is None
    assert interaction.el_agent_turn(_Ctx(el_call_bound=True)) is True
    assert interaction.el_agent_turn(_Ctx(el_call_bound=False)) is False
    assert interaction.el_agent_turn(None) is None


def test_the_two_halves_of_the_mark_meet_on_a_real_turn():
    """The `/v1` path marks its own task, ToolContext reads it at construction, and this is the reader
    on the other side. One stored field: a second bool anywhere is the drift this is preventing."""
    from kotoba.core import transport
    from kotoba.tools import ToolContext

    assert interaction.el_agent_turn(ToolContext(db=None)) is False
    with transport.el_call_turn():
        el = ToolContext(db=None)
    assert interaction.el_agent_turn(el) is True
    assert interaction.approval_timeout("voice", el_agent=interaction.el_agent_turn(el)) == 25.0


# --- the coupling: an exec tool must outlive the card it opens ---------------------------------------

class _Spec:
    risk = "exec"


class _Tool:
    pass


def _budget(channel, el_agent=None):
    return _tool_budget(_Tool(), _Spec(), {}, TOOL_TIMEOUT, channel, el_agent=el_agent)


def test_the_tool_budget_follows_the_window_on_every_transport():
    """core.loop grants an exec tool `approval_timeout(...) + 5`, so raising the window raises the budget
    automatically. If it did not, the loop would cancel the tool mid-wait and abandon the card on screen —
    the invariant the 25 s was chosen for in the first place."""
    for channel, mark in (("voice", None), ("voice", True), ("voice", False), ("text", None)):
        assert _budget(channel, mark) > interaction.approval_timeout(channel, el_agent=mark)


def test_an_unmarked_voice_tool_budget_is_exactly_what_it_was():
    assert _budget("voice") == _budget("voice", el_agent=True) == 30
