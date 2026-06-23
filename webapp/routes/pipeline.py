"""
webapp/routes/pipeline.py - start / stream the agent pipeline.
"""
from __future__ import annotations

import json

from flask import Blueprint, Response, current_app, jsonify, request

bp = Blueprint("pipeline", __name__)


@bp.route("/start", methods=["POST"])
def start():
    runner = current_app.config["RUNNER"]
    if runner.is_running():
        return jsonify({"ok": True, "run_id": runner.run_id, "already_running": True})

    # Block start if profile isn't complete
    from agents.profile import ProfileAgent
    root = current_app.config["ROOT"]
    val = ProfileAgent(root / "config").validate_profile()
    if not val["is_complete"]:
        return jsonify({
            "ok": False,
            "error": "profile_incomplete",
            "message": "Fill in the required profile fields before running.",
            "missing_required": val["missing_required"],
            "completeness_pct": val["completeness_pct"],
        }), 400

    options = request.get_json(silent=True) or {}
    run_id = runner.start(options)
    return jsonify({"ok": True, "run_id": run_id})


@bp.route("/state", methods=["GET"])
def state():
    runner = current_app.config["RUNNER"]
    # Auto-heal: thread died without cleanup (crash / force-kill)
    if runner.status == "running" and not runner.is_running():
        runner.status = "error"
    return jsonify({
        "status": runner.status,
        "running": runner.is_running(),
        "run_id": runner.run_id,
        "summary": runner.summary,
    })


@bp.route("/stop", methods=["POST"])
def stop():
    """Ask the running pipeline to stop at the next safe checkpoint."""
    runner = current_app.config["RUNNER"]
    if not runner.is_running():
        return jsonify({"ok": True, "running": False, "message": "no run in progress"})
    runner.request_stop()
    return jsonify({"ok": True, "running": True, "message": "stop requested"})


@bp.route("/errors", methods=["GET"])
def errors():
    """All WARNING / ERROR log lines captured during recent runs."""
    runner = current_app.config["RUNNER"]
    return jsonify({"count": len(runner.errors), "errors": list(runner.errors)})


@bp.route("/errors/clear", methods=["POST"])
def errors_clear():
    runner = current_app.config["RUNNER"]
    runner.errors.clear()
    return jsonify({"ok": True})



@bp.route("/stream", methods=["GET"])
def stream():
    """Server-Sent Events stream. direct_passthrough=True bypasses Werkzeug
    response buffering so events reach the browser in real time."""
    runner = current_app.config["RUNNER"]

    def gen():
        # MUST yield bytes when direct_passthrough=True, else Werkzeug raises
        # AssertionError("applications must write bytes") and the SSE crashes.
        yield b": connected\n\n"
        for ev in runner.stream_events():
            yield f"data: {json.dumps(ev)}\n\n".encode("utf-8")

    resp = Response(gen(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Connection"] = "keep-alive"
    resp.direct_passthrough = True
    return resp


@bp.route("/discovery/start", methods=["POST"])
def discovery_start():
    """Start continuous background discovery (Phase 3)."""
    bg = current_app.config.get("BG_DISCOVERY")
    if not bg:
        return jsonify({"ok": False, "error": "background discovery unavailable"}), 500
    body = request.get_json(silent=True) or {}
    interval = body.get("interval_min")
    return jsonify({"ok": True, **bg.start(interval_min=interval)})


@bp.route("/discovery/stop", methods=["POST"])
def discovery_stop():
    bg = current_app.config.get("BG_DISCOVERY")
    if not bg:
        return jsonify({"ok": False, "error": "background discovery unavailable"}), 500
    return jsonify({"ok": True, **bg.stop()})


@bp.route("/discovery/status", methods=["GET"])
def discovery_status():
    bg = current_app.config.get("BG_DISCOVERY")
    if not bg:
        return jsonify({"ok": False, "enabled": False, "running": False})
    return jsonify({"ok": True, **bg.status()})


@bp.route("/resume", methods=["POST"])
def resume():
    """Resume the last stopped run from its checkpoint.
    Skips scraping/scoring — uses last_discovered.json.
    Skips jobs already in applied/pending/failed CSVs.
    Accepts the same options as /start (dry_run, pause_before_submit, etc.)."""
    runner = current_app.config["RUNNER"]
    if runner.is_running():
        return jsonify({"ok": True, "run_id": runner.run_id, "already_running": True})

    root = current_app.config["ROOT"]
    snap = root / "outputs" / "last_discovered.json"
    if not snap.exists():
        return jsonify({"ok": False, "error": "no_checkpoint",
                        "message": "No previous run found. Run the pipeline first."}), 400

    options = request.get_json(silent=True) or {}
    # Force cache usage so we skip scraping+scoring
    options["use_cache"] = True
    options.setdefault("dry_run", True)
    options.setdefault("pause_before_submit", True)

    run_id = runner.start(options)
    return jsonify({"ok": True, "run_id": run_id, "resumed": True})
