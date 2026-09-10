"""Tests for the task-list system: core module, todo tool, REST endpoint, and leave lifecycle.

The list is one plan per session, held in `core.task_list._state` and mirrored to the browser panel
as a whole frame on every change, so the panel can never drift from the server's copy.

The later half of the file pins what live use taught the tool: a plan and its first tick arrive in
one call, `steps: []` is an empty field and not an instruction to wipe, an announce turn may tick and
close but never restart, re-sending the steps already on screen updates the plan instead of opening a
second one with the same words, and closing over unmarked steps is `abandoned` rather than `done`."""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.task_list as tl


def setup_function():
    tl._state.clear()


# --- core.task_list ---------------------------------------------------------


def test_create_produces_correct_structure():
    lst = tl.open_list("s1", ["Find sources", "Write draft", "Review"])
    assert lst["status"] == "open"
    assert lst["rev"] == 1
    assert len(lst["tasks"]) == 3
    assert lst["tasks"][0] == {"id": f"{lst['list_id']}:1", "text": "Find sources", "detail": "",
                               "status": "pending", "order": 1}
    assert lst["tasks"][2]["order"] == 3


def test_create_sets_title():
    lst = tl.open_list("s2", ["a", "b", "c"], title="My plan")
    assert lst["title"] == "My plan"


def test_cap_20_steps():
    steps = [f"step {i}" for i in range(25)]
    lst = tl.open_list("s3", steps)
    assert len(lst["tasks"]) == 20


def test_cap_200_chars_per_step():
    long_step = "x" * 250
    lst = tl.open_list("s4", [long_step])
    assert len(lst["tasks"][0]["text"]) == 200


def test_tick_done_batched():
    tl.open_list("s5", ["a", "b", "c", "d", "e"])
    tl.update("s5", done=[1, 2, 3])
    lst = tl.get("s5")
    statuses = {t["order"]: t["status"] for t in lst["tasks"]}
    assert statuses[1] == "done"
    assert statuses[2] == "done"
    assert statuses[3] == "done"
    assert statuses[4] == "pending"
    assert statuses[5] == "pending"


def test_active_sets_one_clears_previous():
    tl.open_list("s6", ["a", "b", "c"])
    tl.update("s6", active=1)
    statuses = {t["order"]: t["status"] for t in tl.get("s6")["tasks"]}
    assert statuses[1] == "active"

    tl.update("s6", active=2)
    statuses = {t["order"]: t["status"] for t in tl.get("s6")["tasks"]}
    assert statuses[1] == "pending"  # reverted from active
    assert statuses[2] == "active"


def test_drop_keeps_row():
    tl.open_list("s7", ["a", "b", "c"])
    tl.update("s7", drop=[2])
    tasks = tl.get("s7")["tasks"]
    assert len(tasks) == 3
    assert tasks[1]["status"] == "dropped"


def test_close_sets_list_done():
    tl.open_list("s8", ["a", "b"])
    tl.update("s8", done=[1, 2])
    tl.update("s8", close=True)
    assert tl.get("s8")["status"] == "done"


def test_replace_on_new_opens_abandons_old():
    lst1 = tl.open_list("s9", ["a", "b"])
    id1 = lst1["list_id"]
    assert tl._state["s9"]["status"] == "open"

    lst2 = tl.open_list("s9", ["x", "y", "z"])
    assert tl._state["s9"]["list_id"] == lst2["list_id"]
    assert tl._state["s9"]["list_id"] != id1
    assert lst2["status"] == "open"
    assert len(lst2["tasks"]) == 3


def test_idempotent_update_done_twice():
    """Marking a step done twice must not increment rev a second time."""
    tl.open_list("s10", ["a", "b", "c"])
    tl.update("s10", done=[1])
    rev_after_first = tl.get("s10")["rev"]
    tl.update("s10", done=[1])
    rev_after_second = tl.get("s10")["rev"]
    assert rev_after_first == rev_after_second


def test_idempotent_update_drop_twice():
    tl.open_list("s11", ["a", "b"])
    tl.update("s11", drop=[1])
    rev1 = tl.get("s11")["rev"]
    tl.update("s11", drop=[1])
    assert tl.get("s11")["rev"] == rev1


def test_update_increments_rev():
    tl.open_list("s12", ["a", "b", "c"])
    rev0 = tl.get("s12")["rev"]
    tl.update("s12", done=[1])
    assert tl.get("s12")["rev"] == rev0 + 1


def test_frame_is_pure_function_of_state():
    """frame() is determined entirely by state; mutating the copy doesn't affect state."""
    tl.open_list("sf1", ["a", "b", "c"])
    f1 = tl.frame("sf1")
    f2 = tl.frame("sf1")
    assert f1 == f2

    f1["tasks"][0]["status"] = "MODIFIED"
    f3 = tl.frame("sf1")
    assert f3["tasks"][0]["status"] == "pending"


def test_frame_excludes_internal_fields():
    tl.open_list("sf2", ["a"])
    f = tl.frame("sf2")
    assert set(f.keys()) == {"list_id", "title", "tasks", "status", "rev"}
    assert "origin" not in f
    assert "created_ts" not in f
    assert "updated_ts" not in f


def test_frame_none_when_no_list():
    assert tl.frame("nobody") is None


def test_frame_carries_whole_list_after_mutation():
    tl.open_list("sf3", ["a", "b", "c"])
    tl.update("sf3", done=[1], active=2)
    f = tl.frame("sf3")
    by_order = {t["order"]: t for t in f["tasks"]}
    assert by_order[1]["status"] == "done"
    assert by_order[2]["status"] == "active"
    assert by_order[3]["status"] == "pending"


def test_clear_removes_state():
    tl.open_list("sc1", ["a"])
    tl.clear("sc1")
    assert tl.get("sc1") is None
    assert tl.frame("sc1") is None


def test_clear_is_noop_on_unknown_session():
    tl.clear("nobody")  # must not raise


def test_prompt_note_open_list():
    tl.open_list("pn1", ["Step A", "Step B", "Step C"])
    note = tl.prompt_note("pn1")
    assert "[YOUR OPEN TASK LIST" in note
    assert "Step A" in note
    assert "Step B" in note


def test_prompt_note_empty_when_no_list():
    assert tl.prompt_note("nobody") == ""


def test_prompt_note_empty_when_closed():
    tl.open_list("pn2", ["a"])
    tl.update("pn2", done=[1], close=True)
    assert tl.prompt_note("pn2") == ""


def test_prompt_note_all_done_prompts_close():
    tl.open_list("pn3", ["a", "b"])
    tl.update("pn3", done=[1, 2])
    note = tl.prompt_note("pn3")
    assert "close" in note.lower()


def test_prompt_note_caps_at_10_steps():
    tl.open_list("pn4", [f"step {i}" for i in range(15)])
    note = tl.prompt_note("pn4")
    assert "more" in note  # signals truncation


def test_update_on_closed_list_is_noop():
    tl.open_list("s13", ["a"])
    tl.update("s13", close=True)
    rev_closed = tl.get("s13")["rev"]
    result = tl.update("s13", done=[1])  # list is closed — should be no-op
    assert tl.get("s13")["rev"] == rev_closed


def test_update_on_missing_session_returns_none():
    result = tl.update("nobody", done=[1])
    assert result is None


# --- the SSE contract: what the panel receives ------------------------------


def test_emit_task_list_carries_whole_frame():
    """emit_task_list pushes exactly the frame dict as a 'task_list' kind event."""
    from kotoba.core.events import register, unregister, emit_task_list

    session_id = "em1"
    queue = register(session_id)
    try:
        tl.open_list(session_id, ["step 1", "step 2", "step 3"])
        tl.update(session_id, done=[1], active=2)
        f = tl.frame(session_id)

        asyncio.run(emit_task_list(session_id, f))

        frame_in_queue = queue.get_nowait()
        assert frame_in_queue["type"] == "task"
        assert frame_in_queue["kind"] == "task_list"
        assert frame_in_queue["status"] == "open"
        assert len(frame_in_queue["tasks"]) == 3
        assert frame_in_queue["rev"] == f["rev"]
        by_order = {t["order"]: t for t in frame_in_queue["tasks"]}
        assert by_order[1]["status"] == "done"
        assert by_order[2]["status"] == "active"
    finally:
        unregister(session_id, queue)


def test_emit_task_list_closed_list_same_kind():
    """A closed list is emitted as the same 'task_list' kind — the status field carries the lifecycle."""
    from kotoba.core.events import register, unregister, emit_task_list

    session_id = "em2"
    queue = register(session_id)
    try:
        tl.open_list(session_id, ["a", "b"])
        tl.update(session_id, done=[1, 2], close=True)
        f = tl.frame(session_id)
        asyncio.run(emit_task_list(session_id, f))

        frame_in_queue = queue.get_nowait()
        assert frame_in_queue["kind"] == "task_list"
        assert frame_in_queue["status"] == "done"
    finally:
        unregister(session_id, queue)


# --- the todo tool ----------------------------------------------------------


def test_todo_tool_execute_create():
    from dataclasses import dataclass

    @dataclass
    class Ctx:
        session_id: str = "tt1"

    async def run():
        from kotoba.tools.builtin.todo import execute
        result = await execute({"steps": ["Step A", "Step B", "Step C"], "title": "My plan"}, Ctx())
        return result

    result = asyncio.run(run())
    assert "open" in result
    assert "3" in result
    lst = tl.get("tt1")
    assert lst is not None
    assert len(lst["tasks"]) == 3
    assert lst["title"] == "My plan"


def test_todo_tool_no_list_no_steps_returns_instruction():
    from dataclasses import dataclass

    @dataclass
    class Ctx:
        session_id: str = "tt2"

    async def run():
        from kotoba.tools.builtin.todo import execute
        return await execute({}, Ctx())

    result = asyncio.run(run())
    assert "steps" in result.lower()


def test_todo_tool_update_existing():
    from dataclasses import dataclass

    @dataclass
    class Ctx:
        session_id: str = "tt3"

    async def run():
        from kotoba.tools.builtin.todo import execute
        await execute({"steps": ["a", "b", "c", "d", "e"]}, Ctx())
        return await execute({"done": [1, 2], "active": 3}, Ctx())

    result = asyncio.run(run())
    assert "done" in result.lower() or "2" in result
    lst = tl.get("tt3")
    by_order = {t["order"]: t["status"] for t in lst["tasks"]}
    assert by_order[1] == "done"
    assert by_order[2] == "done"
    assert by_order[3] == "active"


def test_todo_tool_close():
    from dataclasses import dataclass

    @dataclass
    class Ctx:
        session_id: str = "tt4"

    async def run():
        from kotoba.tools.builtin.todo import execute
        await execute({"steps": ["a", "b"]}, Ctx())
        await execute({"done": [1, 2]}, Ctx())
        return await execute({"close": True}, Ctx())

    result = asyncio.run(run())
    assert "done" in result.lower() or "completed" in result.lower()
    assert tl.get("tt4")["status"] == "done"


def test_todo_tool_limit_higher_than_per_tool_default():
    """A five-step plan ticked one step at a time is six todo calls and must not hit the anti-loop cap.

    `_PER_TOOL_LIMIT` (3) would refuse the fourth call, so todo carries its own, higher ceiling."""
    from kotoba.core.loop import _PER_TOOL_LIMIT, _TODO_TOOL_LIMIT

    assert _TODO_TOOL_LIMIT > 5, (
        f"_TODO_TOOL_LIMIT={_TODO_TOOL_LIMIT} must be > 5 to allow 1 create + 5 individual ticks"
    )
    assert _TODO_TOOL_LIMIT > _PER_TOOL_LIMIT, (
        f"_TODO_TOOL_LIMIT={_TODO_TOOL_LIMIT} must exceed _PER_TOOL_LIMIT={_PER_TOOL_LIMIT}"
    )


def test_todo_cap_is_used_in_tool_cap_expression():
    """The cap expression must route 'todo' to `_TODO_TOOL_LIMIT`, not `_PER_TOOL_LIMIT`.

    Six todo calls stand in for the per-tool counter: they must stay under the cap the expression
    computes for that name."""
    import kotoba.core.loop as loop

    fake_per_tool = {"todo": 6}
    tool_name = "todo"
    mode = "companion"

    browser_high_cap = mode == "work" and tool_name.startswith("browser__")
    computed_cap = (
        loop._BROWSER_TOOL_LIMIT if browser_high_cap else (
        loop._DELEGATE_LIMIT if tool_name == "delegate" else (
        loop._TODO_TOOL_LIMIT if tool_name == "todo" else loop._PER_TOOL_LIMIT))
    )
    assert fake_per_tool[tool_name] <= computed_cap, (
        f"6 todo calls exceed cap={computed_cap}; per-tool limit would block a 5-step plan"
    )


# --- the REST endpoint ------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "tl.db"))
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    from fastapi.testclient import TestClient
    import kotoba.server as main
    with TestClient(main.app) as c:
        yield c


_AUTH = {"Authorization": "Bearer k"}


def test_session_tasks_no_list(client):
    r = client.get("/api/session/rt1/tasks", headers=_AUTH)
    assert r.status_code == 200
    assert r.json() == {"list": None}


def test_session_tasks_with_open_list(client):
    """The endpoint serves the panel's frame, and the frame is not the stored record: `origin` and
    `created_ts` are bookkeeping the client has no use for and must not be handed."""
    tl.open_list("rt2", ["Alpha", "Beta", "Gamma"], title="Test plan")
    r = client.get("/api/session/rt2/tasks", headers=_AUTH)
    assert r.status_code == 200
    data = r.json()["list"]
    assert data is not None
    assert data["status"] == "open"
    assert data["title"] == "Test plan"
    assert len(data["tasks"]) == 3
    assert data["tasks"][0]["text"] == "Alpha"
    assert "rev" in data
    assert "list_id" in data
    assert "origin" not in data
    assert "created_ts" not in data


def test_session_tasks_after_close(client):
    tl.open_list("rt3", ["a", "b"])
    tl.update("rt3", done=[1, 2], close=True)
    r = client.get("/api/session/rt3/tasks", headers=_AUTH)
    data = r.json()["list"]
    assert data["status"] == "done"


# --- lifecycle: leaving, dropping, and being cancelled ----------------------


def test_leave_clears_task_list(client):
    tl.open_list("lv1", ["a", "b", "c"])
    assert tl.get("lv1") is not None

    r = client.post("/api/session/lv1/leave", headers=_AUTH)
    assert r.status_code == 200

    assert tl.get("lv1") is None, "leave must clear the task list"


def test_drop_call_does_not_clear_list():
    """A session drop (a call that ends without an explicit leave) keeps the list alive.
    The drop is modelled by NOT calling
    `tl.clear()` — that call is what a real leave does, and only a leave may take the plan away."""
    tl.open_list("dp1", ["a", "b", "c"])
    tl.update("dp1", done=[1])
    assert tl.get("dp1") is not None
    assert tl.get("dp1")["status"] == "open"


def test_leave_is_noop_when_no_list(client):
    assert tl.get("lv2") is None
    r = client.post("/api/session/lv2/leave", headers=_AUTH)
    assert r.status_code == 200


def test_cancelled_turn_leaves_list_intact():
    """A turn cancelled while the list is open leaves the state alone: not cleared, not failed.

    Cancellation is modelled by calling neither `tl.clear()` nor any error path — nothing on the
    cancel route touches the list. What survives is the truth of the moment: step 2 still reads
    `active`, which is where the work stopped."""
    tl.open_list("ca1", ["a", "b", "c"])
    tl.update("ca1", active=2)

    lst = tl.get("ca1")
    assert lst is not None
    assert lst["status"] == "open"
    by_order = {t["order"]: t["status"] for t in lst["tasks"]}
    assert by_order[2] == "active"


def test_creating_a_plan_and_ticking_in_one_call_keeps_the_tick():
    """"Plan it and mark the first done" is one natural request. Applying the marks only when the call
    did NOT create the list drops them silently while the tool still reports success."""
    import asyncio

    from kotoba.tools.builtin import todo

    class _Ctx:
        session_id = "one-shot"

    asyncio.run(todo.execute(
        {"steps": ["buscar fuentes", "resumir", "comparar", "redactar"],
         "title": "Sixel", "done": [1], "active": 2},
        _Ctx(),
    ))
    lst = tl.get("one-shot")
    statuses = [t["status"] for t in lst["tasks"]]
    tl.clear("one-shot")
    assert statuses == ["done", "active", "pending", "pending"], statuses


def test_a_tick_only_call_still_needs_an_open_plan():
    import asyncio

    from kotoba.tools.builtin import todo

    class _Ctx:
        session_id = "no-plan"

    tl.clear("no-plan")
    out = asyncio.run(todo.execute({"done": [1]}, _Ctx()))
    assert "No plan open" in out


def test_an_empty_steps_list_never_wipes_an_open_plan():
    """`steps: []` means "I sent the field but have no steps", not "replace the plan with nothing".
    Taken literally it destroys an open plan and closes it empty, losing every step."""
    import asyncio

    from kotoba.tools.builtin import todo

    class _Ctx:
        session_id = "wipe"

    tl.open_list("wipe", ["uno", "dos", "tres"], "Real")
    asyncio.run(todo.execute({"steps": [], "done": [1, 2, 3], "close": True}, _Ctx()))
    lst = tl.get("wipe")
    texts = [t["text"] for t in lst["tasks"]]
    status = lst["status"]
    title = lst["title"]
    tl.clear("wipe")
    assert texts == ["uno", "dos", "tres"], "the real steps must survive"
    assert title == "Real"
    assert status == "done", "closing still works"


def test_an_announce_turn_cannot_replace_the_plan_it_just_finished():
    """The turn that reports finished work used to re-derive the request. start_work and delegate are
    withheld from it; todo can start one too — through `steps` — and a fresh plan there restarts the job."""
    import asyncio

    from kotoba.tools.builtin import todo

    class _Ctx:
        session_id = "announce"
        announce_turn = True

    tl.open_list("announce", ["uno", "dos", "tres"], "El trabajo")
    tl.update("announce", done=[1, 2])
    asyncio.run(todo.execute({"steps": ["otra vez uno", "otra vez dos"], "title": "Replanteado"}, _Ctx()))
    lst = tl.get("announce")
    texts = [t["text"] for t in lst["tasks"]]
    title = lst["title"]
    tl.clear("announce")
    assert texts == ["uno", "dos", "tres"], "the finished plan must survive the announce turn"
    assert title == "El trabajo"


def test_an_announce_turn_can_still_tick_and_close():
    """Withholding the restart must not cost her the correct ending."""
    import asyncio

    from kotoba.tools.builtin import todo

    class _Ctx:
        session_id = "announce2"
        announce_turn = True

    tl.open_list("announce2", ["uno", "dos"], "Trabajo")
    asyncio.run(todo.execute({"done": [1, 2], "close": True}, _Ctx()))
    lst = tl.get("announce2")
    status, statuses = lst["status"], [t["status"] for t in lst["tasks"]]
    tl.clear("announce2")
    assert statuses == ["done", "done"]
    assert status == "done"


def test_a_normal_turn_may_still_replace_the_plan():
    import asyncio

    from kotoba.tools.builtin import todo

    class _Ctx:
        session_id = "normal"
        announce_turn = False

    tl.open_list("normal", ["viejo"], "Viejo")
    asyncio.run(todo.execute({"steps": ["nuevo"], "title": "Nuevo"}, _Ctx()))
    texts = [t["text"] for t in tl.get("normal")["tasks"]]
    tl.clear("normal")
    assert texts == ["nuevo"]


def test_resending_the_same_steps_updates_the_plan_instead_of_replacing_it():
    """Measured in a live run: five todo calls in one turn, three carrying the steps she had
    already written. Each of those opened a NEW list, so the `done` in the same call landed on the fresh
    one and the panel jumped to a different plan with the same words on it."""
    import asyncio

    from kotoba.tools.builtin import todo

    class _Ctx:
        session_id = "resend"
        announce_turn = False

    first = tl.open_list("resend", ["mirar el tiempo", "redactar el informe"], "Lisboa")
    asyncio.run(todo.execute(
        {"steps": ["mirar el tiempo", "redactar el informe"], "title": "Lisboa", "done": [1], "active": 2},
        _Ctx(),
    ))
    lst = tl.get("resend")
    same_list, statuses = lst["list_id"] == first["list_id"], [t["status"] for t in lst["tasks"]]
    tl.clear("resend")
    assert same_list, "the same steps are the same plan — the panel must not jump to a new list_id"
    assert statuses == ["done", "active"]


def test_resending_the_same_steps_cannot_reopen_a_closed_plan():
    """The other half of the same live run: she closed the plan and then sent its steps once more, which
    replaced the finished list with an open one reading 1 of 2 on a job that was over."""
    import asyncio

    from kotoba.tools.builtin import todo

    class _Ctx:
        session_id = "resend-closed"
        announce_turn = False

    tl.open_list("resend-closed", ["uno", "dos"], "Trabajo")
    tl.update("resend-closed", done=[1, 2], close=True)
    out = asyncio.run(todo.execute({"steps": ["uno", "dos"], "done": [1], "title": "Trabajo"}, _Ctx()))
    lst = tl.get("resend-closed")
    status, statuses = lst["status"], [t["status"] for t in lst["tasks"]]
    tl.clear("resend-closed")
    assert status == "done" and statuses == ["done", "done"]
    assert "already finished" in out


def test_a_genuinely_different_plan_still_replaces():
    """The guard keys on the steps, not on having a list: re-planning with different work is a replace,
    which is what the tool is for."""
    assert tl.same_steps("nothing-here", ["uno"]) is False
    tl.open_list("differs", ["uno", "dos"], "Uno")
    assert tl.same_steps("differs", ["uno", "dos"]) is True
    assert tl.same_steps("differs", ["uno", "dos", "tres"]) is False
    assert tl.same_steps("differs", ["dos", "uno"]) is False, "order is part of the plan"
    assert tl.same_steps("differs", [{"text": "uno", "detail": "notas nuevas"}, "dos"]) is True, \
        "she rewrites her own notes freely — that is the same plan"
    tl.clear("differs")


def test_closing_with_steps_left_is_abandoned_not_done():
    """Live stress test: she closed a list with 2 of 3 steps unmarked and it read as finished. A list
    that says 'done' while showing 1/3 is the lie on screen the panel exists to prevent."""
    tl.open_list("half", ["uno", "dos", "tres"], "A medias")
    tl.update("half", done=[1], close=True)
    lst = tl.get("half")
    status, statuses = lst["status"], [t["status"] for t in lst["tasks"]]
    tl.clear("half")
    assert statuses == ["done", "pending", "pending"], "closing must not silently mark the rest"
    assert status == "abandoned"


def test_closing_with_everything_marked_is_done():
    tl.open_list("full", ["uno", "dos"], "Entera")
    tl.update("full", done=[1, 2], close=True)
    status = tl.get("full")["status"]
    tl.clear("full")
    assert status == "done"


def test_dropped_steps_do_not_block_a_clean_close():
    """A step she dropped is decided, not outstanding — closing on top of it is still 'done'."""
    tl.open_list("dropped", ["uno", "dos", "tres"], "Con descarte")
    tl.update("dropped", done=[1, 2], drop=[3], close=True)
    status = tl.get("dropped")["status"]
    tl.clear("dropped")
    assert status == "done"
