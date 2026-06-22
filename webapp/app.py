"""
webapp/app.py — Flask app factory.

Single SPA at "/", REST + SSE under "/api/*".
"""
from __future__ import annotations

import logging
import socket
from pathlib import Path

from flask import Flask, render_template
from flask_cors import CORS

from webapp.pipeline_runner import PipelineRunner
from webapp.routes.api import bp as api_bp
from webapp.routes.copilot import bp as copilot_bp
from webapp.routes.files import bp as files_bp
from webapp.routes.history import bp as history_bp
from webapp.routes.pipeline import bp as pipeline_bp


def create_app(root: Path) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    CORS(app)
    app.config["ROOT"] = root
    app.config["RUNNER"] = PipelineRunner(root)

    from core.usage import configure as configure_usage
    configure_usage(root / "outputs" / "api_usage.json")

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
