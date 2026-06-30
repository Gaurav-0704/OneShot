"""
webapp/background.py — continuous background discovery (Phase 3).

A daemon thread that periodically runs discovery (scrape + score + freshness
filter) WITHOUT tailoring, so fresh matches accumulate in last_discovered.json
on their own. No new dependencies — a plain threading loop, not APScheduler.

Controlled from the UI:
  POST /api/discovery/start  {interval_min}
  POST /api/discovery/stop
  GET  /api/discovery/status

It never runs while a full pipeline run is active (shared rate limits), and
relies on core.seen_store so each pass only adds genuinely-new postings.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# How often the loop wakes to check whether it's time for the next pass.
_TICK_SECONDS = 5


class BackgroundDiscovery:
    def __init__(self, root: Path, runner=None):
        self.root = Path(root)
        self.runner = runner            # PipelineRunner — to avoid concurrent scrapes
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.enabled = False
        self.interval_min = int(os.environ.get("DISCOVERY_INTERVAL_MIN", "60") or 60)
        self.last_run_at: Optional[str] = None
        self.last_new_count: Optional[int] = None
        self.last_error: Optional[str] = None
        self._next_due = 0.0

    # ── Control ───────────────────────────────────────────────────────────────

    def start(self, interval_min: Optional[int] = None) -> dict:
        if interval_min:
            self.interval_min = max(5, int(interval_min))
        if self._thread and self._thread.is_alive():
            self.enabled = True
            self._next_due = time.time()  # run soon
            return self.status()
        self._stop.clear()
        self.enabled = True
        self._next_due = time.time()      # first pass immediately
        self._thread = threading.Thread(target=self._loop, daemon=True, name="bg-discovery")
        self._thread.start()
        log.info(f"background discovery started (every {self.interval_min} min)")
        return self.status()

    def stop(self) -> dict:
        self.enabled = False
        self._stop.set()
        log.info("background discovery stopped")
        return self.status()

    def status(self) -> dict:
        running = bool(self._thread and self._thread.is_alive())
        next_in = max(0, int(self._next_due - time.time())) if (self.enabled and running) else None
        return {
            "enabled": self.enabled,
            "running": running,
            "interval_min": self.interval_min,
            "last_run_at": self.last_run_at,
            "last_new_count": self.last_new_count,
            "next_run_in_s": next_in,
            "last_error": self.last_error,
        }

    # ── Loop ──────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.enabled and time.time() >= self._next_due:
                pipeline_busy = bool(self.runner and self.runner.is_running())
                if not pipeline_busy:
                    try:
                        self._run_pass()
                    except Exception as e:
                        self.last_error = str(e)
                        log.warning(f"background discovery pass failed: {e}")
                # Schedule next pass regardless (busy passes just wait a cycle).
                self._next_due = time.time() + self.interval_min * 60
            self._stop.wait(_TICK_SECONDS)

    def _run_pass(self) -> None:
        from agents.profile import ProfileAgent
        from agents.discovery import DiscoveryAgent

        os.environ.setdefault("NONINTERACTIVE", "1")
        profile = ProfileAgent(self.root / "config").build()
        applied_csv = self.root / "outputs" / "applied_jobs.csv"
        snap = self.root / "outputs" / "last_discovered.json"

        agent = DiscoveryAgent(
            profile, applied_csv=applied_csv, score_jobs=True,
            cache_path=snap, use_cache=False,
        )
        agent.discover()
        new_rows = self._build_rows(agent, profile)
        merged = self._merge_snapshot(snap, new_rows)
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(json.dumps(merged, indent=2, default=str), encoding="utf-8")

        self.last_run_at = datetime.now().isoformat(timespec="seconds")
        self.last_new_count = agent.new_count
        self.last_error = None
        log.info(f"background discovery: {agent.new_count} new; snapshot now {len(merged)} jobs")

    # ── Snapshot helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_rows(agent, profile) -> list[dict]:
        min_score = int((profile.raw_preferences.get("fit_score") or {}).get("min_score", 6))
        rows = []
        for j in agent.all_scored or []:
            fs = j.get("fit_score")
            pct = int(fs * 10) if isinstance(fs, (int, float)) and fs is not None else None
            rows.append({
                "site": j.get("site", ""), "job_id": j.get("job_id", ""),
                "title": j.get("title", ""), "company": j.get("company", ""),
                "location": j.get("location", ""), "url": j.get("url", ""),
                "is_remote": bool(j.get("is_remote", False)),
                "fit_score": fs, "match_pct": pct,
                "fit_reason": j.get("fit_reason", ""),
                "above_threshold": isinstance(fs, (int, float)) and fs >= min_score,
                "min_score": min_score,
                "date_posted": j.get("date_posted", ""),
                "min_salary": j.get("min_salary"), "max_salary": j.get("max_salary"),
                "salary_meets_minimum": j.get("salary_meets_minimum"),
                "description_chars": len(j.get("description", "") or ""),
                "discovered_at": datetime.now().isoformat(timespec="seconds"),
            })
        return rows

    @staticmethod
    def _merge_snapshot(snap: Path, new_rows: list[dict], cap: int = 500) -> list[dict]:
        """Prepend new rows to the existing snapshot, dedup by job_id, cap size."""
        existing: list[dict] = []
        if snap.exists():
            try:
                existing = json.loads(snap.read_text(encoding="utf-8")) or []
            except Exception:
                existing = []
        seen = set()
        merged: list[dict] = []
        for row in new_rows + existing:
            jid = row.get("job_id") or row.get("url") or ""
            if jid in seen:
                continue
            seen.add(jid)
            merged.append(row)
        return merged[:cap]
