"""
core/copilot.py - AI Application Copilot answer engine

Pipeline for a single question:
  1. classify_question(text) → yes_no | short_text | long_text | numeric
     (cheap LLM; in-process dict cache to avoid re-classifying same text)
  2. qa_memory.search()  →  look for a prior answer
       sim >= 0.90 AND approved  → source = "cached"   (no LLM call)
       0.75 ≤ sim < 0.90        → source = "adapted"  (LLM adapts cached answer)
       sim < 0.75               → source = "generated" (fresh LLM call)
  3. _composite_confidence() → 0-100 score
       Components:
         cache_sim    * 100  × 0.30   (how well the cache matched)
         type_det     (0-100) × 0.20  (yes_no / numeric are more deterministic)
         llm_self_conf (0-100) × 0.50  (LLM's own calibrated confidence)
       Reduced to ≤ 35 and needs_review forced True when _guardrail fires.
  4. Guardrail: year-claim check (post-generation) — flags fabricated numbers.

Public functions (called by agents/copilot.py and core/copilot.py internally):
  classify_question(text) -> str
  answer_question(question, job, profile, store, *, force_llm, instructions) -> dict
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from core.qa_memory import QAStore, _norm

log = logging.getLogger(__name__)

# In-process cache for classification results (question_norm → type)
_classify_cache: dict[str, str] = {}

# Type-determinism bonus (higher = more predictable answer)
_TYPE_DET = {"yes_no": 100, "numeric": 85, "short_text": 65, "long_text": 45}

_VALID_TYPES = {"yes_no", "short_text", "long_text", "numeric"}


# ── Question classifier ───────────────────────────────────────────────────────

def classify_question(text: str) -> str:
    """Classify one question. Cached per normalised text; uses cheap LLM."""
    key = _norm(text)[:120]
    if key in _classify_cache:
        return _classify_cache[key]

    # Heuristic fast-path (no LLM needed for obvious patterns)
    lower = text.lower().strip()
    if any(p in lower for p in (
        "are you ", "do you ", "have you ", "will you ", "can you ", "is your ",
        "authorized", "sponsor", "relocate", "clearance", "background check",
    )):
        # Questions that start with yes/no markers
        # But only short ones — longer descriptive questions get LLM classification
        if len(text.split()) < 20:
            _classify_cache[key] = "yes_no"
            return "yes_no"

    if any(p in lower for p in ("how many years", "how many months", "how much", "salary", "rate")):
        _classify_cache[key] = "numeric"
        return "numeric"

    # LLM classification for ambiguous cases
    try:
        from llm.client import complete_cheap
        from llm.prompts import CLASSIFY_SYSTEM
        raw = complete_cheap(CLASSIFY_SYSTEM, text, max_tokens=8).strip().lower()
        q_type = raw if raw in _VALID_TYPES else "short_text"
    except Exception:
        q_type = "short_text"

    _classify_cache[key] = q_type
    return q_type


# ── Confidence scoring ────────────────────────────────────────────────────────

def _composite_confidence(
    source: str,
    cache_sim: float,
    q_type: str,
    llm_self_conf: int,
) -> int:
    """
    Composite confidence 0-100:
      cache_sim * 100 × 0.30 + type_det × 0.20 + llm_self_conf × 0.50

    For "cached" source, llm_self_conf is replaced by a perfect 100 from
    the cache (we trust an approved cached answer fully).
    """
    type_det = _TYPE_DET.get(q_type, 60)
    if source == "cached":
        score = round(cache_sim * 100 * 0.5 + type_det * 0.5)
    elif source == "adapted":
        score = round(llm_self_conf * 0.6 + cache_sim * 100 * 0.2 + type_det * 0.2)
    else:   # generated
        score = round(llm_self_conf * 0.7 + type_det * 0.3)
    return max(0, min(100, score))


# ── Guardrail ─────────────────────────────────────────────────────────────────

def _guardrail(answer: str, profile_text: str) -> bool:
    """
    Return True (flag for review) if the answer makes specific numeric claims
    that are NOT present in the candidate's profile text.
    E.g. "I have 7 years of Python" when the resume only says "5 years".
    """
    year_claims = re.findall(r"(\d+)\s+year", answer.lower())
    profile_lower = profile_text.lower()
    for yr in year_claims:
        if yr not in profile_lower:
            log.debug(f"copilot guardrail: answer claims '{yr} year' but not found in profile")
            return True
    return False


# ── Profile text builder ──────────────────────────────────────────────────────

def build_profile_text(profile) -> str:
    """Format a UserProfile into a concise text block for the LLM system prompt."""
    p = profile
    lines = [
        f"Name:               {getattr(p, 'full_name', '') or ''}",
        f"Location:           {p.city}, {p.state}, {p.country}",
        f"Email:              {p.email}",
        f"Phone:              {p.phone}",
        f"LinkedIn:           {p.linkedin_url}",
        f"GitHub:             {p.github_url}",
        f"Website/Portfolio:  {p.website_url}",
        "",
        f"Years of experience:  {p.years_of_experience}",
        f"Desired salary USD:   {p.desired_salary_usd:,}" if p.desired_salary_usd else "Desired salary: not specified",
        f"Notice period:        {p.notice_period_days} days",
        f"US work auth:         {p.auth_us}",
        f"Requires sponsorship: {p.requires_sponsorship}",
        f"Willing to relocate:  {getattr(p, 'raw_questions', {}).get('willing_to_relocate', 'not specified')}",
        f"Background check OK:  {getattr(p, 'raw_questions', {}).get('background_check', 'not specified')}",
        "",
        f"Summary: {p.user_information_summary or p.summary}",
    ]
    resume = (getattr(p, "master_resume_text", "") or "").strip()
    if resume:
        lines.append(f"\n=== MASTER RESUME (source of truth) ===\n{resume[:4000]}")
    return "\n".join(lines)


# ── JSON parse helper ─────────────────────────────────────────────────────────

def _parse_copilot_json(raw: str) -> dict:
    """Loose JSON parse with fence stripping."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.MULTILINE)
    try:
        return json.loads(text)
    except Exception:
        first, last = text.find("{"), text.rfind("}")
        if first != -1 and last > first:
            try:
                return json.loads(text[first:last + 1])
            except Exception:
                pass
    return {}


# ── Main answer function ──────────────────────────────────────────────────────

def answer_question(
    question: str,
    job: dict,
    profile,
    store: QAStore,
    *,
    force_llm: bool = False,
    instructions: str = "",
) -> dict:
    """
    Full copilot pipeline for one question.
    Returns:
        {answer, confidence_score, answer_type, source, needs_review}
    """
    from llm.client import complete_cheap
    from llm.prompts import COPILOT_SYSTEM, copilot_user_prompt

    q_type = classify_question(question)
    profile_text = build_profile_text(profile)
    job_desc = (
        job.get("enriched_description")
        or job.get("raw_description")
        or job.get("description", "")
    )

    # ── Cache lookup ──────────────────────────────────────────────────────────
    cached_rec, cache_sim, source = None, 0.0, "generated"
    cached_answer_text: str | None = None

    if not force_llm:
        results = store.search(question, top_k=1)
        if results:
            cached_rec, cache_sim = results[0]
            if cache_sim >= 0.90 and cached_rec.get("approved"):
                source = "cached"
                cached_answer_text = cached_rec["answer"]
            elif cache_sim >= 0.75:
                source = "adapted"
                cached_answer_text = cached_rec["answer"]

    # ── LLM call ─────────────────────────────────────────────────────────────
    if source in ("generated", "adapted"):
        try:
            raw = complete_cheap(
                COPILOT_SYSTEM,
                copilot_user_prompt(
                    profile=profile_text,
                    job_title=job.get("title", ""),
                    company=job.get("company", ""),
                    job_description=job_desc,
                    question=question,
                    question_type=q_type,
                    cached_answer=cached_answer_text,
                    instructions=instructions,
                ),
                max_tokens=500,
                json_mode=True,
            )
            data = _parse_copilot_json(raw)
        except Exception as e:
            log.warning(f"copilot LLM call failed: {e}")
            data = {
                "answer": cached_answer_text or "Unable to generate an answer — please fill manually.",
                "self_confidence": 15,
                "needs_review": True,
                "reasoning": f"LLM unavailable: {e}",
            }
        llm_self_conf = max(0, min(100, int(data.get("self_confidence") or 50)))
        answer_text   = str(data.get("answer") or "").strip()
        needs_review  = bool(data.get("needs_review", False))
    else:
        # Pure cache hit — no LLM call
        answer_text   = cached_answer_text or ""
        llm_self_conf = int(cache_sim * 100)
        needs_review  = False

    # ── Confidence + guardrail ────────────────────────────────────────────────
    confidence = _composite_confidence(source, cache_sim, q_type, llm_self_conf)

    if _guardrail(answer_text, profile_text):
        needs_review = True
        confidence   = min(confidence, 35)

    if confidence < 40:
        needs_review = True

    # ── Humanize the answer text (deterministic clean; skip LLM polish) ──────
    try:
        from core.text_clean import deliver as _deliver
        result = _deliver(answer_text, kind="answer", facts=profile_text, skip_polish=True)
        answer_text = result["text"]
        if not result["report"]["ok"]:
            needs_review = True
            confidence   = min(confidence, 35)
            log.debug(f"copilot text_clean issues: {result['report']['issues']}")
    except Exception as _e:
        log.debug(f"copilot text_clean skipped: {_e}")

    return {
        "answer":           answer_text,
        "confidence_score": confidence,
        "answer_type":      q_type,
        "source":           source,
        "needs_review":     needs_review,
    }
