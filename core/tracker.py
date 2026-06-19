"""
core/tracker.py
CSV-based application tracking. Two files:
  - outputs/applied_jobs.csv  - successful submissions
  - outputs/failed_jobs.csv   - failures with stack trace + screenshot path

Adapted from AutoApplyLinkedIn's submitted_jobs() / failed_job() helpers
but cleaner: pandas-friendly, handles concurrent runs by re-reading on each call.
"""
from __future__ import annotations

import csv
import logging
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Allow up to 1MB per CSV field (LinkedIn JDs + AI answers can be huge)
csv.field_size_limit(min(sys.maxsize, 2**30))

APPLIED_HEADERS = [
    "applied_at", "site", "job_id", "title", "company", "location",
    "url", "fit_score", "fit_reason", "resume_pdf", "cover_pdf",
    "questions_answered_count", "submitted",
]

FAILED_HEADERS = [
    "failed_at", "site", "job_id", "title", "company", "url",
    "stage", "reason", "stack_trace", "screenshot",
]

PENDING_HEADERS = [
    "pending_at", "site", "applier", "job_id", "title", "company", "location",
    "url", "fit_score", "ats_score", "questions_answered",
    "files_attached", "review_passed", "review_issues", "form_state",
    "fill_error", "resume_pdf", "cover_pdf", "folder", "submitted",
    "pending_reason",
]


def _ensure_csv(path: Path, headers: list[str]) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)


def load_applied_ids(applied_csv: Path) -> set[str]:
    """Return the set of job_ids we've already applied to."""
    if not applied_csv.exists():
        return set()
    ids: set[str] = set()
    with applied_csv.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            jid = row.get("job_id")
            if jid:
                ids.add(jid)
    return ids


def record_applied(applied_csv: Path, row: dict) -> None:
    """Append a new application to the applied CSV."""
    _ensure_csv(applied_csv, APPLIED_HEADERS)
    out = {h: row.get(h, "") for h in APPLIED_HEADERS}
    out["applied_at"] = out.get("applied_at") or datetime.now().isoformat(timespec="seconds")
    with applied_csv.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=APPLIED_HEADERS).writerow(out)
    log.info(f"tracked applied: {out['title']} @ {out['company']}")


def record_failed(failed_csv: Path, row: dict) -> None:
    """Append a failure to the failed CSV."""
    _ensure_csv(failed_csv, FAILED_HEADERS)
    out = {h: row.get(h, "") for h in FAILED_HEADERS}
    out["failed_at"] = out.get("failed_at") or datetime.now().isoformat(timespec="seconds")
    with failed_csv.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FAILED_HEADERS).writerow(out)
    log.warning(f"tracked failed: {out.get('title','')} @ {out.get('company','')} - {out.get('reason','')}")


def record_pending(pending_csv: Path, row: dict, *, reason: str) -> None:
    """Append an application that needs user review (form filled but bot
    couldn't / wouldn't auto-submit)."""
    _ensure_csv(pending_csv, PENDING_HEADERS)
    out = {h: row.get(h, "") for h in PENDING_HEADERS}
    out["pending_at"] = out.get("pending_at") or datetime.now().isoformat(timespec="seconds")
    out["pending_reason"] = reason
    with pending_csv.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=PENDING_HEADERS).writerow(out)
    log.info(f"tracked pending: {out.get('title','')} @ {out.get('company','')} - {reason}")


def daily_count(applied_csv: Path, site: str | None = None) -> int:
    """Number of applications submitted today (optionally for a single site)."""
    if not applied_csv.exists():
        return 0
    today = datetime.now().date().isoformat()
    n = 0
    with applied_csv.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("applied_at", "").startswith(today):
                continue
            if site and row.get("site") != site:
                continue
            if str(row.get("submitted", "")).lower() in {"true", "1", "yes"}:
                n += 1
    return n
