"""
webapp/app.py — Flask app factory.

Single SPA at "/", REST + SSE under "/api/*".
"""
from __future__ import annotations

import hashlib
import logging
import os
import socket
from pathlib import Path

from flask import (
    Flask, jsonify, redirect, render_template, render_template_string,
    request, session, url_for,
)
from flask_cors import CORS

from webapp.pipeline_runner import PipelineRunner
from webapp.routes.api import bp as api_bp
from webapp.routes.copilot import bp as copilot_bp
from webapp.routes.files import bp as files_bp
from webapp.routes.history import bp as history_bp
from webapp.routes.pipeline import bp as pipeline_bp


def _bootstrap_resume(root: Path) -> None:
    """Download master_resume.pdf from RESUME_URL env var if not present.

    Set RESUME_URL in Railway env vars to a public direct-download link
    (Google Drive, Dropbox, GitHub raw, etc.) so the resume is available
    without committing a PDF to the repo.
    """
    import os
    resume_path = root / "config" / "master_resume.pdf"
    if resume_path.exists():
        return
    url = os.environ.get("RESUME_URL", "").strip()
    if not url:
        logging.getLogger(__name__).warning(
            "config/master_resume.pdf not found and RESUME_URL not set. "
            "Upload a resume via the Profile tab or set RESUME_URL in Railway env vars."
        )
        return
    try:
        import urllib.request
        logging.getLogger(__name__).info("Downloading resume from RESUME_URL ...")
        urllib.request.urlretrieve(url, resume_path)
        logging.getLogger(__name__).info(f"Resume saved to {resume_path}")
    except Exception as e:
        logging.getLogger(__name__).error(f"Failed to download resume: {e}")


_LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>OneShot — Sign in</title>
<style>
  body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
       background:#070b12;color:#e7ecf3;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  .box{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.10);border-radius:14px;
       padding:30px 28px;width:300px;text-align:center;backdrop-filter:blur(20px)}
  h1{font-size:19px;margin:0 0 4px;letter-spacing:-.02em}
  p{color:#8a97a8;font-size:13px;margin:0 0 18px}
  input{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.12);
        background:#0d1320;color:#e7ecf3;font-size:14px;margin-bottom:10px}
  button{width:100%;padding:11px;border:none;border-radius:8px;background:#4f8ef7;color:#fff;font-weight:700;
         font-size:14px;cursor:pointer}
  .err{color:#ff6b6b;font-size:12.5px;margin-bottom:10px;min-height:16px}
</style></head><body>
  <form class="box" method="post" action="/login">
    <h1>OneShot</h1><p>Enter the access password to continue.</p>
    <div class="err">{{ error }}</div>
    <input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password"/>
    <button type="submit">Sign in</button>
  </form>
</body></html>"""

# Paths reachable without logging in.
_PUBLIC_PATHS = {"/login", "/logout", "/healthz", "/ping"}


def _install_auth(app: Flask, password: str) -> None:
    """Gate every route behind a shared password when APP_PASSWORD is set."""

    @app.before_request
    def _require_login():
        if not password:
            return                      # auth disabled (local dev)
        p = request.path
        if p in _PUBLIC_PATHS or p.startswith("/static/"):
            return
        if session.get("authed"):
            return
        # Unauthenticated: redirect page loads to the login form, 401 the API.
        if p.startswith("/api/"):
            return jsonify({"error": "unauthorized", "message": "Login required"}), 401
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not password:
            return redirect("/")
        error = ""
        if request.method == "POST":
            if (request.form.get("password") or "") == password:
                session["authed"] = True
                session.permanent = True
                return redirect("/")
            error = "Incorrect password."
        return render_template_string(_LOGIN_PAGE, error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect("/login" if password else "/")


def create_app(root: Path) -> Flask:
    # Ensure output directories exist on a fresh deployment
    for d in ["outputs", "outputs/logs", "outputs/tailored", "config"]:
        (root / d).mkdir(parents=True, exist_ok=True)

    _bootstrap_resume(root)

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )

    # CORS: same-origin SPA needs none. Only open cross-origin when explicitly
    # asked (ALLOWED_ORIGIN), never a wildcard on a key-holding server.
    allowed_origin = os.environ.get("ALLOWED_ORIGIN", "").strip()
    if allowed_origin:
        CORS(app, origins=[allowed_origin], supports_credentials=True)

    # Optional password gate. When APP_PASSWORD is set, every page/API requires
    # login; when unset (local dev) the app stays open. Session key is derived
    # from the password so logins survive restarts without extra config.
    app_password = os.environ.get("APP_PASSWORD", "").strip()
    app.secret_key = (
        os.environ.get("SECRET_KEY", "").strip()
        or (hashlib.sha256(("oneshot:" + app_password).encode()).hexdigest() if app_password else os.urandom(24).hex())
    )
    _install_auth(app, app_password)

    app.config["ROOT"] = root
    runner = PipelineRunner(root)
    app.config["RUNNER"] = runner

    from webapp.background import BackgroundDiscovery
    app.config["BG_DISCOVERY"] = BackgroundDiscovery(root, runner=runner)

    from core.usage import configure as configure_usage
    configure_usage(root / "outputs" / "api_usage.json")

    from core.cache import configure as configure_cache
    configure_cache(root)

    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(copilot_bp, url_prefix="/api/copilot")
    app.register_blueprint(history_bp, url_prefix="/api/history")
    app.register_blueprint(pipeline_bp, url_prefix="/api/pipeline")
    app.register_blueprint(files_bp, url_prefix="/api/files")

    @app.route("/")
    def index():
        try:
            return render_template("index.html")
        except Exception as e:
            logging.getLogger(__name__).exception("index render failed")
            return "<pre>OneShot error: " + str(e) + "</pre>", 500

    @app.route("/healthz")
    def healthz():
        return {"ok": True}

    @app.route("/ping")
    def ping():
        return "OneShot OK", 200, {"Content-Type": "text/plain"}

    logging.getLogger("werkzeug").setLevel(logging.INFO)
    return app


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def run_server(
    root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 5001,
    open_browser: bool = True,
) -> None:
    app = create_app(root)

    if _port_in_use(host, port):
        old = port
        port = port + 1
        while _port_in_use(host, port) and port < old + 10:
            port += 1
        print("\n  WARNING  Port " + str(old) + " already in use, switching to " + str(port))
        print("           (Close the terminal that started it to free it)\n")

    url = "http://" + host + ":" + str(port)

    if open_browser:
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    print("\n    OneShot UI  ->  " + url + "\n")

    app.run(host=host, port=port, debug=True, threaded=True, use_reloader=False)
