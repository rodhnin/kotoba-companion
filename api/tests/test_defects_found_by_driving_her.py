"""Defects that only appeared when somebody drove her, each test named for the one it pins.

The round covered: cancelling work that left deferred tasks alive, a report written twice under the
same title, an unbounded image fetch, a substring server match, the delegate spawn cap, destructive
Python slipping past a saved grant, an MCP install that connected after a refusal, an unverified
registry candidate that did not say so, the browser tool cap, the fallback line for a reply the
filters emptied, the local-time block the model schedules from, a phantom `in_minutes: 0`, a
filesystem server pointed at a missing directory, and an orphaned approval card from a dead session."""
from __future__ import annotations

import asyncio



def test_deferred_exec_cancel_stops_pending_task(monkeypatch):
    """Cancelling the work must cancel DEFERRED exec tasks too, not just the work runner.

    A deferred task parked on an approval card is still a task. `deferred_exec.cancel` is what
    `cancel_work` and `leave` call; before this, the command the user had asked to stop was still
    waiting on its card and would run the moment anyone answered it."""
    import kotoba.core.deferred_exec as de

    async def go():
        started = asyncio.Event()
        ran = {"v": False}

        async def slow_approval(sid, action, timeout=180.0, **k):
            started.set()
            await asyncio.sleep(30)   # stands in for a card nobody has answered yet
            return (True, False)

        async def runner():
            ran["v"] = True
            return "did it"

        # monkeypatch, not a bare rebind: an unrestored one leaks into every later test in the session
        monkeypatch.setattr(de, "request_approval", slow_approval)
        ctx = type("C", (), {"session_id": "cw1", "approval": None})()
        de.schedule(ctx, "rm something", runner, label="rm something")
        await started.wait()
        n = de.cancel("cw1")
        assert n == 1
        await asyncio.sleep(0.05)
        assert ran["v"] is False
        assert "cw1" not in de._tasks

    asyncio.run(go())


def test_make_report_dedupes_double_call(monkeypatch, tmp_path):
    """A second make_report for the same title is a no-op that says so.

    Titles are compared normalised, so a change of case is the same report."""
    import kotoba.core.reports as reports
    import kotoba.tools.action.make_report as mr

    reports.clear_report("r1")
    monkeypatch.setattr(mr, "_template", lambda: "<html>{{TITLE}}{{SUMMARY}}{{STEPS}}{{RESULTS}}{{FILES}}{{NEXT}}{{CHIBI}}</html>")

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("kotoba.core.events.emit_task", _noop)
    # the point under test is the dedupe, so nothing here reaches the real library or memory
    import kotoba.core.file_library as fl
    monkeypatch.setattr(fl, "save_text", lambda *a, **k: None)

    class _Ctx:
        session_id = "r1"

    out1 = asyncio.run(mr.execute({"title": "My Report", "summary": "s"}, _Ctx()))
    out2 = asyncio.run(mr.execute({"title": "my report", "summary": "s"}, _Ctx()))
    assert "already" in out2.lower() and reports.already_made("r1", "My Report")
    assert out1 != out2


def test_remember_image_caps_data_url():
    """remember_image rejects an oversize `data:` URL, and the cap fires BEFORE anything is stored."""
    import kotoba.tools.builtin.remember_image as ri

    class _Ctx:
        session_id = "img1"

    big = "data:image/png;base64," + ("A" * (ri._MAX_FETCH_BYTES * 2))
    out = asyncio.run(ri.execute({"source": big, "about": "huge"}, _Ctx()))
    assert "too big" in out.lower()
    # a small one may still fail downstream, but never on size — the cap must not be the reason
    small = "data:image/png;base64," + ("A" * 100)
    out2 = asyncio.run(ri.execute({"source": small, "about": "tiny"}, _Ctx()))
    assert out2 is None or "too big" not in (out2 or "").lower()


def test_activate_tools_token_match_not_substring():
    """activate_tools matches a server by TOKEN, not substring: `git` must not reach `github`."""
    import kotoba.tools.builtin.activate_tools as at

    class _MCP:
        server_tools = {"github": ["github__x"]}

    class _Ctx:
        def __init__(s): s.mcp = _MCP(); s.session_id = "a1"

    out_git = asyncio.run(at.execute({"server": "git"}, _Ctx()))
    assert "don't have a connected server matching 'git'" in out_git
    assert "github" in asyncio.run(at.execute({"server": "github"}, _Ctx())).lower()
    assert "github" in asyncio.run(at.execute({"server": "github mcp"}, _Ctx())).lower()


def test_delegate_selection_caps_and_dedups_before_spawn():
    """Parallel delegate selection caps and de-duplicates BEFORE anything spawns.

    Four cases, in order: six distinct calls against a fresh budget spawn the first `_DELEGATE_LIMIT`
    and skip the rest; duplicate tasks in one batch share a slot instead of each taking one; an exact
    repeat already run earlier in the turn is skipped; and an exhausted budget spawns nothing."""
    import kotoba.core.loop as loop

    items = [(f"call{i}", {"task": f"framework {i}"}) for i in range(6)]
    spawn, skip = loop._select_delegates_to_spawn(items, budget=loop._DELEGATE_LIMIT, already_ran=lambda s: False)
    assert len(spawn) == loop._DELEGATE_LIMIT and len(skip) == 6 - loop._DELEGATE_LIMIT
    assert [tc for tc, _ in spawn] == [f"call{i}" for i in range(loop._DELEGATE_LIMIT)]

    dupes = [("a", {"task": "x"}), ("b", {"task": "x"}), ("c", {"task": "y"}), ("d", {"task": "x"})]
    spawn2, skip2 = loop._select_delegates_to_spawn(dupes, budget=3, already_ran=lambda s: False)
    assert len(spawn2) == 2 and {tc for tc, _ in spawn2} == {"a", "c"}

    ran = {'delegate:{"task": "seen"}'}
    mixed = [("p", {"task": "seen"}), ("q", {"task": "new"})]
    spawn3, skip3 = loop._select_delegates_to_spawn(mixed, budget=3, already_ran=lambda s: s in ran)
    assert [tc for tc, _ in spawn3] == ["q"] and [tc for tc, _ in skip3] == ["p"]

    spawn4, skip4 = loop._select_delegates_to_spawn(items, budget=0, already_ran=lambda s: False)
    assert spawn4 == [] and len(skip4) == 6


def test_dangerous_code_flags_destructive_and_forces_reprompt():
    """dangerous_code flags destructive Python, and `force_ask` overrides a saved always-allow family.

    Destructive, `exec`/`eval` and raw-socket snippets are flagged; benign computation is not. A
    saved `execute_code` family would normally auto-allow a clean action string — `force_ask` has to
    veto that, or the grant becomes permanent for anything the family covers."""
    from kotoba.core.approval import dangerous_code, ApprovalGate

    assert dangerous_code("import shutil; shutil.rmtree('/data')")
    assert dangerous_code("import os; os.system('rm -rf x')")
    assert dangerous_code("import subprocess; subprocess.run(['x'])")
    assert dangerous_code("eval(open('x').read())")
    assert dangerous_code("import socket; socket.socket()")
    assert dangerous_code("print(sum(range(10)))") is None
    assert dangerous_code("x = [i*2 for i in range(5)]\nprint(x)") is None

    g = ApprovalGate(saved_commands={"execute_code"}, host_exec=True)
    action = "run Python snippet"
    assert g.would_auto_allow(action, "exec", family="execute_code", force_ask=False) is True
    assert g.would_auto_allow(action, "exec", family="execute_code", force_ask=True) is False


def test_mcp_install_denied_does_not_connect(monkeypatch):
    """mcp_install asks before connecting, and a refusal means nothing is installed."""
    import kotoba.tools.action.mcp_install as mi
    import kotoba.core.interaction as interaction

    connected = {"v": False}

    class _MCP:
        server_tools = {}

        async def connect(self, name, cfg):
            connected["v"] = True
            return ["x__y"]

    class _Ctx:
        def __init__(s): s.mcp = _MCP(); s.session_id = "b2"

    async def _deny(sid, text, **k):
        return (False, False)  # the user declines the card
    monkeypatch.setattr(interaction, "request_approval", _deny)

    out = asyncio.run(mi.execute({"name": "filesystem"}, _Ctx()))
    assert connected["v"] is False
    assert "said no" in out.lower() and "did not happen" in out.lower()


def test_mcp_find_fallback_card_warns_unverified():
    """The mcp_find approval card says so when the candidate came from the PyPI/npm fallback rather
    than the verified registry — a registry-verified card stays clean."""
    import kotoba.tools.action.mcp_find as mf
    from kotoba.core.mcp.registry_search import Candidate

    c = Candidate(name="blender-mcp", description="drive blender", repo_url="https://pypi.org/project/blender-mcp",
                  version="1.0", kind="pypi", cfg={"command": "uvx", "args": ["blender-mcp"]})
    verified, _ = mf._approval_card(c, from_fallback=False)
    unverified, _ = mf._approval_card(c, from_fallback=True)
    assert "not verified" in unverified.lower() and "pypi/npm" in unverified.lower()
    assert "not verified" not in verified.lower()


def test_browser_tool_cap_is_high_but_finite():
    """Browser tools get a high per-tool cap, not an unlimited one.

    Browsing legitimately needs many more calls than the default, but the cap has to stay finite or
    a page that never settles loops until the turn is killed."""
    import kotoba.core.loop as loop

    assert loop._BROWSER_TOOL_LIMIT > loop._PER_TOOL_LIMIT
    assert loop._BROWSER_TOOL_LIMIT < 1000


def test_an_emptied_reply_never_becomes_a_blank_turn():
    """The defect this pins is silence, not wording: the filters removed everything the model
    produced and the turn ended with nothing said."""
    import kotoba.core.stream as sse

    assert sse.filtered_reply_fallback().strip()
    assert sse.crash_apology().strip()


def test_now_strings_include_local_offset():
    """The prompt exposes the user's LOCAL time carrying an explicit UTC offset.

    Without the offset the model cannot turn "at eight" into an absolute `due_at`."""
    import kotoba.soul.prompt as p

    utc, local = p._now_strings()
    assert "UTC" in utc
    assert "UTC" in local and ("+" in local or "-" in local)


def test_parse_due_ignores_zero_in_minutes_and_uses_due_at():
    """`_parse_due` ignores `in_minutes: 0` — a default the model emits without meaning it.

    With a real `due_at` beside it the due_at wins; alone it is a graceful failure, never a reminder
    that fires immediately. Negative values stay rejected and a positive `in_minutes` still works."""
    import kotoba.tools.action.cronjob as cj

    got = cj._parse_due({"in_minutes": 0, "due_at": "2026-07-18T16:00:00Z"})
    assert got == "2026-07-18 16:00:00"
    assert cj._parse_due({"in_minutes": 0, "due_at": ""}) is None
    assert cj._parse_due({"in_minutes": -5}) is None
    from datetime import datetime, timezone
    rel = cj._parse_due({"in_minutes": 90})
    assert rel is not None and datetime.strptime(rel, "%Y-%m-%d %H:%M:%S") > datetime.now(timezone.utc).replace(tzinfo=None)


def test_build_cfg_filesystem_path_exists(monkeypatch, tmp_path):
    """`build_cfg("filesystem")` resolves `{path}` to a directory that EXISTS.

    The upstream server refuses to start against a missing directory, which is what made the install
    fail: the placeholder was resolved but nothing created the target."""
    import kotoba.core.mcp.known as known

    target = tmp_path / "kfiles"           # deliberately absent: build_cfg has to create it
    monkeypatch.setenv("KOTOBA_WORKSPACE_DIR", str(target))
    res = known.build_cfg("filesystem")
    assert res is not None
    _canonical, cfg = res
    assert str(target) in cfg["args"]
    assert "{path}" not in " ".join(cfg["args"])
    assert target.is_dir()


def test_note_turn_warns_on_orphaned_pending(caplog):
    """A new session opening while another session's card is still unanswered logs a warning once.

    The old session's card can no longer be answered by anyone, and a single-slot UI would otherwise
    drop it silently. A repeat turn on an already-seen session must not warn again."""
    import logging

    import kotoba.core.interaction as it

    it._seen_sessions.clear()
    it._pending.clear()

    async def go():
        fut = asyncio.get_event_loop().create_future()
        it._pending["old-sid"] = {"req-1": fut}   # session -> {request_id: Future}
        it.note_turn("old-sid")
        with caplog.at_level(logging.WARNING, logger="kotoba"):
            it.note_turn("new-sid")
        assert any("orphaned" in r.message.lower() for r in caplog.records)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="kotoba"):
            it.note_turn("new-sid")
        assert not any("orphaned" in r.message.lower() for r in caplog.records)
        fut.cancel()

    asyncio.run(go())
    it._seen_sessions.clear()
    it._pending.clear()



def test_saved_code_grant_still_asks_for_disk_and_network():
    """The "always allow Python" grant covers COMPUTATION, never I/O.

    "Always allow" is offered on the card itself rather than behind a settings switch, so the grant
    is given mid-conversation under one printed line. It must therefore not extend to reaching disk
    or the network: the exfiltration shape — read a key, POST it — has to raise a card even with the
    family saved."""
    from kotoba.core.approval import dangerous_code

    exfil = "import requests\nrequests.post(url, data=open('~/.ssh/id_rsa').read())"
    assert dangerous_code(exfil)
    assert dangerous_code("open('notes.txt','w').write(x)")
    assert dangerous_code("from pathlib import Path; Path('a').write_text('b')")
    assert dangerous_code("import shutil; shutil.copy(src, dst)")
    assert dangerous_code("import smtplib")
    assert dangerous_code("open('/etc/shadow').read()")

    # FETCHING and pure computation stay smooth — flagging those would train people to click through
    assert dangerous_code("import requests; print(requests.get(url).text[:100])") is None
    assert dangerous_code("print(sum(range(1000)))") is None
    assert dangerous_code("data = [i*i for i in range(10)]\nprint(max(data))") is None
    assert dangerous_code("open('notes.txt').read()") is None
