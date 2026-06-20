"""
core/scraper.py - thin wrapper around JobSpy that takes our YAML preferences
and returns a normalized list of job dicts the pipeline can use.

For small runs (limit <= 20) I search LinkedIn only with the top 3 terms so
discovery finishes in ~30 seconds instead of 4+ minutes. For medium runs I
add Indeed. Only full runs (no limit or limit > 60) hit all configured sites.
All (term, location) pairs are scraped in parallel regardless of tier.
"""
from __future__ import annotations

import logging
import sys as _sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path as _Path
from typing import Any, Optional

import pandas as pd

# JobSpy is vendored under core/_vendor/jobspy/. Its source uses absolute
# imports like "from jobspy.bayt import BaytScraper", so we put _vendor/ on
# sys.path before importing - that way those imports resolve normally.
_VENDOR = _Path(__file__).resolve().parent / "_vendor"
if str(_VENDOR) not in _sys.path:
    _sys.path.insert(0, str(_VENDOR))

from jobspy import scrape_jobs  # noqa: E402  (after sys.path patch)

log = logging.getLogger(__name__)

# Parallel workers for (term × location) scrape calls.
_SCRAPE_WORKERS = 6

# Sites tried in order of density / speed when I need to truncate the list.
_SITE_ORDER = ["linkedin", "indeed", "glassdoor", "zip_recruiter", "google"]

# Speed tiers: (limit_threshold, max_sites, max_terms, max_results_per_site)
# The first tier whose threshold the limit falls under is used.
_TIERS = [
    (20,  1, 3, 15),   # quick test  — LinkedIn only, 3 terms, 15 results/site
    (60,  2, 5, 20),   # medium run  — LinkedIn + Indeed, 5 terms, 20 results/site
]
# No limit or limit > 60: all configured sites, all terms, full results_per_site.


def _scale_for_limit(
    prefs: dict[str, Any],
    limit: Optional[int],
) -> tuple[list[str], list[str], int]:
    """Return (sites, terms, results_per_site) scaled to the requested limit.

    I keep the site list in the user's configured order and just truncate it,
    so their preferred sources stay prioritised within each tier.
    """
    configured_sites   = prefs.get("sites", ["linkedin", "indeed"])
    configured_terms   = prefs.get("search_terms", [])
    configured_results = int(prefs.get("results_per_site", 25))

    if not limit or limit > 60:
        return configured_sites, configured_terms, configured_results

    for threshold, max_sites, max_terms, max_results in _TIERS:
        if limit <= threshold:
            ordered = [s for s in _SITE_ORDER if s in configured_sites]
            sites   = (ordered or configured_sites)[:max_sites]
            terms   = configured_terms[:max_terms]
            results = min(configured_results, max_results)
            log.info(
                f"fast-mode (limit={limit}): "
                f"sites={sites}, {len(terms)} terms, {results} results/site"
            )
            return sites, terms, results

    return configured_sites, configured_terms, configured_results


def _scrape_one(
    *,
    sites: list[str],
    term: str,
    loc: str,
    distance: int,
    is_remote: bool,
    job_type,
    results_wanted: int,
    hours_old,
    country: str,
) -> pd.DataFrame | None:
    """One (term, location) scrape call — runs inside a thread."""
    log.info(f"scraping site={sites} term={term!r} location={loc!r}")
    try:
        df = scrape_jobs(
            site_name=sites,
            search_term=term,
            location=loc,
            distance=distance,
            is_remote=is_remote,
            job_type=job_type,
            results_wanted=results_wanted,
            hours_old=hours_old,
            country_indeed=country,
            description_format="markdown",
            linkedin_fetch_description=True,
            verbose=0,
        )
        if df is not None and not df.empty:
            df["search_term"] = term
            df["search_location"] = loc
            log.info(f"  -> {len(df)} jobs")
            return df
        return None
    except Exception as e:
        log.warning(f"scrape failed for {term} / {loc}: {e}")
        return None


def scrape(prefs: dict[str, Any], limit: Optional[int] = None) -> pd.DataFrame:
    """Run JobSpy across sites and search terms in parallel.

    When limit is set I automatically scale down sites, terms, and results
    so small runs finish in ~30 seconds rather than several minutes.
    Returns a deduplicated DataFrame of all results.
    """
    if not prefs.get("search_terms"):
        raise ValueError("preferences.yaml must define at least one search_term")

    sites, terms, results_wanted = _scale_for_limit(prefs, limit)

    locations   = prefs.get("locations", [""])
    is_remote   = bool(prefs.get("remote", False))
    job_type    = (prefs.get("job_types") or [None])[0]
    distance    = int(prefs.get("distance_miles", 50))
    country     = prefs.get("country_indeed", "usa")
    date_filter = prefs.get("date_posted", "week")
    hours_old   = {
        "6_hours":  6,
        "12_hours": 12,
        "24_hours": 24,
        "48_hours": 48,
        "today":    24,   # legacy value from old UI
        "3days":    72,   # legacy value from old UI
        "week":    168,
        "month":   720,
        "all_time": None,
    }.get(date_filter, 168)

    work = [(term, loc) for term in terms for loc in locations]
    frames: list[pd.DataFrame] = []

    with ThreadPoolExecutor(max_workers=_SCRAPE_WORKERS) as pool:
        futures = {
            pool.submit(
                _scrape_one,
                sites=sites,
                term=term,
                loc=loc,
                distance=distance,
                is_remote=is_remote,
                job_type=job_type,
                results_wanted=results_wanted,
                hours_old=hours_old,
                country=country,
            ): (term, loc)
            for term, loc in work
        }
        for future in as_completed(futures):
            df = future.result()
            if df is not None:
                frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["title", "company", "location"], keep="first")
    combined = combined.reset_index(drop=True)
    log.info(f"total unique jobs: {len(combined)}")
    return combined


def _safe_str(v) -> str:
    """Coerce any value (None, NaN, float, etc.) to a clean string.
    Prevents '.lower() on float' crashes downstream when JobSpy returns NaN
    for missing description/title/company/etc fields."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    # pandas sometimes stringifies NaN as the literal "nan"
    if s.lower() == "nan":
        return ""
    return s


def _safe_num(v):
    """Return v if it's a real number, else None. Strips NaN."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def df_to_job_dicts(df: pd.DataFrame) -> list[dict]:
    """Convert the JobSpy DataFrame to a clean list of dicts our pipeline uses.
    Every string field goes through _safe_str so NaN floats from JobSpy can't
    reach .lower() downstream and crash filter.py."""
    if df.empty:
        return []
    rows = df.to_dict(orient="records")
    cleaned: list[dict] = []
    for r in rows:
        cleaned.append({
            "site":         _safe_str(r.get("site")),
            "job_id":       _safe_str(r.get("id")) or _safe_str(r.get("job_url")),
            "title":        _safe_str(r.get("title")),
            "company":      _safe_str(r.get("company")),
            "location":     _safe_str(r.get("location")),
            "url":          _safe_str(r.get("job_url")),
            "description":  _safe_str(r.get("description")),
            "is_remote":    bool(r.get("is_remote") or False),
            "job_type":     _safe_str(r.get("job_type")) or None,
            "min_salary":   _safe_num(r.get("min_amount")),
            "max_salary":   _safe_num(r.get("max_amount")),
            "date_posted":  _safe_str(r.get("date_posted")),
            "search_term":  _safe_str(r.get("search_term")),
        })
    return cleaned
