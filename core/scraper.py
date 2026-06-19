"""
core/scraper.py - thin wrapper around JobSpy that takes our YAML preferences
and returns a normalized list of job dicts the pipeline can use.
"""
from __future__ import annotations

import logging
import sys as _sys
from pathlib import Path as _Path
from typing import Any

import pandas as pd

# JobSpy is vendored under core/_vendor/jobspy/. Its source uses absolute
# imports like "from jobspy.bayt import BaytScraper", so we put _vendor/ on
# sys.path before importing - that way those imports resolve normally.
_VENDOR = _Path(__file__).resolve().parent / "_vendor"
if str(_VENDOR) not in _sys.path:
    _sys.path.insert(0, str(_VENDOR))

from jobspy import scrape_jobs  # noqa: E402  (after sys.path patch)

log = logging.getLogger(__name__)


def scrape(prefs: dict[str, Any]) -> pd.DataFrame:
    """
    Run JobSpy across all sites and search terms in the preferences file.
    Returns a single DataFrame, deduplicated by (title, company, location).
    """
    sites = prefs.get("sites", ["linkedin", "indeed"])
    locations = prefs.get("locations", [""])
    search_terms = prefs.get("search_terms", [])
    if not search_terms:
        raise ValueError("preferences.yaml must define at least one search_term")

    is_remote = bool(prefs.get("remote", False))
    job_type = (prefs.get("job_types") or [None])[0]
    distance = int(prefs.get("distance_miles", 50))
    results_wanted = int(prefs.get("results_per_site", 25))
    country = prefs.get("country_indeed", "usa")
    date_filter = prefs.get("date_posted", "week")
    hours_old = {"24_hours": 24, "week": 168, "month": 720, "all_time": None}.get(date_filter, 168)

    frames: list[pd.DataFrame] = []
    for term in search_terms:
        for loc in locations:
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
                    frames.append(df)
                    log.info(f"  -> {len(df)} jobs")
            except Exception as e:
                log.warning(f"scrape failed for {term} / {loc}: {e}")

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
