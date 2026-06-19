"""
webapp/routes/files.py — serve generated PDFs and per-job artifact folders.

  GET  /api/files/tailored                       → list of tailored job folders
  GET  /api/files/tailored/<slug>/<filename>     → the actual file
  POST /api/files/open-folder {path}             → open folder in OS Explorer
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request, send_from_directory

bp = Blueprint("files", __name__)


@bp.route("/tailored", methods=["GET"])
def list_tailored():
    """Return one entry per per-job folder under outputs/tailored/."""
    root: Path = current_app.config["ROOT"]
    base = root / "outputs" / "tailored"
    if not base.exists():
        return jsonify({"folders": []})
    out = []
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        files = sorted(p.name for p in d.iterdir() if p.is_file())
        out.append({
            "slug": d.name,
            "files": files,
            "modified": d.stat().st_mtime,
        })
    return jsonify({"folders": out})


@bp.route("/tailored/<slug>/<path:filename>", methods=["GET"])
def serve_tailored(slug: str, filename: str):
    root: Path = current_app.config["ROOT"]
    base = (root / "outputs" / "tailored" / slug).resolve()
    # Path traversal guard
    if not str(base).startswith(str((root / "outputs" / "tailored").resolve())):
        abort(403)
    if not base.is_dir():
        abort(404)
    return send_from_directory(base, filename)


@bp.route("/open-folder", methods=["POST"])
def open_folder():
    """Open a tailored-output folder in the OS file explorer.

    Body: {"path": "<absolute path under outputs/tailored/>"}
    Only paths under outputs/tailored/ are allowed (path traversal guard).
    Returns: {"ok": true} or {"ok": false, "error": "..."}.
    """
    body = request.get_json(silent=True) or {}
    raw_path = (body.get("path") or "").strip()
    if not raw_path:
        return jsonify({"ok": False, "error": "path is required"}), 400

    root: Path = current_app.config["ROOT"]
    tailored_root = (root / "outputs" / "tailored").resolve()

    try:
        target = Path(raw_path).resolve()
    except Exception:
        return jsonify({"ok": False, "error": "invalid path"}), 400

    # Security: only allow paths inside outputs/tailored/
    if not str(target).startswith(str(tailored_root)):
        return jsonify({"ok": False, "error": "path not allowed"}), 403

    if not target.exists():
        return jsonify({"ok": False, "error": "path does not exist"}), 404

    try:
        sysname = platform.system()
        if sysname == "Windows":
            os.startfile(str(target))          # type: ignore[attr-defined]
        elif sysname == "Darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
