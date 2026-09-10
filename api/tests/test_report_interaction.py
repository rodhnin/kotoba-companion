"""The report pipeline, the interaction layer (input and approval cards), and saved keys."""
from __future__ import annotations

import asyncio

from kotoba.core import events, interaction, reports
from kotoba.db.database import Database
from kotoba.tools import ToolContext
from kotoba.tools.action import make_report


# --- report -----------------------------------------------------------------

def test_make_report_fills_template_and_stores_and_emits():
    """The template comes back with every placeholder filled, the report is stored, and a
    `report_ready` frame reaches the events queue."""
    async def go():
        events.register("rs")
        ctx = ToolContext(db=None, session_id="rs", mode="work")
        msg = await make_report.execute(
            {
                "title": "My Site",
                "summary": "Built a portfolio.",
                "steps": ["scaffolded", "styled"],
                "results": ["it works"],
                "files": ["index.html — the page"],
                "next_steps": ["deploy"],
            },
            ctx,
        )
        html = reports.get_report("rs")
        frame = events.event_queues["rs"].get_nowait()
        events.unregister("rs")
        return msg, html, frame

    msg, html, frame = asyncio.run(go())
    assert "Report ready" in msg
    assert html and "My Site" in html and "Built a portfolio." in html
    assert "<li>scaffolded</li>" in html and "index.html — the page" in html
    assert "{{" not in html
    assert frame["type"] == "task" and frame["kind"] == "report_ready" and frame["title"] == "My Site"


def test_make_report_requires_title_and_summary():
    async def go():
        ctx = ToolContext(db=None, session_id="rs2", mode="work")
        return await make_report.execute({"title": "", "summary": ""}, ctx)

    assert asyncio.run(go()) is None


def test_report_escapes_html_in_content():
    """Content reaches the report escaped, never injected as live markup."""
    async def go():
        ctx = ToolContext(db=None, session_id="rs3", mode="work")
        await make_report.execute({"title": "<script>x</script>", "summary": "a & b <b>"}, ctx)
        return reports.get_report("rs3")

    html = asyncio.run(go())
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


# --- interaction: input + approval ------------------------------------------

def test_request_input_resolves_with_typed_value():
    async def go():
        events.register("is1")

        async def answer_later():
            await asyncio.sleep(0.05)
            interaction.resolve("is1", {"value": "https://repo"})

        asyncio.create_task(answer_later())
        val = await interaction.request_input("is1", "Paste the repo link", "link", timeout=5)
        events.unregister("is1")
        return val

    assert asyncio.run(go()) == "https://repo"


def test_request_approval_yes_no():
    async def go():
        events.register("is2")

        async def approve():
            await asyncio.sleep(0.05)
            interaction.resolve("is2", {"approved": True})

        asyncio.create_task(approve())
        ok, _always = await interaction.request_approval("is2", "delete build/", timeout=5)
        events.unregister("is2")
        return ok

    assert asyncio.run(go()) is True


def test_request_input_times_out_to_none():
    async def go():
        events.register("is3")
        v = await interaction.request_input("is3", "anything", "text", timeout=0.2)
        events.unregister("is3")
        return v

    assert asyncio.run(go()) is None


# --- saved keys: stored backend-only --------------------------------------

def test_saved_key_roundtrip(tmp_path):
    """A saved key round-trips by name, and the listing used for management never carries the value."""
    async def go():
        db = Database("sqlite:///" + str(tmp_path / "keys.db"))
        await db.connect()
        try:
            await db.save_key("openai", "sk-secret-123")
            names = await db.list_key_names()
            val = await db.get_key("openai")
            return names, val
        finally:
            await db.close()

    names, val = asyncio.run(go())
    assert [n["name"] for n in names] == ["openai"]
    assert val == "sk-secret-123"
    assert all("value" not in n for n in names)
