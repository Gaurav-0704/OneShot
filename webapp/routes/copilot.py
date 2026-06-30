"""
webapp/routes/copilot.py — AI Application Copilot REST endpoints.

POST /api/copilot/answer        {question, job_id?}
     → {answer, confidence_score, answer_type, source, needs_review}

POST /api/copilot/save          {question, answer, job_id?, preferred?}
     → {ok, row_id}

POST /api/copilot/regenerate    {question, job_id?, instructions?}
     → {answer, confidence_score, answer_type, source, needs_review}

GET  /api/copilot/job/<job_id>  → {requirements, extra_docs, prebaked_answers}
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("copilot", __name__)


def _root() -> Path:
    return current_app.config["ROOT"]


def _build_profile():
    """Build a UserProfile from config YAML (cheap; no browser, no GitHub fetch)."""
    from agents.profile import ProfileAgent
    return ProfileAgent(_root() / "config").build()


def _build_agent():
    from agents.copilot import CopilotAgent
    profile = _build_profile()
    return CopilotAgent(profile, root=_root())


def _job_for_id(job_id: str) -> dict:
    """Find the job record in pending_review.csv or last_discovered.json."""
    # Try pending_review.csv first
    pcsv = _root() / "outputs" / "pending_review.csv"
    if pcsv.exists():
        with pcsv.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("job_id") == job_id:
                    job = {
                        "job_id":   job_id,
                        "title":    row.get("title", ""),
                        "company":  row.get("company", ""),
                        "location": row.get("location", ""),
                        "url":      row.get("url", ""),
                        "site":     row.get("site", ""),
                        "folder":   row.get("folder", ""),
                        "description": "",
                    }
                    # Phase 5: load full JD + research context saved at package
                    # time so LIVE questions have the same context as prebake.
                    ctx = _job_context(row.get("folder", ""))
                    if ctx:
                        job["description"] = ctx.get("description", "") or job["description"]
                        job["enriched_description"] = ctx.get("enriched_description", "")
                        job["research_notes"] = ctx.get("research_notes", "")
                    return job
    # Fallback: last_discovered.json
    snap = _root() / "outputs" / "last_discovered.json"
    if snap.exists():
        try:
            rows = json.loads(snap.read_text(encoding="utf-8"))
            for r in rows:
                if r.get("job_id") == job_id:
                    return r
        except Exception:
            pass
    return {"job_id": job_id}


def _job_context(folder: str) -> dict:
    """Load job_context.json (full JD + research notes) from a job folder."""
    if not folder:
        return {}
    p = Path(folder) / "job_context.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _copilot_data_for_job(job: dict) -> dict:
    """Load copilot_data.json from the job's folder if it exists."""
    folder = job.get("folder", "")
    if not folder:
        return {}
    p = Path(folder) / "copilot_data.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/answer", methods=["POST"])
def copilot_answer():
    """Generate an answer for a question, optionally scoped to a job."""
    body     = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    job_id   = (body.get("job_id") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "question is required"}), 400
    try:
        agent  = _build_agent()
        job    = _job_for_id(job_id) if job_id else {}
        result = agent.generate(question, job)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/regenerate", methods=["POST"])
def copilot_regenerate():
    """Force a fresh LLM answer, optionally with extra instructions."""
    body         = request.get_json(silent=True) or {}
    question     = (body.get("question") or "").strip()
    job_id       = (body.get("job_id") or "").strip()
    instructions = (body.get("instructions") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "question is required"}), 400
    try:
        agent  = _build_agent()
        job    = _job_for_id(job_id) if job_id else {}
        result = agent.regenerate(question, job, instructions=instructions)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/save", methods=["POST"])
def copilot_save():
    """Persist a (question, answer) pair; optionally mark as preferred."""
    body      = request.get_json(silent=True) or {}
    question  = (body.get("question") or "").strip()
    answer    = (body.get("answer") or "").strip()
    job_id    = (body.get("job_id") or "").strip()
    preferred = bool(body.get("preferred", False))
    if not question or not answer:
        return jsonify({"ok": False, "error": "question and answer are required"}), 400
    try:
        agent = _build_agent()
        job   = _job_for_id(job_id) if job_id else {}
        row_id = agent.save(question, answer, job, preferred=preferred)
        return jsonify({"ok": True, "row_id": row_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/job/<job_id>", methods=["GET"])
def copilot_job(job_id: str):
    """Return prebaked copilot data for a specific job."""
    try:
        job  = _job_for_id(job_id)
        data = _copilot_data_for_job(job)
        # Also merge any answers stored in qa_store
        from core.qa_memory import QAStore
        store = QAStore(_root() / "outputs" / "qa_store.sqlite")
        stored = store.list_for_job(job_id)
        # Prefer copilot_data.json prebaked_answers; supplement with store records
        prebaked = data.get("prebaked_answers", [])
        if not prebaked and stored:
            prebaked = [
                {
                    "question":         r["question"],
                    "answer":           r["answer"],
                    "confidence_score": int(r.get("confidence") or 70),
                    "answer_type":      r.get("answer_type") or "short_text",
                    "source":           "cached",
                    "needs_review":     not bool(r.get("approved")),
                }
                for r in stored
            ]
        return jsonify({
            "ok":               True,
            "job_id":           job_id,
            "requirements":     data.get("requirements", {}),
            "extra_docs":       (data.get("requirements") or {}).get("extra_docs", []),
            "prebaked_answers": prebaked,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
