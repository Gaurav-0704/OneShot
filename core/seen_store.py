"""
core/seen_store.py — persistent cross-run "seen jobs" memory (Phase 2).

JobSpy dedup only works within a single run, and only applied_ids persist
across runs, so reposted / already-discovered jobs keep reappearing. This
store remembers every job we've surfaced before so each run shows only
genuinely-new postings.

Keys per job (any match = "seen"):
  - job_id              (board-specific id / url)
  - url_norm            (normalized job url)
  - content_hash        sha1(title | company | location)
  - coarse_key          sha1(title | company)   → catches reposts that only
                        changed location or got a fresh id

SQLite at outputs/seen_jobs.sqlite — additive, never touches existing CSVs.
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def _url_norm(url: str) -> str:
    u = (url or "").strip().lower()
    u = re.sub(r"[?#].*$", "", u)          # drop query/fragment
    u = re.sub(r"/+$", "", u)              # drop trailing slashes
    return u


def _sha1(*parts: str) -> str:
    return hashlib.sha1("|".join(_norm(p) for p in parts).encode("utf-8")).hexdigest()


def content_hash(job: dict) -> str:
    return _sha1(job.get("title", ""), job.get("company", ""), job.get("location", ""))


def coarse_key(job: dict) -> str:
    return _sha1(job.get("title", ""), job.get("company", ""))


class SeenStore:
    _DDL = """
    CREATE TABLE IF NOT EXISTS seen_jobs (
        job_id        TEXT,
        url_norm      TEXT,
        content_hash  TEXT,
        coarse_key    TEXT,
        title         TEXT,
        company       TEXT,
        location      TEXT,
        first_seen_at TEXT,
        last_seen_at  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_seen_jobid   ON seen_jobs(job_id);
    CREATE INDEX IF NOT EXISTS idx_seen_url     ON seen_jobs(url_norm);
    CREATE INDEX IF NOT EXISTS idx_seen_content ON seen_jobs(content_hash);
    CREATE INDEX IF NOT EXISTS idx_seen_coarse  ON seen_jobs(coarse_key);
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            for stmt in self._DDL.strip().split(";"):
                if stmt.strip():
                    c.execute(stmt)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── Queries ───────────────────────────────────────────────────────────────

    def _load_keys(self) -> tuple[set, set, set, set]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT job_id, url_norm, content_hash, coarse_key FROM seen_jobs"
            ).fetchall()
        ids   = {r["job_id"] for r in rows if r["job_id"]}
        urls  = {r["url_norm"] for r in rows if r["url_norm"]}
        chash = {r["content_hash"] for r in rows if r["content_hash"]}
        ckey  = {r["coarse_key"] for r in rows if r["coarse_key"]}
        return ids, urls, chash, ckey

    def split_new(self, jobs: list[dict]) -> tuple[list[dict], dict]:
        """Partition jobs into (new, stats). A job is NOT new when any of its
        keys was seen in a prior run. Reposts (same coarse key, new id) count
        as not-new."""
        ids, urls, chash, ckey = self._load_keys()
        new: list[dict] = []
        seen = reposts = 0
        for j in jobs:
            jid = (j.get("job_id") or "").strip()
            un  = _url_norm(j.get("url", ""))
            ch  = content_hash(j)
            ck  = coarse_key(j)
            if jid and jid in ids:
                seen += 1; continue
            if un and un in urls:
                seen += 1; continue
            if ch in chash:
                seen += 1; continue
            if ck in ckey:
                reposts += 1; continue
            new.append(j)
        return new, {"seen": seen, "reposts": reposts, "new": len(new)}

    def record(self, jobs: list[dict]) -> int:
        """Remember these jobs so future runs skip them. Returns rows written."""
        if not jobs:
            return 0
        now = datetime.now().isoformat(timespec="seconds")
        n = 0
        with self._conn() as c:
            for j in jobs:
                c.execute(
                    """INSERT INTO seen_jobs
                       (job_id, url_norm, content_hash, coarse_key,
                        title, company, location, first_seen_at, last_seen_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    ((j.get("job_id") or "").strip(), _url_norm(j.get("url", "")),
                     content_hash(j), coarse_key(j),
                     j.get("title", ""), j.get("company", ""), j.get("location", ""),
                     now, now),
                )
                n += 1
        return n
