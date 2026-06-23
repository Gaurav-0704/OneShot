"""
agents/orchestrator.py - Orchestrator

Browser-free "tailored-application factory + handoff" pipeline.

Per-job flow:
  [1] ProfileAgent.build()          — assemble UserProfile from YAML + resume
  [2] DiscoveryAgent.discover()     — scrape & score jobs
  [3] for each candidate (sorted freshest-first):
        TailorAgent.tailor(app)     — research company + write resume & cover
        HumanizerAgent.run(app)     — clean AI filler, smoke-test copy
        PackagerAgent.package(app)  — write ready-to-apply record to pending CSV
  [4] LearnerAgent.learn()          — ATS gap analysis, Q&A memory promotion

TAILOR_TOP_N env var (default 0 = all):
  If set to N > 0, only tailor the top N candidates (freshest + highest fit).
  Remaining jobs appear in last_discovered.json (Discovered tab) but get no
  resume/cover. Useful to cap LLM spend per run.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from agents.discovery import DiscoveryAgent
from agents.humanizer import HumanizerAgent
from agents.learner import LearnerAgent
from agents.packager import PackagerAgent
from agents.profile import ProfileAgent
from agents.tailor import TailorAgent
from core.tracker import daily_count
from models import JobApplication, UserProfile

log = logging.getLogger("orchestrator")

_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(s: str, max_len: int = 40) -> str:
    s = _slug_re.sub("-", (s or "").lower()).strip("-")
    return s[:max_len] or "x"


class Orchestrator:
    def __init__(
        self,
        root: Path,
        *,
        master_resume: Optional[Path] = None,
        dry_run: bool = True,          # kept for API/runner compat; no browser to control
        pause: bool = True,            # kept for API/runner compat
        run_limit: Optional[int] = None,
        score_jobs: bool = True,
        do_research: bool = True,
        run_ats_check: bool = True,
        require_min_ats: int = 0,      # kept for API compat; enforced by PackagerAgent hook
        headless: bool = False,        # kept for API compat; no browser launched
        should_stop=None,
        use_cache: bool = False,
        on_event=None,
    ):
        self.root          = root
        self.master_resume = master_resume
        self.dry_run       = dry_run
        self.pause         = pause
        self.run_limit     = run_limit
        self.score_jobs    = score_jobs
        self.do_research   = do_research
        self.run_ats_check = run_ats_check
        self.require_min_ats = require_min_ats
        self.headless      = headless
        self.should_stop   = should_stop or (lambda: False)
        self.use_cache     = use_cache
        self.on_event      = on_event or (lambda **kw: None)

        self.applied_csv = root / "outputs" / "applied_jobs.csv"
        self.failed_csv  = root / "outputs" / "failed_jobs.csv"
        self.pending_csv = root / "outputs" / "pending_review.csv"

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self) -> int:
        log.info("=" * 64)
        log.info(f" Pipeline start  -  tailoring factory mode")
        log.info("=" * 64)

        # ── Archive previous run before writing any new pending rows ──────────
        from agents.history import HistoryAgent
        history = HistoryAgent(self.root)
        archive_result = history.archive_current()
        if archive_result.get("ok"):
            log.info(
                f"Archived previous run → "
                f"{archive_result['date']}/task_{archive_result['task_number']} "
                f"({archive_result['count']} rows)"
            )
        self._history = history
        self._history_task: dict = archive_result  # carry task coords for finish_run

        profile = self._run_profile()
        if profile is None:
            return 1

        candidates = self._run_discovery(profile)
        if not candidates:
            log.info("no candidates after discovery - done")
            return 0

        # Sort: freshest jobs first, then by fit score descending.
        # Empty date_posted goes to the end (treat as oldest).
        candidates.sort(
            key=lambda a: (a.date_posted or "0", a.fit_score or 0),
            reverse=True,
        )

        # Dedup by (title, company) — same job sometimes appears with two
        # location strings (e.g. "Remote" and "Worldwide") and both survive
        # the scraper's location-aware dedup. Keep the first (highest-scored).
        seen_jobs: set[tuple[str, str]] = set()
        unique: list[JobApplication] = []
        for a in candidates:
            key = (a.title.lower().strip(), a.company.lower().strip())
            if key not in seen_jobs:
                seen_jobs.add(key)
                unique.append(a)
            else:
                log.info(f"dedup: dropping duplicate '{a.title} @ {a.company}' ({a.location})")
        if len(unique) < len(candidates):
            log.info(f"dedup removed {len(candidates) - len(unique)} duplicate(s); {len(unique)} remain")
        candidates = unique

        tailor_top_n = int(os.environ.get("TAILOR_TOP_N", "0") or 0)
        if tailor_top_n > 0:
            log.info(
                f"TAILOR_TOP_N={tailor_top_n}: tailoring top {tailor_top_n} of "
                f"{len(candidates)} candidates"
            )

        tailor_agent   = TailorAgent(
            profile,
            do_research=self.do_research,
            run_ats_check=self.run_ats_check,
        )
        humanizer      = HumanizerAgent(profile, root=self.root)
        packager       = PackagerAgent(profile, pending_csv=self.pending_csv, root=self.root)

        # run_limit now means "stop once this many are successfully SAVED to
        # pending_review" — failures must NOT consume the quota. We still bound
        # the number of candidates we ATTEMPT with a safety cap so a run can't
        # loop forever when most jobs fail.
        target = self.run_limit if (self.run_limit and self.run_limit > 0) else None
        if target:
            attempt_cap = min(len(candidates), max(target * 3, target + 10))
        else:
            attempt_cap = len(candidates)
        if tailor_top_n > 0:                      # explicit cost cap on attempts
            attempt_cap = min(attempt_cap, tailor_top_n)
        process_list = candidates[:attempt_cap]
        n_skipped = len(candidates) - len(process_list)

        import os
        import threading
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        workers = max(1, int(os.environ.get("TAILOR_WORKERS", "4") or 4))
        csv_lock = threading.Lock()
        counters = {"packaged": 0, "failed": 0, "humanized": 0}
        counters_lock = threading.Lock()
        reached = threading.Event()               # set once `target` are packaged
        total = len(process_list)
        t0 = _time.time()

        def _process(idx: int, app: JobApplication) -> None:
            # Skip cheaply once we've saved enough or a stop was requested.
            if self.should_stop() or reached.is_set():
                return
            log.info(f"[{idx}/{total}] {app.title} @ {app.company}"
                     + (f"  (fit {app.fit_score}/10)" if app.fit_score else ""))
            app.folder = self._make_folder(app)

            try:
                tailor_agent.tailor(app)
            except Exception as e:
                log.error(f"   tailor crashed: {e}")
                with counters_lock: counters["failed"] += 1
                self.on_event(type="job", stage="failed", title=app.title, company=app.company)
                return

            if app.tailored_resume_pdf is None:
                log.warning(f"   no resume produced - skipping {app.title}")
                with counters_lock: counters["failed"] += 1
                self.on_event(type="job", stage="failed", title=app.title, company=app.company)
                return

            try:
                humanizer.run(app, profile)
                with counters_lock: counters["humanized"] += 1
            except Exception as e:
                log.warning(f"   humanizer failed (non-fatal): {e}")

            try:
                with csv_lock:                       # serialize pending_review.csv writes
                    packager.package(app)
                with counters_lock:
                    counters["packaged"] += 1
                    if target and counters["packaged"] >= target:
                        reached.set()                # enough saved — short-circuit the rest
                self.on_event(type="job", stage="packaged", title=app.title,
                              company=app.company, ats_score=app.ats_score,
                              fit_score=app.fit_score)
            except Exception as e:
                log.error(f"   packager crashed: {e}")
                with counters_lock: counters["failed"] += 1

        log.info(f"tailoring {total} job(s) with {workers} parallel worker(s)")
        if workers == 1:
            for i, app in enumerate(process_list, 1):
                if self.should_stop():
                    log.info("stop requested - exiting per-job loop")
                    break
                if reached.is_set():
                    log.info(f"reached {target} saved application(s) - stopping")
                    break
                _process(i, app)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_process, i, app): i
                           for i, app in enumerate(process_list, 1)}
                for fut in as_completed(futures):
                    fut.result()   # surface unexpected exceptions

        elapsed = _time.time() - t0
        per_job = elapsed / max(1, counters["packaged"] + counters["failed"])
        log.info(f"per-job loop finished in {elapsed:.1f}s "
                 f"({per_job:.1f}s/job avg, {workers} workers)")
        self._final_report(counters["packaged"], counters["failed"],
                           n_skipped, counters["humanized"])

        # ── LearnerAgent — runs after every pipeline pass ──────────────────────
        try:
            LearnerAgent(self.root).learn()
        except Exception as e:
            log.warning(f"LearnerAgent failed (non-fatal): {e}")

        # ── Stamp ended_at on the archived task (best-effort) ─────────────────
        task = getattr(self, "_history_task", {})
        if task.get("ok") and hasattr(self, "_history"):
            try:
                search_terms = list(
                    (profile.raw_preferences.get("search", {}) or {}).get("job_titles", [])
                    or []
                )
                self._history.finish_run(
                    task["date"], task["task_number"], search_terms=search_terms
                )
            except Exception as e:
                log.debug(f"finish_run non-fatal: {e}")

        return 0

    # ── ProfileAgent ──────────────────────────────────────────────────────────

    def _run_profile(self) -> Optional[UserProfile]:
        log.info("[1/3] ProfileAgent ...")
        agent = ProfileAgent(self.root / "config", master_resume=self.master_resume)
        try:
            profile = agent.build()
        except FileNotFoundError as e:
            log.error(str(e))
            return None
        if not profile.master_resume_text:
            log.error("master resume is empty after parsing - check the PDF")
            return None
        log.info(
            f"   profile: {profile.full_name}  |  "
            f"{len(profile.github_repos)} GH repos  |  "
            f"{len(profile.master_resume_text)} resume chars"
        )
        return profile

    # ── DiscoveryAgent ────────────────────────────────────────────────────────

    def _run_discovery(self, profile: UserProfile) -> list[JobApplication]:
        log.info("[2/3] DiscoveryAgent ...")
        cache_path = self.root / "outputs" / "last_discovered.json"
        agent = DiscoveryAgent(
            profile,
            applied_csv=self.applied_csv,
            score_jobs=self.score_jobs,
            cache_path=cache_path,
            use_cache=self.use_cache,
            limit=self.run_limit,
        )
        candidates = agent.discover()

        if agent.new_count is not None:
            log.info(f"{agent.new_count} new job(s) since last run")

        # Persist a full snapshot (above + below threshold) for the Discovered tab.
        try:
            import json
            snap = self.root / "outputs" / "last_discovered.json"
            snap.parent.mkdir(parents=True, exist_ok=True)
            min_score = int(
                (profile.raw_preferences.get("fit_score") or {}).get("min_score", 6)
            )
            full_list = agent.all_scored or []
            kept_ids  = {c.job_id for c in candidates}
            relaxed_to = None
            if kept_ids:
                kept_scores = [
                    j.get("fit_score") for j in full_list
                    if j.get("job_id") in kept_ids
                    and isinstance(j.get("fit_score"), (int, float))
                ]
                if kept_scores and min(kept_scores) < min_score:
                    relaxed_to = int(min(kept_scores))
            data = []
            for j in full_list:
                fs  = j.get("fit_score")
                pct = int(fs * 10) if isinstance(fs, (int, float)) and fs is not None else None
                data.append({
                    "site":          j.get("site", ""),
                    "job_id":        j.get("job_id", ""),
                    "title":         j.get("title", ""),
                    "company":       j.get("company", ""),
                    "location":      j.get("location", ""),
                    "url":           j.get("url", ""),
                    "is_remote":     bool(j.get("is_remote", False)),
                    "fit_score":     fs,
                    "match_pct":     pct,
                    "fit_reason":    j.get("fit_reason", ""),
                    "above_threshold": j.get("job_id") in kept_ids,
                    "min_score":     min_score,
                    "fit_threshold_relaxed_to": relaxed_to,
                    "date_posted":   j.get("date_posted", ""),
                    "min_salary":    j.get("min_salary"),
                    "max_salary":    j.get("max_salary"),
                    "salary_meets_minimum": j.get("salary_meets_minimum"),
                    "description_chars": len(j.get("description", "") or ""),
                })
            snap.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            log.info(
                f"discovered snapshot: {len(data)} jobs total, "
                f"{len(kept_ids)} above min_score={min_score}"
            )
        except Exception as e:
            log.debug(f"snapshot write failed: {e}")

        return candidates

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_folder(self, app: JobApplication) -> Path:
        folder = (
            f"{slugify(app.company)}_"
            f"{slugify(app.title)}_"
            f"{slugify((app.job_id or '')[:12])}"
        )
        p = self.root / "outputs" / "tailored" / folder
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _final_report(
        self, n_packaged: int, n_failed: int, n_skipped: int = 0, n_humanized: int = 0
    ) -> None:
        log.info("=" * 64)
        log.info(
            f" Run complete: {n_packaged} ready to apply, "
            f"{n_humanized} humanized & checked, "
            f"{n_failed} failed, "
            f"{n_skipped} skipped (TAILOR_TOP_N cap)"
        )
        log.info(f" Ready-to-apply: {self.pending_csv}")
        log.info(f" Documents:      {self.root / 'outputs' / 'tailored'}")
        log.info("=" * 64)
