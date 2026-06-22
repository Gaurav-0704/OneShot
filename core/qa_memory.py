"""
core/qa_memory.py
Q&A memory — two layers:

1. MODULE-LEVEL FUNCTIONS (preserved from appliers/base.py rescue):
   qa_path(), save_qa(), lookup_learned_answer(), mark_qa_submitted()
   These still write to outputs/learned_qa.json for backward compat.

2. QAStore (SQLite) — semantic cache for the AI Application Copilot:
   outputs/qa_store.sqlite  table: qa_answers
   Embeddings via OpenAI text-embedding-3-small when OPENAI_API_KEY is set;
   lexical Jaccard token-overlap fallback when it is not (no heavy new deps).
   On first init, migrates outputs/learned_qa.json rows (no data loss).

   Public API:
     store.search(question, top_k=5)  → [(record_dict, similarity_float)]
     store.upsert(rec)                → row_id
     store.mark_preferred(row_id)     → None
     store.list_for_job(job_id)       → [record_dict, ...]
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import struct
from datetime import datetime
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


# ── Normalisation helpers ──────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for key matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def _tokens(text: str) -> set[str]:
    return set(_norm(text).split())


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ── Embedding helpers ──────────────────────────────────────────────────────────

def _try_embed(text: str) -> bytes | None:
    """Return packed float32 embedding bytes, or None if unavailable."""
    import os
    key = os.environ.get("OPENAI_API_KEY", "").strip().strip("'").strip('"')
    if not key or key.startswith("sk-...") or key.endswith("..."):
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        resp = client.embeddings.create(
            model="text-embedding-3-small",
            input=[text[:8000]],
        )
        vec = resp.data[0].embedding
        return struct.pack(f"{len(vec)}f", *vec)
    except Exception as e:
        log.debug(f"QAStore embedding failed (using lexical fallback): {e}")
        return None


def _cosine(blob_a: bytes, blob_b: bytes) -> float:
    """Cosine similarity between two packed float32 blobs."""
    if not blob_a or not blob_b:
        return 0.0
    n = len(blob_a) // 4
    if n != len(blob_b) // 4:
        return 0.0
    a = struct.unpack(f"{n}f", blob_a)
    b = struct.unpack(f"{n}f", blob_b)
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if (na and nb) else 0.0


# ── QAStore ────────────────────────────────────────────────────────────────────

class QAStore:
    """
    SQLite-backed Q&A semantic cache.

    Schema:
      id INTEGER PK, question TEXT, question_norm TEXT,
      answer TEXT, answer_type TEXT,
      embedding BLOB (packed float32, nullable — lexical fallback used when NULL),
      job_id TEXT, company TEXT,
      confidence REAL,
      preferred INTEGER DEFAULT 0,
      approved INTEGER DEFAULT 0,
      created_at TEXT
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS qa_answers (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        question     TEXT    NOT NULL,
        question_norm TEXT   NOT NULL,
        answer       TEXT    NOT NULL,
        answer_type  TEXT,
        embedding    BLOB,
        job_id       TEXT,
        company      TEXT,
        confidence   REAL,
        preferred    INTEGER DEFAULT 0,
        approved     INTEGER DEFAULT 0,
        created_at   TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_qa_job    ON qa_answers(job_id);
    CREATE INDEX IF NOT EXISTS idx_qa_norm   ON qa_answers(question_norm);
    CREATE INDEX IF NOT EXISTS idx_qa_pref   ON qa_answers(preferred);
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    # ── Connection ────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            for stmt in self._DDL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    c.execute(stmt)
        self._migrate_json()

    # ── Migration ─────────────────────────────────────────────────────────────

    def _migrate_json(self) -> None:
        """One-time migration of outputs/learned_qa.json into SQLite."""
        json_path = self.db_path.parent / "learned_qa.json"
        if not json_path.exists():
            return
        with self._conn() as c:
            if c.execute("SELECT COUNT(*) FROM qa_answers").fetchone()[0] > 0:
                return   # already migrated
        try:
            entries = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            for e in entries:
                q = (e.get("question") or "").strip()
                a = (e.get("answer") or "").strip()
                if not q or not a:
                    continue
                c.execute(
                    """INSERT INTO qa_answers
                       (question, question_norm, answer, answer_type,
                        job_id, company, confidence, approved, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (q, _norm(q), a, "short_text", "",
                     e.get("company", ""), 70.0,
                     1 if e.get("submitted") else 0,
                     e.get("saved_at") or now),
                )
        log.info(f"QAStore: migrated {len(entries)} entries from learned_qa.json")

    # ── Public API ────────────────────────────────────────────────────────────

    def search(self, question: str, top_k: int = 5) -> list[tuple[dict, float]]:
        """Return top_k most-similar records with their similarity scores.
        Uses embedding cosine when available; falls back to Jaccard overlap."""
        q_embed = _try_embed(question)
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM qa_answers ORDER BY created_at DESC LIMIT 500"
            ).fetchall()
        if not rows:
            return []

        scored: list[tuple[dict, float]] = []
        for row in rows:
            rec = dict(row)
            if q_embed and rec.get("embedding"):
                sim = _cosine(q_embed, rec["embedding"])
            else:
                sim = _jaccard(question, rec["question"])
            scored.append((rec, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def upsert(self, rec: dict) -> int:
        """Insert or update a Q&A record. Returns the row id."""
        question   = (rec.get("question") or "").strip()
        answer     = (rec.get("answer") or "").strip()
        if not question or not answer:
            raise ValueError("question and answer are required")

        q_norm   = _norm(question)
        embedding = _try_embed(question)
        now      = datetime.now().isoformat(timespec="seconds")

        with self._conn() as c:
            existing = c.execute(
                "SELECT id FROM qa_answers WHERE question_norm=? AND job_id=?",
                (q_norm, rec.get("job_id", "")),
            ).fetchone()
            if existing:
                c.execute(
                    """UPDATE qa_answers SET
                       answer=?, answer_type=?, embedding=?,
                       confidence=?, preferred=?, approved=?
                       WHERE id=?""",
                    (answer, rec.get("answer_type", "short_text"),
                     embedding or rec.get("embedding"),
                     rec.get("confidence", 70.0),
                     int(rec.get("preferred", 0)),
                     int(rec.get("approved", 0)),
                     existing["id"]),
                )
                return existing["id"]
            else:
                cur = c.execute(
                    """INSERT INTO qa_answers
                       (question, question_norm, answer, answer_type, embedding,
                        job_id, company, confidence, preferred, approved, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (question, q_norm, answer,
                     rec.get("answer_type", "short_text"), embedding,
                     rec.get("job_id", ""), rec.get("company", ""),
                     rec.get("confidence", 70.0),
                     int(rec.get("preferred", 0)),
                     int(rec.get("approved", 0)),
                     now),
                )
                return cur.lastrowid  # type: ignore[return-value]

    def mark_preferred(self, row_id: int) -> None:
        """Mark a row as preferred and approved."""
        with self._conn() as c:
            c.execute(
                "UPDATE qa_answers SET preferred=1, approved=1 WHERE id=?",
                (row_id,),
            )

    def list_for_job(self, job_id: str) -> list[dict]:
        """Return all answers stored for a specific job_id."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM qa_answers WHERE job_id=? ORDER BY preferred DESC, id ASC",
                (job_id,),
            ).fetchall()
        return [dict(r) for r in rows]


# ── Legacy JSON-file helpers (kept for backward compat) ───────────────────────

def qa_path() -> Path:
    return Path("outputs") / "learned_qa.json"


def save_qa(question: str, answer: str, company: str, submitted: bool = False) -> None:
    """Append or update one Q&A pair in outputs/learned_qa.json."""
    try:
        p = qa_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        entries: list = []
        if p.exists():
            try:
                entries = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                entries = []
        q_key = question.strip().lower()[:120]
        for e in entries:
            if (e.get("question") or "").strip().lower()[:120] == q_key:
                e["answer"] = answer
                e["submitted"] = e.get("submitted") or submitted
                e["company"] = company or e.get("company", "")
                break
        else:
            entries.append({
                "question": question,
                "answer": answer,
                "company": company,
                "submitted": submitted,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            })
        p.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except Exception:
        pass


def lookup_learned_answer(question: str) -> str | None:
    """Check questions.yaml learned_answers for a matching prior answer."""
    try:
        cfg_path = Path("config") / "questions.yaml"
        if not cfg_path.exists():
            return None
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        learned = cfg.get("learned_answers", {})
        if not learned:
            return None
        q_key = re.sub(r"\s+", " ", question.strip().lower())[:120]
        if q_key in learned:
            return str(learned[q_key].get("answer", "") or "")
        for saved_q, val in learned.items():
            if q_key in saved_q or saved_q in q_key:
                return str(val.get("answer", "") or "")
    except Exception:
        pass
    return None


def mark_qa_submitted(company: str) -> None:
    """Mark all Q&A entries for this company as submitted=True."""
    try:
        p = qa_path()
        if not p.exists():
            return
        entries = json.loads(p.read_text(encoding="utf-8"))
        for e in entries:
            if (e.get("company") or "").strip().lower() == company.strip().lower():
                e["submitted"] = True
        p.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except Exception:
        pass
