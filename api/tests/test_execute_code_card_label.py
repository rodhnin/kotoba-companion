"""The execute_code approval card must show the full code as the action label, not just the
first 120 characters of the first line.

Before: the label was the first line truncated at 120 chars — a user could approve a comment
while the audit log never showed what actually ran. After: the full snippet is the label; the UI
clips the first line as a headline with a "Show full command" expander for the rest.

Safe path: a call that auto-approves via a saved "always allow execute_code" family must still
run without asking — no added friction there."""
from __future__ import annotations

import asyncio


from kotoba.core.approval import ApprovalGate
from kotoba.tools import ToolContext


def _make_ctx(tmp_path, asker=None, saved=None):
    gate = ApprovalGate(
        ask=asker,
        saved_commands=set(saved or ()),
        host_exec=True,
        workspace_root=tmp_path,
    )
    # channel="text" as the work runner sets it — that is what makes the approval BLOCK inline (and so
    # reach `asker`) instead of being deferred to an SSE card, which only a live voice turn needs.
    return ToolContext(db=None, session_id="t", workdir=tmp_path, mode="work", channel="text",
                       approval=gate)


def test_card_label_contains_full_code(tmp_path):
    """The label passed to confirm() must be the full code (not a truncated first line)."""
    seen_action: list[str] = []

    async def spy(action, risk, family=None):
        seen_action.append(action)
        return True  # approve

    code = "# setup\nimport math\nresult = math.sqrt(16)\nprint(result)"
    ctx = _make_ctx(tmp_path, asker=spy)

    async def run():
        from kotoba.tools.action import execute_code
        return await execute_code.execute({"code": code}, ctx)

    asyncio.run(run())
    assert seen_action, "asker was never called"
    label = seen_action[0]
    assert "import math" in label, "second line missing from label"
    assert "print(result)" in label, "fourth line missing from label"
    assert label.startswith("run Python:"), f"unexpected label prefix: {label[:40]!r}"


def test_card_label_not_truncated_at_120(tmp_path):
    """A first line longer than 120 chars must appear in full in the label."""
    seen_action: list[str] = []

    async def spy(action, risk, family=None):
        seen_action.append(action)
        return True

    first_line = "x = " + "a" * 200  # 204 chars — well over the old 120-char cap
    ctx = _make_ctx(tmp_path, asker=spy)

    async def run():
        from kotoba.tools.action import execute_code
        return await execute_code.execute({"code": first_line}, ctx)

    asyncio.run(run())
    assert seen_action
    assert "a" * 200 in seen_action[0], "full first line missing from label"


def test_auto_allowed_family_skips_asker(tmp_path):
    """A saved 'execute_code' family must still run without prompting — no friction on safe path."""
    asked: list[bool] = []

    async def spy(action, risk, family=None):
        asked.append(True)
        return True

    ctx = _make_ctx(tmp_path, asker=spy, saved={"execute_code"})

    async def run():
        from kotoba.tools.action import execute_code
        return await execute_code.execute({"code": "print(42)"}, ctx)

    asyncio.run(run())
    assert not asked, "asker was called even though execute_code family is saved"
