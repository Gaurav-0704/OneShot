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
from core.filter import apply_location_filter, apply_rule_filters, score_and_filter
from core.scraper import df_to_job_dicts, scrape
from core.seen_store import SeenStore
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
        # Phase 2: how many genuinely-new jobs this run surfaced.
        self.new_count: int | None = None

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

        # Scrape — auto-widen the date window if a too-narrow one returns
        # nothing. Freshest-first: we try the user's window, then widen only
        # when it comes back empty, so a 6-hour window never silently zeroes.
        df, used_window = self._scrape_widening(prefs)
        raw = 0 if df is None or df.empty else len(df)
        if raw == 0:
            self.warn(
                "0 jobs scraped from the job boards. The usual cause on a hosted "
                "server is the boards blocking its datacenter IP — try a wider "
                "date window, more search terms, or running locally."
            )
            self.all_scored = []
            self.new_count = 0
            return []

        jobs_dicts = df_to_job_dicts(df)

        applied_ids = load_applied_ids(self.applied_csv)
        jobs_dicts = apply_rule_filters(jobs_dicts, prefs, applied_ids)
        n_rule = len(jobs_dicts)

        jobs_dicts = apply_location_filter(jobs_dicts, prefs)
        n_geo = len(jobs_dicts)

        jobs_dicts = self._drop_stale(jobs_dicts, prefs)
        n_stale = len(jobs_dicts)

        jobs_dicts = self._keep_fresh(jobs_dicts, prefs)
        n_fresh = len(jobs_dicts)

        self.all_scored = list(jobs_dicts)

        if self.score_jobs:
            def _capture_all(scored_list):
                self.all_scored = scored_list
            jobs_dicts = score_and_filter(
                jobs_dicts, self.profile.master_resume_text, prefs,
                on_all_scored=_capture_all,
            )
        n_scored = len(jobs_dicts)

        # One-line funnel so the exact drop point is always visible in the log.
        self.info(
            f"discovery funnel [{used_window}]: scraped={raw} -> rule={n_rule} -> "
            f"geo={n_geo} -> fresh={n_stale}/{n_fresh} -> kept={n_scored}"
        )
        if not jobs_dicts:
            self.warn(self._explain_zero(raw, n_rule, n_geo, n_fresh, n_scored, prefs))

        # Remember ONLY what we surface, so re-runs skip already-shown jobs
        # without zeroing out future runs.
        if prefs.get("fresh_only", True) is not False:
            self.record_surfaced(jobs_dicts)

        return [self._to_application(j) for j in jobs_dicts]

    # ── Scrape with adaptive date-window widening ─────────────────────────────

    _WINDOWS = ["6_hours", "12_hours", "24_hours", "48_hours", "3days", "week", "month"]

    def _scrape_widening(self, prefs: dict):
        """Scrape at the configured date window; if empty, widen step by step
        (freshest-first) until jobs appear or we hit 'month'. Returns
        (DataFrame|None, window_used)."""
        configured = str(prefs.get("date_posted", "week"))
        try:
            start = self._WINDOWS.index(configured)
        except ValueError:
            start = self._WINDOWS.index("week")
        # Cap at 4 attempts so an IP-blocked server doesn't make 7 slow scrapes.
        for window in self._WINDOWS[start:start + 4]:
            p = dict(prefs)
            p["date_posted"] = window
            df = scrape(p, limit=self.limit)
            if df is not None and not df.empty:
                if window != configured:
                    self.info(f"date window '{configured}' returned nothing — widened to '{window}'")
                return df, window
        return None, configured

    @staticmethod
    def _explain_zero(raw, n_rule, n_geo, n_fresh, n_scored, prefs) -> str:
        """Turn a zero result into one actionable sentence pointing at the stage
        that dropped everything."""
        if n_rule == 0:
            return f"All {raw} scraped jobs were removed by blacklist/already-applied filters — relax your blacklists."
        if n_geo == 0:
            allowed = prefs.get("allowed_countries") or []
            return (f"All {n_rule} jobs were dropped by the location filter (allowed: {allowed}). "
                    "Add countries, or set Remote Scope to 'worldwide'.")
        if n_fresh == 0:
            return "Every match was already shown in a previous run — uncheck 'Only show new jobs' to see them again."
        if n_scored == 0:
            ms = (prefs.get("fit_score") or {}).get("min_score", 6)
            return f"{n_fresh} jobs scored below your fit threshold ({ms}/10) — lower it or widen your search terms."
        return "0 jobs after filtering — widen your date window, search terms, or fit threshold."

    # ── Freshness / repost suppression (Phase 2) ──────────────────────────────

    def _seen_store(self) -> SeenStore:
        return SeenStore(self.applied_csv.parent / "seen_jobs.sqlite")

    def _keep_fresh(self, jobs: list[dict], prefs: dict) -> list[dict]:
        """Drop only jobs that were SURFACED in a prior run (or reposts of them).

        Recording happens later (record_surfaced) on just the jobs we actually
        return — NOT every scraped job — otherwise a first run would mark
        everything seen and a re-run would find 0 new. Toggle off with
        preferences.yaml `fresh_only: false`."""
        if prefs.get("fresh_only", True) is False:
            self.new_count = len(jobs)
            return jobs
        try:
            store = self._seen_store()
            new, stats = store.split_new(jobs)
            self.new_count = stats["new"]
            self.info(
                f"freshness: {stats['new']} not-yet-shown "
                f"({stats['seen']} already shown, {stats['reposts']} reposts dropped)"
            )
            # If freshness would empty the run but there WERE jobs, keep them —
            # better to re-show than to leave the user staring at zero.
            if not new and jobs:
                self.warn("all scraped jobs were already shown before — showing them "
                          "again (uncheck 'Only show new jobs' to silence this)")
                self.new_count = len(jobs)
                return jobs
            return new
        except Exception as e:
            self.warn(f"seen-store unavailable ({e}) - skipping freshness filter")
            self.new_count = len(jobs)
            return jobs

    def record_surfaced(self, candidates: list[dict]) -> None:
        """Remember the jobs we actually returned so future runs skip what the
        user already saw. Only called for surfaced candidates."""
        if not candidates:
            return
        try:
            self._seen_store().record(candidates)
        except Exception as e:
            self.warn(f"could not record surfaced jobs: {e}")

    @staticmethod
    def _drop_stale(jobs: list[dict], prefs: dict) -> list[dict]:
        """Drop postings older than the date_posted window (when a parseable
        date is present). Jobs with no date survive — we can't judge them."""
        max_days = {
            "6_hours": 1, "12_hours": 1, "24_hours": 1, "today": 1,
            "48_hours": 2, "3days": 3, "week": 7, "month": 31,
        }.get(str(prefs.get("date_posted", "week")), None)
        if not max_days:
            return jobs
        from datetime import date
        today = date.today()
        kept = []
        for j in jobs:
            dp = (j.get("date_posted") or "").strip()[:10]
            try:
                d = datetime.strptime(dp, "%Y-%m-%d").date()
            except Exception:
                kept.append(j)   # unknown date — keep
                continue
            if (today - d).days <= max_days:
                kept.append(j)
        return kept

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
