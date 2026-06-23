"""
core/email_tracker.py - inbox-watching for application replies (STUB).

After a job is submitted, the recruiter usually emails back with one of:
  - "Thanks, we received your application" (auto-ack, no signal)
  - "We'd like to set up a call" (interview invite)
  - "We've decided to move forward with other candidates" (rejection)
  - Or a follow-up question / assessment link

Wiring this gives OneShot a real funnel view: applied -> auto-ack ->
interview / rejection / ghosted, and feeds that signal back into fit
scoring (companies that ghost get downweighted next time).

This file is a scaffold. Real implementation needs:
  1. OAuth to Gmail / Outlook (or local IMAP creds in .env)
  2. A polling loop OR webhook subscription
  3. An LLM classifier on the message body  -> {ack, interview, reject, other}
  4. A writer that updates outputs/applied_jobs.csv with the new status

The simplest first cut: a CLI command `oneshot inbox-poll` that the user
runs manually (or via cron) which reads the last 7 days of mail, matches
each message to an applied row (by company name + email domain), classifies
it, and appends to outputs/email_replies.csv.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ReplyKind = Literal["ack", "interview", "reject", "assessment", "other"]


@dataclass
class EmailReply:
    received_at: str             # ISO timestamp
    from_email: str
    company_guess: str           # best-effort match against applied jobs
    job_id: str | None
    kind: ReplyKind
    subject: str
    snippet: str                 # first 200 chars of body


# ── Public stub API ─────────────────────────────────────────────────────────

def is_configured() -> bool:
    """Check if email creds are present. Used by the orchestrator to decide
    whether to even attempt a poll cycle."""
    import os
    return bool(
        os.environ.get("GMAIL_OAUTH_TOKEN")
        or (os.environ.get("IMAP_HOST") and os.environ.get("IMAP_USER"))
    )


def poll_inbox(applied_csv: Path, since_days: int = 7) -> list[EmailReply]:
    """Read recent inbox messages and try to match each to an applied job.
    Returns a list of classified replies. STUB: returns empty list today.

    TODO when wired:
      - Connect via Gmail API (OAuth) or IMAP
      - Pull messages from last `since_days`
      - For each, match against applied_jobs.csv by sender domain or company
      - Classify with one cheap LLM call (batch)
      - Append to outputs/email_replies.csv
    """
    if not is_configured():
        return []
    # Real implementation deferred. Returning empty so callers no-op safely.
    return []


def write_replies(replies: list[EmailReply], out_csv: Path) -> int:
    """Append classified replies to outputs/email_replies.csv. Idempotent
    on (received_at, from_email, subject)."""
    if not replies:
        return 0
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    existing_keys: set[tuple] = set()
    if out_csv.exists():
        with out_csv.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing_keys.add((row.get("received_at"), row.get("from_email"), row.get("subject")))
    fields = ["received_at", "from_email", "company_guess", "job_id",
              "kind", "subject", "snippet"]
    write_header = not out_csv.exists()
    new_rows = 0
    with out_csv.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            w.writeheader()
        for r in replies:
            key = (r.received_at, r.from_email, r.subject)
            if key in existing_keys:
                continue
            w.writerow({
                "received_at": r.received_at,
                "from_email": r.from_email,
                "company_guess": r.company_guess,
                "job_id": r.job_id or "",
                "kind": r.kind,
                "subject": r.subject,
                "snippet": r.snippet,
            })
            new_rows += 1
    return new_rows


def summary_counts(replies_csv: Path) -> dict[str, int]:
    """Quick aggregate for the Dashboard - {ack: N, interview: N, ...}.
    Returns empty dict if the CSV doesn't exist yet."""
    if not replies_csv.exists():
        return {}
    counts: dict[str, int] = {}
    with replies_csv.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            k = row.get("kind") or "other"
            counts[k] = counts.get(k, 0) + 1
    return counts


# ── Module marker so orchestrator can detect "scaffold present, not wired" ──
__implementation_status__ = "stub"
__last_updated__ = "2026-05-03"
