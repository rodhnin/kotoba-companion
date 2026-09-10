"""In-memory store of the latest report HTML per session (served to the ReportPanel viewer).

Reports are artifacts of a work turn — kept in memory keyed by session, served via
GET /api/session/{id}/report and rendered in the frontend's iframe viewer. (No disk write needed; the
file-tools already persist anything durable in the workspace.)
"""
from __future__ import annotations

_reports: dict[str, str] = {}
_last_title: dict[str, str] = {}  # session_id -> normalized title of the last report made (double-call dedupe)


def set_report(session_id: str | None, html: str) -> None:
    if session_id:
        _reports[session_id] = html


def get_report(session_id: str | None) -> str | None:
    return _reports.get(session_id) if session_id else None


def clear_report(session_id: str | None) -> None:
    """Drop a session's report. NO PRODUCTION CALLER — only tests, for isolation. Nothing evicts
    `_reports`, so a long-lived process keeps one report per session it ever served; that is the
    bound to reconsider if it ever matters, not this function."""
    if session_id:
        _reports.pop(session_id, None)
        _last_title.pop(session_id, None)


def _norm(title: str) -> str:
    return " ".join((title or "").lower().split())


def already_made(session_id: str | None, title: str) -> bool:
    """True if a report with this SAME title was just made for this session — so a reflexive second
    make_report call for the same work doesn't produce a duplicate file + memory note."""
    return bool(session_id) and bool(_norm(title)) and _last_title.get(session_id) == _norm(title)


def note_made(session_id: str | None, title: str) -> None:
    if session_id and _norm(title):
        _last_title[session_id] = _norm(title)
