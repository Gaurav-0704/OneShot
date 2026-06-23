"""
core/cache.py — tiny keyed JSON disk cache under outputs/cache/ (Phase 4).

Used to memoize slow, idempotent work across runs: company-about fetches,
research briefs, and GitHub enrichment. Keeps full upfront generation fast
without changing what gets generated.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path("outputs") / "cache"


def configure(root: Path) -> None:
    """Point the cache at <root>/outputs/cache (called from the app factory)."""
    global _ROOT
    _ROOT = Path(root) / "outputs" / "cache"


def _path(namespace: str, key: str) -> Path:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return _ROOT / namespace / f"{h}.json"


def get(namespace: str, key: str, *, ttl_seconds: int | None = None):
    """Return cached value or None (also None when expired)."""
    p = _path(namespace, key)
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if ttl_seconds is not None and (time.time() - blob.get("_ts", 0)) > ttl_seconds:
        return None
    return blob.get("value")


def set(namespace: str, key: str, value) -> None:
    p = _path(namespace, key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"_ts": time.time(), "value": value}, default=str),
                     encoding="utf-8")
    except Exception as e:
        log.debug(f"cache write failed [{namespace}/{key[:40]}]: {e}")
