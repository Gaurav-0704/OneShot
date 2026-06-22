"""
webapp/routes/history.py — Read-only history endpoints.

GET  /api/history                          → list of all runs grouped by date
GET  /api/history/<date>/<int:task_number> → rows + meta for one run
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify

bp = Blueprint("history", __name__)


def _root() -> Path:
    return current_app.config["ROOT"]


@bp.route("", methods=["GET"])
def list_history():
    """Return all archived runs, newest-first, grouped by date."""
    from agents.history import HistoryAgent
    agent = HistoryAgent(_root())
    runs  = agent.list_runs()
    # Group by date for the UI
    by_date: dict[str, list] = {}
    for r in runs:
        by_date.setdefault(r["date"], []).append(r)
    grouped = [
        {"date": date, "tasks": tasks}
        for date, tasks in by_date.items()
    ]
    return jsonify({"ok": True, "runs": runs, "grouped": grouped})


@bp.route("/<date>/<int:task_number>", methods=["GET"])
def get_run(date: str, task_number: int):
    """Return rows and meta for one specific run."""
    from agents.history import HistoryAgent
    agent = HistoryAgent(_root())
    rows  = agent.get_run(date, task_number)
    meta  = agent.get_run_meta(date, task_number)
    # Enrich rows the same way the pending endpoint does
    for r in rows:
        files = [f.strip() for f in (r.get("files_attached") or "").split(",") if f.strip()]
        r["apply_url"]   = r.get("url", "")
        r["resume_path"] = next(
            (f[7:] for f in files if f.startswith("resume:")),
            r.get("resume_pdf", ""),
        )
        r["cover_path"]  = next(
            (f[6:] for f in files if f.startswith("cover:")),
            r.get("cover_pdf", ""),
        )
        r["folder_path"] = r.get("folder", "")
    return jsonify({"ok": True, "meta": meta, "count": len(rows), "rows": rows})
