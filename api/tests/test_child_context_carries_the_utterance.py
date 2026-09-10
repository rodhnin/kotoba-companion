"""A child ToolContext (a subagent's) must carry the parent's utterance.

`ToolContext.child()` copied no utterance, so every guard that anchors on the user's OWN words ran blind
under a helper: cronjob's repeat guard read nothing and failed closed, and deferred_exec._action_key —
the ONE REQUEST, ONE RUN anchor — returned None, switching the duplicate-execution guard OFF for any
deferred action a subagent schedules. Latent today (no subagent-reachable tool defers: work-runner
helpers inherit channel="text", and cron is not a delegate toolset), but the seam is exactly the kind
live QA keeps finding, so the utterance travels now: the model's goal paraphrase never replaces what
the user actually said.
"""
from __future__ import annotations

from kotoba.core.deferred_exec import _action_key
from kotoba.tools import ToolContext


def _parent(**kw):
    ctx = ToolContext(db=None, session_id="s-child-utt", user_text="run the primes script", **kw)
    ctx.user_texts = ["run the primes script", "hola"]
    return ctx


def test_child_carries_user_text_and_user_texts():
    parent = _parent()
    child = parent.child("sub1")

    assert child.user_text == "run the primes script"
    assert child.user_texts == ["run the primes script", "hola"]
    # Its own list: a helper must not be able to rewrite the parent's witness.
    assert child.user_texts is not parent.user_texts


def test_child_of_a_bare_parent_keeps_the_invariant():
    """core.loop guarantees user_texts[0] is user_text; a hand-built parent without the list still
    hands its child a consistent pair instead of an attribute that is not there."""
    ctx = ToolContext(db=None, session_id="s-bare", user_text="do it")
    child = ctx.child("sub2")

    assert child.user_text == "do it"
    assert child.user_texts == ["do it"]


def test_one_request_one_run_holds_under_a_subagent():
    """deferred_exec keys every deferred action to (user request + action). With no utterance the key is
    None and the guard is deliberately OFF — so a helper re-emitting an approved command could card and
    run it twice. The child's key must exist and be the PARENT's: same user request, same identity."""
    parent = _parent()
    child = parent.child("sub3")

    key = _action_key(child, "python primes.py")
    assert key is not None
    assert key == _action_key(parent, "python primes.py")


def test_a_truly_unanchored_context_still_switches_the_guard_off():
    """The documented escape stays: no user request anywhere (sessionless/synthetic caller) means no
    anchor, and the guard is OFF rather than session-global."""
    ctx = ToolContext(db=None, session_id="s-synth")
    child = ctx.child("sub4")

    assert child.user_text is None
    assert child.user_texts == []
    assert _action_key(child, "ls") is None


def test_the_cron_repeat_guard_reads_the_parents_words_under_a_helper():
    """The reader the cron agent built (`_repeat_was_asked`): under a child it used to see nothing and
    answer NO for everyone. With the utterance carried, the user's own cadence word is the authority in
    both directions — asked-for repeats survive, invented ones still die."""
    from kotoba.tools.action.cronjob import _repeat_was_asked

    asked = ToolContext(db=None, session_id="s-cron", user_text="recuérdame cada día tomar agua")
    asked.user_texts = ["recuérdame cada día tomar agua"]
    assert _repeat_was_asked(asked.child("sub5")) is True

    once = ToolContext(db=None, session_id="s-cron2", user_text="recuérdame en dos minutos sacar la basura")
    once.user_texts = ["recuérdame en dos minutos sacar la basura"]
    assert _repeat_was_asked(once.child("sub6")) is False
