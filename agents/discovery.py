"""
agents/discovery.py - DiscoveryAgent

Job: find candidate jobs across all configured platforms, dedupe against the
already-applied CSV, apply rule-based filters, optionally score with Claude.

Tools used:
  - core.scraper.scrape  (JobSpy wrapper)
  - core.filter.apply_rule_filters / score_and_filter
  - core.tracker.load_applied_ids

Input:  preferences (dict from preferences.yaml), user resume text
Output: list[JobApplication], partially populated (stage 1 fields)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from agents.base import Agent
from core.filter import apply_rule_filters, score_and_filter
from core.scraper import df_to_job_dicts, scrape
from core.tracker import load_applied_ids
from models import JobApplication, UserProfile

# How old a cached discovery snapshot can be before we re-scrape.
CACHE_MAX_AGE_HOURS = 4


class DiscoveryAgent(Agent):
    name = "discovery"
    role = (
        "Pulls jobs from LinkedIn / Indeed / Glassdoor / ZipRecruiter / Google. "
        "Drops already-applied or blacklisted jobs and ranks the rest."
    )

    def __init__(
        self,
        profile: UserProfile,
        *,
        applied_csv: Path,
        score_jobs: bool = True,
        cache_path: Path | None = None,
        use_cache: bool = False,
        limit: int | None = None,
    ):
        super().__init__(profile=profile, dry_run=True)
        self.applied_csv = applied_csv
        self.score_jobs = score_jobs
        self.cache_path = cache_path
        self.use_cache = use_cache
        self.limit = limit
        # Side-effect: full scored list (above + below threshold) so the
        # orchestrator can persist a snapshot for the Discovered tab.
        self.all_scored: list[dict] = []

    # ── Public API ───────────────────────────────────────────────────────────

    def discover(self) -> list[JobApplication]:
        """Run the full discovery flow -> list of JobApplication (above-threshold).
        If use_cache=True and cache_path exists and is fresh, skip scraping+scoring
        entirely and rebuild the candidate list from the cached snapshot."""

        # ── Cache resume path ─────────────────────────────────────────────
        if self.use_cache and self.cache_path and self.cache_path.exists():
            try:
                cached = self._load_cache()
                if cached is not None:
                    self.info(f"resuming from cached snapshot: {len(cached)} scored jobs")
                    self.all_scored = cached
                    applied_ids = load_applied_ids(self.applied_csv)
                    prefs = self.profile.raw_preferences
                    min_score = int((prefs.get("fit_score") or {}).get("min_score", 6))
                    # Rebuild candidate list: above-threshold, not already applied
                    candidates = [
                        j for j in cached
                        if j.get("job_id") not in applied_ids
                        and (j.get("fit_score") or 0) >= min_score
                    ]
                    self.info(f"after cache filter: {len(candidates)} candidates")
                    return [self._to_application(j) for j in candidates]
            except Exception as e:
                self.warn(f"cache load failed ({e}) - falling back to fresh scrape")

        prefs = self.profile.raw_preferences

        terms = [t.strip() for t in (prefs.get("search_terms") or []) if t and t.strip()]
        if len(terms) >= 2:
            tokens = [set(t.lower().split()) for t in terms]
            common = set.intersection(*tokens) if tokens else set()
            if common and len(terms) <= 3:
                self.warn(
                    f"all {len(terms)} search terms share the word(s) {sorted(common)} - "
                    "consider adding broader fall-back queries for better coverage"
                )

        self.info(f"sites={prefs.get('sites')} terms={prefs.get('search_terms')}")
        df = scrape(prefs, limit=self.limit)
        if df.empty:
            self.warn("no jobs scraped")
            self.all_scored = []
            return []
        jobs_dicts = df_to_job_dicts(df)
        self.info(f"raw jobs scraped: {len(jobs_dicts)}")

        applied_ids = load_applied_ids(self.applied_csv)
        jobs_dicts = apply_rule_filters(jobs_dicts, prefs, applied_ids)
        self.info(f"after rule filter: {len(jobs_dicts)}")

        self.all_scored = list(jobs_dicts)

        if self.score_jobs:
            def _capture_all(scored_list):
                self.all_scored = scored_list
            jobs_dicts = score_and_filter(
                jobs_dicts, self.profile.master_resume_text, prefs,
                on_all_scored=_capture_all,
            )
            self.info(f"after fit scoring: {len(jobs_dicts)}")

        return [self._to_application(j) for j in jobs_dicts]

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _load_cache(self) -> list[dict] | None:
        """Load last_discovered.json if it exists and is fresh enough."""
        p = self.cache_path
        if not p or not p.exists():
            return None
        age = datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)
        if age > timedelta(hours=CACHE_MAX_AGE_HOURS):
            self.info(f"cache is {int(age.total_seconds() / 3600)}h old - will re-scrape")
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if not data:
            return None
        return data

    # ── Internals ────────────────────────────────────────────────────────────

    @staticmethod
    def _to_application(j: dict) -> JobApplication:
        return JobApplication(
            site=j.get("site", "") or "",
            job_id=j.get("job_id", "") or "",
            title=j.get("title", "") or "",
            company=j.get("company", "") or "",
            location=j.get("location", "") or "",
            url=j.get("url", "") or "",
            raw_description=j.get("description", "") or "",
            is_remote=bool(j.get("is_remote", False)),
            job_type=j.get("job_type"),
            min_salary=j.get("min_salary"),
            max_salary=j.get("max_salary"),
            date_posted=j.get("date_posted", "") or "",
            fit_score=j.get("fit_score"),
            fit_reason=j.get("fit_reason", "") or "",
        )
