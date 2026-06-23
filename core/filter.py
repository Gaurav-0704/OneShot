"""
core/filter.py - cheap rule filter + LLM fit-scoring with batching and retry.

Two-stage filter:
  apply_rule_filters() - dedupe + blacklist (no LLM calls)
  score_and_filter()   - score remaining jobs in BATCHES of 10 to stay under
                         Gemini free-tier rate limit (5 RPM).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from llm.client import complete_cheap
from llm.prompts import (
    BATCH_FIT_SYSTEM, FIT_SCORE_SYSTEM,
    batch_fit_score_user_prompt, fit_score_user_prompt,
)

log = logging.getLogger(__name__)


# ── Positive location filter (Phase 1: geo-bounding) ──────────────────────────
#
# JobSpy's is_remote=True is NOT geo-bounded, so boards return worldwide remote
# jobs. apply_rule_filters only does negative blacklisting. This adds a POSITIVE
# "must be in an allowed country/region" check so a US search never leaks
# China / Hong Kong / other foreign listings.

# Canonical country -> set of aliases/codes that may appear in a location string.
_COUNTRY_ALIASES: dict[str, set[str]] = {
    "united states": {"united states", "usa", "u.s.a", "u.s.", " us", "us ",
                       "united states of america", "america"},
    "united kingdom": {"united kingdom", "uk", "u.k.", "england", "scotland",
                       "wales", "northern ireland", "britain", "great britain"},
    "canada": {"canada"},
    "india": {"india"},
    "china": {"china", "prc", "mainland china"},
    "hong kong": {"hong kong", "hongkong"},
    "singapore": {"singapore"},
    "philippines": {"philippines"},
    "germany": {"germany", "deutschland"},
    "france": {"france"},
    "ireland": {"ireland"},
    "australia": {"australia"},
    "netherlands": {"netherlands", "holland"},
    "spain": {"spain"},
    "poland": {"poland"},
    "japan": {"japan"},
    "south korea": {"south korea", "korea"},
    "brazil": {"brazil", "brasil"},
    "mexico": {"mexico"},
    "pakistan": {"pakistan"},
    "bangladesh": {"bangladesh"},
    "indonesia": {"indonesia"},
    "vietnam": {"vietnam", "viet nam"},
    "malaysia": {"malaysia"},
    "uae": {"united arab emirates", "uae", "dubai", "abu dhabi"},
    "nigeria": {"nigeria"},
    "south africa": {"south africa"},
    "argentina": {"argentina"},
    "colombia": {"colombia"},
    "portugal": {"portugal"},
    "italy": {"italy"},
    "sweden": {"sweden"},
    "switzerland": {"switzerland"},
    "israel": {"israel"},
    "turkey": {"turkey", "türkiye"},
    "ukraine": {"ukraine"},
    "romania": {"romania"},
    "taiwan": {"taiwan"},
    "thailand": {"thailand"},
    "egypt": {"egypt"},
    "kenya": {"kenya"},
    "new zealand": {"new zealand"},
}

# US state codes + names → used to infer "united states" when no country token.
_US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}
_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
}


def _norm_country(name: str) -> str:
    """Map a free-form country string to a canonical key (or the lowered input)."""
    n = (name or "").strip().lower()
    for canon, aliases in _COUNTRY_ALIASES.items():
        if n == canon or n in {a.strip() for a in aliases}:
            return canon
    return n


def _detect_country(location: str) -> str | None:
    """Best-effort: return the canonical country named in a location string,
    or None when it can't be determined (e.g. bare 'Remote')."""
    loc = (location or "").strip().lower()
    if not loc:
        return None
    # Pad with spaces so the " us"/"us " aliases match on boundaries.
    padded = f" {loc} "
    # Explicit country tokens win.
    for canon, aliases in _COUNTRY_ALIASES.items():
        for a in aliases:
            a = a.strip()
            if not a:
                continue
            # Word-ish boundary: alias surrounded by non-alphanumerics.
            if re.search(rf"(?<![a-z]){re.escape(a)}(?![a-z])", padded):
                return canon
    # No country token — try US state inference from comma segments.
    segs = [s.strip() for s in loc.replace("/", ",").split(",") if s.strip()]
    for s in segs:
        if s in _US_STATES or s in _US_STATE_NAMES:
            return "united states"
    return None


def apply_location_filter(jobs: list[dict], prefs: dict[str, Any]) -> list[dict]:
    """Keep only jobs located in an allowed country/region (or allowed remote).

    prefs keys:
      allowed_countries: ["United States", ...]   (empty/None = no geo filter)
      allowed_regions:   ["California", "NY", ...] (optional; soft state filter)
      remote_scope:      "country" (default) | "worldwide"

    A job is kept when:
      - allowed_countries is empty (filter disabled), OR
      - remote_scope == "worldwide", OR
      - its detected country is allowed, OR
      - its country can't be determined (bare 'Remote'/blank) — kept to avoid
        dropping legitimately-region-targeted remote roles.
    A job is dropped when its detected country is clearly NOT allowed.
    """
    allowed_raw = prefs.get("allowed_countries") or []
    allowed = {_norm_country(c) for c in allowed_raw if str(c).strip()}
    remote_scope = str(prefs.get("remote_scope", "country")).strip().lower()

    if not allowed or remote_scope == "worldwide":
        return jobs

    allowed_regions = {str(r).strip().lower() for r in (prefs.get("allowed_regions") or []) if str(r).strip()}

    kept: list[dict] = []
    dropped_foreign = 0
    dropped_region = 0
    for j in jobs:
        country = _detect_country(j.get("location", ""))
        if country is not None and country not in allowed:
            dropped_foreign += 1
            log.debug(f"location filter: drop {j.get('title','')[:40]} @ {j.get('location','')!r} (country={country})")
            continue
        # Optional region narrowing (only when a region is detectable and the
        # job is not remote — never drop a remote role on region grounds).
        if allowed_regions and not j.get("is_remote"):
            loc = (j.get("location", "") or "").lower()
            segs = {s.strip() for s in loc.replace("/", ",").split(",") if s.strip()}
            region_tokens = segs & (_US_STATES | _US_STATE_NAMES)
            if region_tokens and not (region_tokens & allowed_regions):
                dropped_region += 1
                continue
        kept.append(j)

    if dropped_foreign or dropped_region:
        log.info(
            f"location filter: {len(jobs)} -> {len(kept)} "
            f"(dropped {dropped_foreign} foreign, {dropped_region} out-of-region; "
            f"allowed={sorted(allowed)}, remote_scope={remote_scope})"
        )
    return kept


def _matches_any(text, patterns) -> bool:
    """Defensive: coerce text to str. JobSpy can hand us NaN floats."""
    if not patterns:
        return False
    if text is None:
        return False
    try:
        text_lower = str(text).lower()
    except Exception:
        return False
    if not text_lower or text_lower == "nan":
        return False
    return any(str(p).lower() in text_lower for p in patterns if p)


def apply_rule_filters(jobs: list[dict], prefs: dict[str, Any], applied_ids: set[str]) -> list[dict]:
    """Cheap filters: dedupe, blacklist, whitelist override. No LLM.

    Side-effect: tag each kept job with `salary_meets_minimum` (bool|None)
    so the UI can flag low-salary postings without dropping them entirely.
    None = job didn't disclose salary (most common case)."""
    bl = prefs.get("blacklists") or {}
    bl_companies = bl.get("companies") or []
    bl_titles = bl.get("title_keywords") or []
    bl_descs = bl.get("description_keywords") or []
    bl_locations = bl.get("locations") or []
    good_words = prefs.get("good_words") or []
    min_salary = (prefs.get("salary") or {}).get("minimum_usd_annual") or 0

    kept: list[dict] = []
    for j in jobs:
        if j["job_id"] in applied_ids:
            continue
        whitelist_hit = (
            _matches_any(j.get("title", "") + " " + j.get("description", ""), good_words)
            if good_words else False
        )
        if not whitelist_hit:
            if _matches_any(j.get("company"), bl_companies):
                continue
            if _matches_any(j.get("title"), bl_titles):
                continue
            if _matches_any(j.get("description"), bl_descs):
                continue
            if _matches_any(j.get("location"), bl_locations):
                continue
        # Tag salary fitness for UI badges. JobSpy hands back salary in
        # whatever currency the listing used; we only judge USD/yr listings,
        # because comparing $/hr to a yearly minimum without context is wrong.
        if min_salary and j.get("min_salary"):
            try:
                listed = float(j.get("min_salary"))
                # Heuristic: anything < 1000 is probably hourly or unparsed
                if listed >= 1000:
                    j["salary_meets_minimum"] = listed >= min_salary
                else:
                    j["salary_meets_minimum"] = None
            except Exception:
                j["salary_meets_minimum"] = None
        else:
            j["salary_meets_minimum"] = None
        kept.append(j)
    log.info(f"rule filter: {len(jobs)} -> {len(kept)}")
    return kept


# ── Robust JSON parser (mirrors agents/profile.py for fit-score outputs) ─────

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.MULTILINE)


_ENTRY_RE = re.compile(
    r'\{\s*"id"\s*:\s*"?(?P<id>\d+)"?\s*,'
    r'\s*"score"\s*:\s*(?P<score>\d+)\s*,'
    r'\s*"reason"\s*:\s*"(?P<reason>(?:[^"\\]|\\.)*)"\s*\}',
    re.DOTALL,
)


def _salvage_entries(raw: str) -> list[dict]:
    """Pull every fully-formed {id, score, reason} object out of a possibly-
    truncated batch response. Used when Gemini's verbose reasons blow the
    token budget and the JSON gets cut off mid-array."""
    out = []
    for m in _ENTRY_RE.finditer(raw):
        try:
            out.append({
                "id": m.group("id"),
                "score": int(m.group("score")),
                "reason": m.group("reason"),
            })
        except Exception:
            continue
    return out


def _parse_json_loose(raw: str):
    if not raw:
        return None
    text = _FENCE.sub("", raw.strip())
    try:
        return json.loads(text)
    except Exception:
        pass
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        substr = text[first:last + 1]
        try:
            return json.loads(substr)
        except Exception:
            pass
        # Repair: escape unescaped newlines in strings + drop trailing commas
        out, in_str, esc = [], False, False
        for ch in substr:
            if esc:
                out.append(ch); esc = False; continue
            if ch == "\\":
                out.append(ch); esc = True; continue
            if ch == '"':
                in_str = not in_str
            elif in_str and ch == "\n":
                out.append("\\n"); continue
            elif in_str and ch == "\r":
                continue
            elif in_str and ch == "\t":
                out.append("\\t"); continue
            out.append(ch)
        repaired = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
        try:
            return json.loads(repaired)
        except Exception:
            pass
    return None


# ── 429 retry helper ─────────────────────────────────────────────────────────

def _call_with_retry(fn, *args, max_retries: int = 3, **kwargs):
    """Call fn; on rate-limit (429) errors, sleep the suggested delay and retry."""
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            is_429 = "429" in msg or "quota" in msg.lower() or "rate limit" in msg.lower()
            if not is_429 or attempt == max_retries:
                raise
            # Try to parse retry_delay seconds out of the error
            m = re.search(r"retry.{0,20}?(\d+(?:\.\d+)?)\s*s", msg, flags=re.I)
            if not m:
                m = re.search(r"seconds:\s*(\d+)", msg)
            wait = float(m.group(1)) if m else 30.0
            wait = min(wait + 1, 75)  # cap and add a buffer second
            log.warning(f"rate limit hit, sleeping {wait:.0f}s then retrying (attempt {attempt+1}/{max_retries})")
            time.sleep(wait)


# ── Batched fit scoring ──────────────────────────────────────────────────────

BATCH_SIZE = 25


def _score_batch(jobs: list[dict], resume_text: str) -> dict[str, tuple[int, str]]:
    """Score a batch of jobs in one LLM call. Returns {job_id: (score, reason)}.
    Uses simple sequential int ids in the prompt because LLMs reliably echo
    short ints, but truncate or rewrite long URL job_ids."""
    payload = [
        # id = position in batch (0..N-1). Reliable to echo back.
        {"id": str(i), "title": j["title"], "description": j.get("description", "")}
        for i, j in enumerate(jobs)
    ]
    user_prompt = batch_fit_score_user_prompt(resume_text, payload)

    raw = _call_with_retry(
        complete_cheap, BATCH_FIT_SYSTEM, user_prompt,
        max_tokens=3500, json_mode=True,    # was 1500, truncated mid-string for verbose Gemini reasons
    )
    parsed = _parse_json_loose(raw)
    out: dict[str, tuple[int, str]] = {}

    # Salvage: when Gemini truncates mid-string, the JSON is broken but the
    # COMPLETED entries are still in there. Pull them out via regex.
    if not parsed or "scores" not in parsed:
        salvaged = _salvage_entries(raw or "")
        if salvaged:
            log.info(f"  JSON truncated; salvaged {len(salvaged)} complete entries via regex")
            parsed = {"scores": salvaged}
        else:
            snippet = (raw or "")[:300].replace("\n", " ")
            log.warning(f"  batch returned no scores. raw[:300]={snippet!r}")
            return out
    # Map int-id -> real job_id
    for entry in parsed.get("scores") or []:
        idx_raw = entry.get("id")
        score = entry.get("score", 5)
        reason = entry.get("reason", "")
        try:
            score = max(1, min(10, int(score)))
        except Exception:
            score = 5
        # The id might be returned as int, str, or even "0", "1" etc.
        try:
            idx = int(str(idx_raw).strip())
        except Exception:
            continue
        if 0 <= idx < len(jobs):
            real_id = jobs[idx]["job_id"]
            out[real_id] = (score, str(reason)[:200])
    return out


def fit_score(job: dict, resume_text: str) -> tuple[int, str]:
    """Single-job fit score (fallback when batch fails). Robust JSON parse + retry."""
    try:
        user_prompt = fit_score_user_prompt(resume_text, job["title"], job.get("description", ""))
        raw = _call_with_retry(
            complete_cheap, FIT_SCORE_SYSTEM, user_prompt,
            max_tokens=1500, json_mode=True,
        )
        parsed = _parse_json_loose(raw)
        if not parsed:
            return 5, "scoring failed (unparseable JSON)"
        score = max(1, min(10, int(parsed.get("score", 5))))
        return score, str(parsed.get("reason", ""))[:200]
    except Exception as e:
        log.warning(f"fit_score failed for {job.get('title')}: {e}")
        return 5, f"scoring failed: {str(e)[:80]}"


def score_and_filter(
    jobs: list[dict], resume_text: str, prefs: dict[str, Any],
    on_all_scored=None,
) -> list[dict]:
    """Batch-score all jobs and filter by min_score. Sorted desc by fit_score.

    on_all_scored: optional callback that receives the FULL scored list
    (including below-threshold) so the orchestrator can persist a snapshot
    for the Discovered tab.
    """
    fs = prefs.get("fit_score") or {}
    if not fs.get("enabled", False):
        log.info("fit scoring disabled - keeping all jobs")
        for j in jobs:
            j["fit_score"] = None
            j["fit_reason"] = ""
        if on_all_scored:
            on_all_scored(list(jobs))
        return jobs

    min_score = int(fs.get("min_score", 6))
    log.info(f"fit scoring {len(jobs)} jobs in batches of {BATCH_SIZE}")

    # Phase 4: score batches in parallel across a small pool (SCORE_WORKERS,
    # default 3) so a large discovery doesn't block serially on the LLM.
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed
    chunks = [jobs[i:i + BATCH_SIZE] for i in range(0, len(jobs), BATCH_SIZE)]
    workers = max(1, int(os.environ.get("SCORE_WORKERS", "3") or 3))

    def _score_chunk(idx_chunk):
        idx, chunk = idx_chunk
        try:
            results = _score_batch(chunk, resume_text)
            log.info(f"  batch {idx + 1}/{len(chunks)}: scored {len(results)}/{len(chunk)}")
            return results
        except Exception as e:
            log.warning(f"  batch {idx + 1} failed: {e}; falling back to per-job")
            out: dict[str, tuple[int, str]] = {}
            for j in chunk:
                score, reason = fit_score(j, resume_text)
                out[j["job_id"]] = (score, reason)
            return out

    by_id: dict[str, tuple[int, str]] = {}
    if len(chunks) <= 1 or workers == 1:
        for ic in enumerate(chunks):
            by_id.update(_score_chunk(ic))
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as pool:
            for fut in as_completed([pool.submit(_score_chunk, ic) for ic in enumerate(chunks)]):
                by_id.update(fut.result())

    # Annotate every job with its score (even below-threshold ones)
    for j in jobs:
        score, reason = by_id.get(j["job_id"], (None, "not scored"))
        j["fit_score"] = score
        j["fit_reason"] = reason

    # Snapshot the full scored list before filtering
    full_sorted = sorted(jobs, key=lambda j: (j.get("fit_score") or 0), reverse=True)
    if on_all_scored:
        try:
            on_all_scored(full_sorted)
        except Exception as e:
            log.debug(f"on_all_scored callback failed: {e}")

    # Filter to above-threshold for the actual application loop.
    # ADAPTIVE: if zero jobs match the user's threshold, relax it by 1
    # point at a time (down to 4) so they always get *something* to review
    # instead of an empty results screen. This is the most common reason
    # a fresh user sees zero matches: their threshold is set too high
    # for the search terms they're using.
    effective_min = min_score
    kept = [j for j in full_sorted if (j.get("fit_score") or 0) >= effective_min]
    while not kept and effective_min > 4:
        effective_min -= 1
        kept = [j for j in full_sorted if (j.get("fit_score") or 0) >= effective_min]
    if effective_min != min_score:
        log.warning(
            f"  no jobs at min_score={min_score}; auto-relaxed to {effective_min} "
            f"and found {len(kept)}. Consider widening search terms or lowering threshold."
        )
        for j in kept:
            j["fit_threshold_relaxed_to"] = effective_min
    for j in kept:
        log.info(f"  ok {j['fit_score']}/10 - {j['title'][:50]} @ {j['company'][:30]}")
    log.info(f"fit filter: {len(jobs)} -> {len(kept)} (min_score={min_score}, effective={effective_min})")
    return kept
