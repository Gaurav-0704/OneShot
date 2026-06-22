"""
wsgi.py — Gunicorn entry point for Railway / production deployments.

Gunicorn imports `application` from this module.
We use 1 worker + threads so the background pipeline thread and SSE
streaming both work correctly (multi-worker would split state).
"""
from pathlib import Path
from webapp.app import create_app

application = create_app(Path(__file__).resolve().parent)
app = application  # Flask dev server fallback alias
